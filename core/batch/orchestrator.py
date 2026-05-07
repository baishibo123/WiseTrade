"""
BatchRunner: ProcessPoolExecutor-based orchestrator for batch backtests.

Responsibilities:
- Set up the batch output directory layout
- Spin up cross-process logging (QueueHandler/QueueListener — ADR-012)
- Submit tasks, drain results via as_completed, print progress
- Persist a derived manifest by scanning runs/ at end (ADR-006)
- Handle Ctrl+C gracefully: in-flight workers finish, manifest still gets built
"""

from __future__ import annotations

import json
import logging
import multiprocessing as mp
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from logging.handlers import QueueListener
from pathlib import Path
from typing import Optional

from core.batch.atomic_io import atomic_write_json
from core.batch.types import BatchTask, RunResult
from core.batch.worker import run_one, worker_init


class BatchRunner:
    def __init__(
        self,
        batch_dir: Path,
        n_workers: Optional[int] = None,
    ):
        self.batch_dir = Path(batch_dir)
        self.n_workers = n_workers if n_workers is not None else max(1, (os.cpu_count() or 2) - 1)

    def run(self, tasks: list[BatchTask], resume: bool = False) -> Path:
        """
        Execute tasks and return the batch directory path.

        resume=False (default): pre-existing run JSON files in this batch_dir
        will still be skipped by the worker (the file's existence is the commit
        signal). To force a full re-run, point at a fresh batch_dir.

        resume=True: same behavior. The flag exists to make user intent
        explicit at the call site; worker semantics are identical.
        """
        self._setup_layout()

        log_queue, listener = self._start_logging()
        logging.info(
            f"BatchRunner starting: {len(tasks)} tasks, {self.n_workers} workers, "
            f"batch_dir={self.batch_dir}"
        )

        try:
            self._execute(tasks, log_queue)
        except KeyboardInterrupt:
            logging.warning("Interrupted — in-flight tasks finishing, then writing manifest.")
        finally:
            listener.stop()

        manifest_path = self._build_manifest()
        print(f"\nBatch complete. Manifest: {manifest_path}")
        return self.batch_dir

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _setup_layout(self) -> None:
        (self.batch_dir / "runs").mkdir(parents=True, exist_ok=True)
        (self.batch_dir / "curves").mkdir(parents=True, exist_ok=True)

    def _start_logging(self):
        """
        Multi-process logging via QueueListener (ADR-012).

        Returns (log_queue, listener). Caller must call listener.stop().
        """
        manager = mp.Manager()
        log_queue = manager.Queue()

        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

        console = logging.StreamHandler(sys.stdout)
        console.setLevel(logging.INFO)
        console.setFormatter(fmt)

        error_log = logging.FileHandler(self.batch_dir / "errors.log", mode="a", encoding="utf-8")
        error_log.setLevel(logging.WARNING)
        error_log.setFormatter(fmt)

        listener = QueueListener(log_queue, console, error_log, respect_handler_level=True)
        listener.start()

        # Main process logging also goes through this listener so its output
        # interleaves cleanly with worker logs.
        root = logging.getLogger()
        root.handlers.clear()
        from logging.handlers import QueueHandler
        root.addHandler(QueueHandler(log_queue))
        root.setLevel(logging.INFO)

        return log_queue, listener

    def _execute(self, tasks: list[BatchTask], log_queue) -> None:
        total = len(tasks)
        done = ok = errors = skipped = crashed = 0
        start = time.time()

        with ProcessPoolExecutor(
            max_workers=self.n_workers,
            initializer=worker_init,
            initargs=(log_queue,),
        ) as ex:
            future_to_task = {ex.submit(run_one, task): task for task in tasks}

            for fut in as_completed(future_to_task):
                task = future_to_task[fut]
                done += 1
                try:
                    result: RunResult = fut.result()
                except Exception as exc:
                    crashed += 1
                    logging.error(
                        f"[{done}/{total}] {task.run_id} {task.strategy_name} CRASHED: {exc!r}"
                    )
                    continue

                if result.status == "ok":
                    ok += 1
                    metrics_str = self._format_metrics(result.metrics)
                    print(
                        f"[{done}/{total}] {task.strategy_name} ok ({result.duration_seconds:.1f}s) {metrics_str}"
                    )
                elif result.status == "error":
                    errors += 1
                    last_line = (result.error or "").strip().splitlines()[-1] if result.error else "?"
                    print(f"[{done}/{total}] {task.strategy_name} ERROR: {last_line}")
                elif result.status == "skipped":
                    skipped += 1
                    print(f"[{done}/{total}] {task.strategy_name} skipped (already done)")

        elapsed = time.time() - start
        logging.info(
            f"Done: {ok} ok, {errors} error, {skipped} skipped, {crashed} crashed in {elapsed:.1f}s"
        )

    def _build_manifest(self) -> Path:
        """Scan runs/*.json and build manifest.json (ADR-006: derived index)."""
        runs_dir = self.batch_dir / "runs"
        summaries = []
        for run_file in sorted(runs_dir.glob("*.json")):
            try:
                data = json.loads(run_file.read_text())
            except Exception as e:
                logging.warning(f"Could not read {run_file.name}: {e}")
                continue
            summaries.append({
                "run_id": data.get("run_id"),
                "strategy_name": data.get("strategy_name"),
                "strategy_version": data.get("strategy_version"),
                "params": data.get("params"),
                "universe": data.get("universe"),
                "status": data.get("status"),
                "metrics": data.get("metrics"),
                "curve_path": data.get("curve_path"),
                "duration_seconds": data.get("duration_seconds"),
                "error": data.get("error"),
            })

        manifest = {
            "batch_dir": str(self.batch_dir),
            "built_at": datetime.utcnow().isoformat() + "Z",
            "n_runs": len(summaries),
            "n_ok": sum(1 for r in summaries if r["status"] == "ok"),
            "n_error": sum(1 for r in summaries if r["status"] == "error"),
            "n_skipped": sum(1 for r in summaries if r["status"] == "skipped"),
            "runs": summaries,
        }
        manifest_path = self.batch_dir / "manifest.json"
        atomic_write_json(manifest_path, manifest)
        return manifest_path

    @staticmethod
    def _format_metrics(metrics: Optional[dict]) -> str:
        if not metrics:
            return ""
        ret = metrics.get("total_return_pct")
        sharpe = metrics.get("sharpe")
        trades = metrics.get("num_trades")
        parts = []
        if ret is not None:
            parts.append(f"ret={ret:+.2f}%")
        if sharpe is not None:
            parts.append(f"sharpe={sharpe:.2f}")
        if trades is not None:
            parts.append(f"trades={trades}")
        return " | ".join(parts)


def make_batch_dir(results_root: Path, batch_name: str) -> Path:
    """Compose a timestamped batch directory under results/."""
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    safe_name = batch_name.replace(" ", "_").replace("/", "_")
    return Path(results_root) / f"{safe_name}_{stamp}"
