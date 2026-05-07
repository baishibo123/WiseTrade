"""
Worker function for batch backtesting.

Contract (ADR-003): never raises. Always returns a RunResult, even on error.
Process-level crashes (OOM kill, segfault) are the only thing that bypasses
this — surfaced by ProcessPoolExecutor as BrokenProcessPool / future failure.

Commit ordering (ADR-006): curve files are written first, result JSON last.
Result JSON existence is the run-level commit signal.
"""

from __future__ import annotations

import logging
import time
import traceback
from logging.handlers import QueueHandler
from pathlib import Path
from typing import Any

from core.batch.atomic_io import atomic_write_json
from core.batch.recorder import ParquetEquityRecorder
from core.batch.types import BatchTask, RunResult


def worker_init(log_queue) -> None:
    """
    Per-worker process initializer (passed to ProcessPoolExecutor).

    Routes all logging records in this worker through a QueueHandler so the
    main process can serialize them to stdout + errors.log (ADR-012).
    """
    handler = QueueHandler(log_queue)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def run_one(task: BatchTask) -> RunResult:
    """
    Execute one backtest task. Never raises — all failures become RunResult(status="error").
    """
    start = time.time()

    batch_dir = Path(task.batch_dir)
    result_path = batch_dir / "runs" / f"{task.run_id}.json"

    if result_path.exists():
        return RunResult(
            run_id=task.run_id,
            strategy_name=task.strategy_name,
            strategy_version=task.strategy_version,
            params=task.params,
            universe=task.universe,
            start_datetime=task.start_datetime,
            end_datetime=task.end_datetime,
            status="skipped",
            duration_seconds=0.0,
        )

    try:
        from core.engine import Engine

        engine = Engine(
            universe=task.universe,
            strategy_class=task.strategy_class,
            start_datetime=task.start_datetime,
            end_datetime=task.end_datetime,
            strategy_params=task.params,
            portfolio_config=task.portfolio_config,
        )
        analyzer = engine.run()

        curve_relative: str | None = None
        if task.save_curves:
            curve_abs = batch_dir / "curves" / task.run_id / "portfolio.parquet"
            recorder = ParquetEquityRecorder(curve_abs)
            recorder.write_equity_history(analyzer.portfolio._equity_history)
            curve_relative = curve_abs.relative_to(batch_dir).as_posix()

        result = RunResult(
            run_id=task.run_id,
            strategy_name=task.strategy_name,
            strategy_version=task.strategy_version,
            params=task.params,
            universe=task.universe,
            start_datetime=task.start_datetime,
            end_datetime=task.end_datetime,
            status="ok",
            metrics=_sanitize_metrics(analyzer.metrics),
            duration_seconds=round(time.time() - start, 3),
            curve_path=curve_relative,
        )

    except Exception:
        result = RunResult(
            run_id=task.run_id,
            strategy_name=task.strategy_name,
            strategy_version=task.strategy_version,
            params=task.params,
            universe=task.universe,
            start_datetime=task.start_datetime,
            end_datetime=task.end_datetime,
            status="error",
            error=traceback.format_exc(),
            duration_seconds=round(time.time() - start, 3),
        )
        logging.error(f"[{task.run_id}] {task.strategy_name} failed: {result.error.splitlines()[-1]}")

    try:
        atomic_write_json(result_path, result.to_dict())
    except Exception:
        logging.exception(f"[{task.run_id}] failed to persist result JSON")

    return result


def _sanitize_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    """Strip non-JSON-serializable fields (e.g., numpy types coerced via default=str)."""
    return {k: v for k, v in metrics.items() if k != "universe"}  # universe already in RunResult
