"""
BatchRunner: ProcessPoolExecutor-based orchestrator for batch backtests.

Responsibilities:
- Set up the batch output directory layout
- Spin up cross-process logging (QueueHandler/QueueListener — ADR-012)
- Submit tasks, drain results in submission order, print progress
- Persist a derived manifest by scanning runs/ at end (ADR-006)
- Handle Ctrl+C: queued tasks are cancelled, running ones finish, manifest built
"""

from __future__ import annotations

import json
import logging
import multiprocessing as mp
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from logging.handlers import QueueHandler, QueueListener
from pathlib import Path
from typing import Optional

from core.batch.atomic_io import atomic_write_json
from core.batch.types import BatchTask, RunResult
from core.batch.worker import run_one, worker_init


class TqdmLoggingHandler(logging.Handler):
    """
    Emit log records via tqdm.write() so they scroll above the progress bar
    rather than tearing it (F2).

    Two streams share one terminal and have different lifetimes. Progress is
    ephemeral state — only the newest value matters, so it is pinned and
    overwritten in place. Log records are an append-only record — every one
    matters, so they scroll and are never overwritten. A plain StreamHandler
    plus a live bar puts two uncoordinated writers on one file descriptor,
    which is how a warning ends up spliced into a half-redrawn bar. tqdm.write()
    takes tqdm's own lock, clears the bar, writes the line, and redraws, so the
    two coexist.
    """

    def emit(self, record):
        # Everything, including the import, is inside the try. This handler runs
        # on the QueueListener thread, and an exception escaping emit() kills
        # that thread -- which silently decapitates logging for the rest of the
        # batch. Falling back to a plain stderr write means a broken or missing
        # tqdm degrades the display instead of the run.
        try:
            from tqdm import tqdm  # local: keeps `import core.batch` tqdm-free
            tqdm.write(self.format(record), file=sys.stderr)
        except Exception:
            try:
                print(self.format(record), file=sys.stderr)
            except Exception:
                self.handleError(record)


class BatchRunner:
    def __init__(
        self,
        batch_dir: Path,
        n_workers: Optional[int] = None,
        start_method: Optional[str] = None,
    ):
        self.batch_dir = Path(batch_dir)
        self.n_workers = n_workers if n_workers is not None else max(1, (os.cpu_count() or 2) - 1)

        # Pin the start method (ADR-015). An explicit context rather than
        # mp.set_start_method(force=True): the choice stays local, so no global
        # interpreter state is mutated, nothing else in the process is affected,
        # and no other caller's choice is silently overridden. Pinning it at all
        # means a fork-vs-spawn bug can never present as an unexplained
        # difference between the Windows box and the Mac.
        self._mp_ctx = self._resolve_mp_context(start_method)

    @staticmethod
    def _resolve_mp_context(requested: Optional[str]):
        """
        Resolve the worker start method to a concrete multiprocessing context.

        Falls back to spawn (with a warning) when the requested method is not
        available on this platform, so a config value carried over from the
        other machine degrades instead of raising.
        """
        if requested is None:
            from config import MP_START_METHOD
            requested = MP_START_METHOD

        available = mp.get_all_start_methods()
        if requested not in available:
            logging.warning(
                f"start method {requested!r} is unavailable on this platform "
                f"(have: {available}); falling back to 'spawn'."
            )
            requested = "spawn"

        if requested == "fork":
            # Not a neutral alternative here: _start_logging() starts the
            # QueueListener thread before _execute() builds the pool, so the
            # parent is multi-threaded at fork time. Python 3.12 warns that this
            # risks deadlock. Exposed for the ADR-015 measurement, not for
            # routine use.
            logging.warning(
                "start method 'fork' selected (experimental, ADR-015). The parent "
                "is multi-threaded when workers are created, which risks deadlock; "
                "use it to measure startup cost, not for production batches."
            )

        return mp.get_context(requested)

    def run(self, tasks: list[BatchTask], overwrite: bool = False) -> Path:
        """
        Execute tasks and return the batch directory path.

        Default (ADR-021): only tasks with no committed result are submitted.
        A task's runs/<run_id>.json existing is the commit signal (ADR-006), so
        reusing it is not a mode — it is what running a batch means.

        overwrite=True: human override. Everything is submitted and any existing
        result is discarded and recomputed. This is for what the hash cannot see
        — a re-adjusted bar database, an edited shared indicator — so it is a
        deliberate, destructive order and is logged at WARNING to leave a trace
        in errors.log. It is deliberately *not* how the program expresses its own
        staleness findings; that is per-task and belongs to ADR-022.

        The filter runs here in the parent rather than inside the worker. A task
        dropped here costs nothing; a task that reaches a worker only to discover
        its own result already exists has already paid pickling, IPC and process
        startup. Keeping the decision here is also what lets the flag mean
        anything at all: the worker never sees it.
        """
        self._setup_layout()

        log_queue, listener, manager, saved_logging = self._start_logging()

        pending = self._filter_committed(self._dedupe(tasks), overwrite)
        logging.info(
            f"BatchRunner starting: {len(pending)} tasks, {self.n_workers} workers, "
            f"batch_dir={self.batch_dir}"
        )

        manifest_path = None
        try:
            try:
                self._execute(pending, log_queue)
            except KeyboardInterrupt:
                logging.warning("Interrupted — writing a manifest for what completed.")

            # Must run INSIDE this try and BEFORE listener.stop(). _build_manifest
            # logs a warning for every unreadable run JSON, and the root logger's
            # only handler is a QueueHandler feeding the listener thread. Stopping
            # the listener first would drop those records into a queue nobody
            # reads, so a truncated run file would silently vanish from the
            # manifest -- n_runs would read 197 against 200 files on disk, with no
            # trace on the console or in errors.log.
            manifest_path = self._build_manifest()
        finally:
            # Order matters. listener.stop() drains the queue and joins the
            # listener thread; manager.shutdown() then terminates the process
            # hosting that queue. Reversed, the listener would be reading from a
            # queue whose backing process is gone -- a hang or BrokenPipeError at
            # interpreter exit.
            listener.stop()
            manager.shutdown()
            self._restore_logging(saved_logging)

        # The batch's result goes to stdout; progress and logs went to stderr.
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

        Returns (log_queue, listener, manager, saved_logging). The caller owns
        teardown and must, in this order: stop the listener, shut down the
        manager, restore logging. See run().
        """
        # A Manager queue rather than mp.Queue(): ProcessPoolExecutor passes
        # initargs by pickling, and a raw mp.Queue only survives inheritance
        # across fork. A manager queue is a proxy and pickles, which is what
        # makes it work under spawn. The cost is a manager process that must be
        # shut down explicitly -- previously it was leaked once per batch.
        manager = self._mp_ctx.Manager()
        log_queue = manager.Queue()

        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

        # stderr, not stdout: stdout carries only the batch's result (the
        # manifest path), so `python run_ranking.py > out.txt` keeps the result
        # and lets progress and logs stay on the terminal.
        console = TqdmLoggingHandler()
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

        # Snapshot before mutating. Without restoring this, BatchRunner
        # permanently breaks logging for everything that runs after it: the
        # handlers are cleared here, and once the listener is stopped the
        # QueueHandler installed below feeds a dead queue -- so every later
        # logging call anywhere in the process is silently swallowed.
        saved_logging = (root.handlers[:], root.level)

        root.handlers.clear()
        root.addHandler(QueueHandler(log_queue))
        root.setLevel(logging.INFO)

        return log_queue, listener, manager, saved_logging

    @staticmethod
    def _restore_logging(saved) -> None:
        """Undo _start_logging's mutation of the process-wide root logger."""
        handlers, level = saved
        root = logging.getLogger()
        root.handlers.clear()
        for handler in handlers:
            root.addHandler(handler)
        root.setLevel(level)

    @staticmethod
    def _dedupe(tasks: list[BatchTask]) -> list[BatchTask]:
        """
        Collapse tasks sharing a run_id. Two such tasks are the same unit of
        work, and submitting both races two workers onto one result JSON and
        one curves/<run_id>/portfolio.parquet.tmp -- the committed Parquet can
        end up an interleaving of two writers.

        Reachable without doing anything unusual: _expand_params unions
        param_grid with param_list without deduplicating (a documented,
        supported combination), and the same strategy can be listed twice.
        """
        seen: dict[str, BatchTask] = {}
        for task in tasks:
            seen.setdefault(task.run_id, task)
        if len(seen) != len(tasks):
            logging.warning(
                f"{len(tasks) - len(seen)} duplicate task(s) collapsed: identical "
                f"run_id means identical work, and running them concurrently would "
                f"race on the same output files."
            )
        return list(seen.values())

    def _filter_committed(self, tasks: list[BatchTask], overwrite: bool) -> list[BatchTask]:
        """
        Return the tasks that should actually be submitted (ADR-021).

        Reports the split before any work starts, so the count is known up front
        instead of being discovered one progress line at a time.
        """
        runs_dir = self.batch_dir / "runs"
        committed, remaining = [], []
        for task in tasks:
            bucket = committed if (runs_dir / f"{task.run_id}.json").exists() else remaining
            bucket.append(task)

        if overwrite:
            if committed:
                # WARNING, not INFO: this destroys committed results, and the
                # errors.log handler is set to WARNING, so the act of discarding
                # them leaves a durable record rather than only a console line.
                logging.warning(
                    f"overwrite=True: discarding and recomputing {len(committed)} of "
                    f"{len(tasks)} already-committed results."
                )
            return list(tasks)

        if committed:
            logging.info(
                f"{len(committed)} of {len(tasks)} already committed, "
                f"submitting {len(remaining)}."
            )
        return remaining

    def _execute(self, tasks: list[BatchTask], log_queue) -> None:
        # Imported before anything is submitted. Raised after submission, an
        # ImportError here would still wait for every task to finish (the pool's
        # __exit__ drains), then propagate past run()'s KeyboardInterrupt-only
        # guard and skip the manifest -- discarding the index for a whole batch
        # that had already been computed. Here it costs milliseconds.
        from tqdm import tqdm  # local: keeps `import core.batch` tqdm-free

        total = len(tasks)
        done = ok = errors = crashed = 0
        start = time.time()

        with ProcessPoolExecutor(
            max_workers=self.n_workers,
            mp_context=self._mp_ctx,
            initializer=worker_init,
            initargs=(log_queue,),
        ) as ex:
            # Every task is submitted up front, so the pool stays saturated no
            # matter what order results are read in. Iterating the submission
            # list instead of as_completed therefore costs no throughput --
            # fut.result() blocks on task i while i+1..n keep running in their
            # own processes -- and buys a meaningful [n/total]: n is the nth
            # task the config enumerated, so a progress line maps back to a
            # specific (strategy, params, symbol). Under as_completed the
            # counter named no task at all.
            #
            # The one cost is head-of-line blocking in *reporting*: a slow
            # first task delays the lines for tasks that already finished.
            # Failures are unaffected -- run_one logs the traceback from inside
            # the worker before returning, so errors still reach the console
            # and errors.log in real time regardless of this ordering.
            submitted = [(task, ex.submit(run_one, task)) for task in tasks]

            # One aggregate bar, not one per worker. Per-task lines go through
            # logging rather than print(), so every character of text output has
            # a single writer -- the listener thread calling tqdm.write() -- and
            # main-process narration cannot race worker logs onto the same fd.
            with tqdm(
                total=total,
                desc=self.batch_dir.name,
                unit="run",
                file=sys.stderr,
                leave=True,
                dynamic_ncols=True,
            ) as pbar:
                try:
                    for task, fut in submitted:
                        done += 1
                        try:
                            result: RunResult = fut.result()
                        except Exception as exc:
                            crashed += 1
                            logging.error(
                                f"[{done}/{total}] {task.run_id} {task.strategy_name} CRASHED: {exc!r}"
                            )
                        else:
                            if result.status == "ok":
                                ok += 1
                                logging.info(
                                    f"[{done}/{total}] {task.strategy_name} ok "
                                    f"({result.duration_seconds:.1f}s) {self._format_metrics(result.metrics)}"
                                )
                            elif result.status == "error":
                                errors += 1
                                last = (result.error or "").strip().splitlines()[-1] if result.error else "?"
                                # Overlaps the worker's own ERROR line by design:
                                # that one is keyed by run_id, this by position.
                                logging.info(f"[{done}/{total}] {task.strategy_name} ERROR: {last}")

                        # try/except/else rather than `continue`, so these two
                        # always run -- a crashed future previously skipped the
                        # bar update and desynced it from reality.
                        pbar.update(1)
                        pbar.set_postfix(ok=ok, err=errors, crashed=crashed, refresh=False)

                except KeyboardInterrupt:
                    # Cancel queued work explicitly. Without this, leaving the
                    # `with` calls shutdown(wait=True) with cancel_futures
                    # defaulting to False, so every not-yet-started task still
                    # runs to completion -- Ctrl+C on a 200-task batch blocked
                    # for hours before run()'s handler was ever reached.
                    # fut.cancel() returns False for tasks already executing, so
                    # the count is an exact split between queued and running.
                    cancelled = sum(1 for _, f in submitted if f.cancel())
                    logging.warning(
                        f"Interrupted after {done}/{total}: cancelled {cancelled} queued "
                        f"task(s), waiting for {max(0, total - done - cancelled)} still "
                        f"running. Results already committed are kept."
                    )
                    raise

        elapsed = time.time() - start
        logging.info(
            f"Done: {ok} ok, {errors} error, {crashed} crashed in {elapsed:.1f}s"
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
            "built_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "n_runs": len(summaries),
            "n_ok": sum(1 for r in summaries if r["status"] == "ok"),
            "n_error": sum(1 for r in summaries if r["status"] == "error"),
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
        trades = metrics.get("num_episodes")
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
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_name = batch_name.replace(" ", "_").replace("/", "_")
    return Path(results_root) / f"{safe_name}_{stamp}"
