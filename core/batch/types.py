"""
Worker contract: BatchTask (input), RunResult (output), and run_id hashing.

See ADR-004 in docs/decisions.md for the run_id scheme.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class BatchTask:
    """
    One unit of work for a batch worker.

    Produced by a BatchConfig (PortfolioBatchConfig or PerSymbolBatchConfig).
    Consumed by run_one() in core.batch.worker.
    """
    run_id: str
    strategy_class: type                    # imported strategy class (pickled by qualified name)
    strategy_name: str
    strategy_version: str
    params: dict[str, Any]
    universe: list[str]
    start_datetime: int                     # Unix millis UTC
    end_datetime: int                       # Unix millis UTC
    portfolio_config: dict[str, Any]
    batch_dir: str                          # absolute path to results/<batch_id>/
    save_curves: bool = True


@dataclass
class RunResult:
    """
    Worker output. Always returned, even on error.

    Persisted as runs/<run_id>.json — the existence of this file is the
    run-level commit signal (ADR-006).
    """
    run_id: str
    strategy_name: str
    strategy_version: str
    params: dict[str, Any]
    universe: list[str]
    start_datetime: int
    end_datetime: int
    status: str                             # "ok" | "error"
    metrics: Optional[dict[str, Any]] = None
    error: Optional[str] = None             # traceback string when status="error"
    duration_seconds: float = 0.0
    curve_path: Optional[str] = None        # relative to batch_dir, e.g. "curves/<run_id>/portfolio.parquet"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_run_id(
    strategy_name: str,
    strategy_version: str,
    params: dict[str, Any],
    universe: list[str],
    start_datetime: int,
    end_datetime: int,
    portfolio_config: dict[str, Any],
) -> str:
    """
    Deterministic 16-char hex hash of the task's identifying fields.

    Bumping strategy_version invalidates prior hashes — required when
    strategy behavior changes without renaming the class. See ADR-004.

    portfolio_config is hashed in full, not key-by-key (ADR-013). Engine only
    reads five keys from it (initial_cash, max_positions, max_position_pct,
    min_trade_size, min_trade_size_per_symbol), so hashing the whole dict can
    produce a spurious cache *miss* when an ignored key changes. That is the
    safe direction to err: a miss costs one recomputation, whereas a spurious
    cache *hit* silently returns metrics computed under different capital
    constraints. Deliberately no default — a caller that forgets this argument
    should fail loudly rather than fall back to an unconstrained hash.
    """
    payload = {
        "strategy_name": strategy_name,
        "strategy_version": strategy_version,
        "params": _canonicalize(params),
        "universe": sorted(universe),
        "start_datetime": start_datetime,
        "end_datetime": end_datetime,
        "portfolio_config": _canonicalize(portfolio_config),
    }
    serialized = json.dumps(payload, sort_keys=True, default=str)
    digest = hashlib.sha256(serialized.encode()).hexdigest()
    return digest[:16]


def _canonicalize(obj: Any) -> Any:
    """Recursively sort dict keys for stable hashing."""
    if isinstance(obj, dict):
        return {k: _canonicalize(obj[k]) for k in sorted(obj.keys())}
    if isinstance(obj, (list, tuple)):
        return [_canonicalize(x) for x in obj]
    return obj
