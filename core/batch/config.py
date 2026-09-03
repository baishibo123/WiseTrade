"""
User-facing batch configuration.

Two distinct types (ADR-009):
- PortfolioBatchConfig: one task per (strategy, params) — task carries full universe
- PerSymbolBatchConfig:  one task per (strategy, params, symbol) — each symbol independent

Both produce a flat list of BatchTask objects consumed by BatchRunner. The
execution engine is unified; only task enumeration differs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from pathlib import Path
from typing import Any, Optional, Sequence, Type, Union

from core.batch.orchestrator import BatchRunner, make_batch_dir
from core.batch.types import BatchTask, compute_run_id


StrategySpec = tuple[Type, dict]  # (StrategyClass, {"param_grid": ..., "param_list": ...})
UniverseSpec = Union[str, Sequence[str]]


# ---------------------------------------------------------------------------
# Universe resolution
# ---------------------------------------------------------------------------

def _resolve_universe(spec: UniverseSpec) -> list[str]:
    """Accept a list of symbols or a named-universe string."""
    if isinstance(spec, str):
        from database.sqlite_db import TECH_100
        named = {"TECH_100": list(TECH_100)}
        if spec not in named:
            raise ValueError(f"Unknown named universe: {spec!r}. Known: {list(named)}")
        return sorted(named[spec])
    return list(spec)


# ---------------------------------------------------------------------------
# Param spec expansion
# ---------------------------------------------------------------------------

def _expand_params(spec: dict) -> list[dict]:
    """
    Expand a param spec into a list of concrete param dicts.

    Accepts (mix-and-match):
        {"param_grid": {"fast": [5, 10], "slow": [20, 30]}}
        {"param_list": [{"fast": 5, "slow": 20}, {"fast": 10, "slow": 30}]}
        {"param_grid": ..., "param_list": ...}  # union of both
    """
    grid = spec.get("param_grid") or {}
    listed = spec.get("param_list") or []

    combos: list[dict] = []
    if grid:
        keys = list(grid.keys())
        for combo in product(*(grid[k] for k in keys)):
            combos.append(dict(zip(keys, combo)))
    combos.extend(listed)

    if not combos:
        combos = [{}]  # one run with default params
    return combos


def _strategy_version(strategy_class: Type) -> str:
    return getattr(strategy_class, "VERSION", "1.0")


def _resolve_rth(flag: Optional[bool]) -> bool:
    if flag is None:
        from config import REGULAR_HOURS_ONLY
        return REGULAR_HOURS_ONLY
    return bool(flag)


def _default_results_root() -> Path:
    """
    Project-derived absolute results root.

    Path("results") would be relative to the *current working directory*, so
    running `python /path/to/run_ranking.py` from anywhere but the project root
    silently wrote the whole batch somewhere else. config.RESULTS_DIR is the
    PROJECT_ROOT-derived absolute path and was otherwise unreferenced.
    Imported lazily so importing core.batch does not pull in config.
    """
    from config import RESULTS_DIR
    return RESULTS_DIR


# ---------------------------------------------------------------------------
# Base shared fields
# ---------------------------------------------------------------------------

@dataclass
class _CommonBatchConfig:
    batch_name: str
    strategies: list[StrategySpec]
    universe: UniverseSpec
    start_datetime: int
    end_datetime: int
    n_workers: Optional[int] = None
    save_curves: bool = True
    regular_hours_only: Optional[bool] = None   # None -> config.REGULAR_HOURS_ONLY
    results_root: Path = field(default_factory=lambda: _default_results_root())


# ---------------------------------------------------------------------------
# Portfolio mode: universe = one shared portfolio per task
# ---------------------------------------------------------------------------

@dataclass
class PortfolioBatchConfig(_CommonBatchConfig):
    portfolio_config: dict[str, Any] = field(default_factory=lambda: {
        "initial_cash": 100_000.0,
        "max_positions": 10,
        "max_position_pct": 0.3,
    })

    def build_tasks(self, batch_dir: Path) -> list[BatchTask]:
        universe = _resolve_universe(self.universe)
        rth = _resolve_rth(self.regular_hours_only)
        tasks: list[BatchTask] = []
        for strategy_class, spec in self.strategies:
            version = _strategy_version(strategy_class)
            for params in _expand_params(spec):
                run_id = compute_run_id(
                    strategy_name=strategy_class.__name__,
                    strategy_version=version,
                    params=params,
                    universe=universe,
                    start_datetime=self.start_datetime,
                    end_datetime=self.end_datetime,
                    portfolio_config=self.portfolio_config,
                    regular_hours_only=rth,
                )
                tasks.append(BatchTask(
                    run_id=run_id,
                    strategy_class=strategy_class,
                    strategy_name=strategy_class.__name__,
                    strategy_version=version,
                    params=params,
                    universe=universe,
                    start_datetime=self.start_datetime,
                    end_datetime=self.end_datetime,
                    portfolio_config=self.portfolio_config,
                    regular_hours_only=rth,
                    batch_dir=str(batch_dir),
                    save_curves=self.save_curves,
                ))
        return tasks

    def run(self, resume_dir: Optional[Path] = None, overwrite: bool = False) -> Path:
        # resolve(): BatchTask.batch_dir is documented as absolute, and workers
        # resolve curve and result paths against it. A relative value would be
        # interpreted against each worker's cwd rather than the parent's.
        batch_dir = (Path(resume_dir) if resume_dir else make_batch_dir(self.results_root, self.batch_name)).resolve()
        tasks = self.build_tasks(batch_dir)
        return BatchRunner(batch_dir, n_workers=self.n_workers).run(tasks, overwrite=overwrite)


# ---------------------------------------------------------------------------
# Per-symbol mode: each symbol becomes its own independent task
# ---------------------------------------------------------------------------

@dataclass
class PerSymbolBatchConfig(_CommonBatchConfig):
    """
    Per-symbol mode: each (strategy, params, symbol) tuple is one task.

    Each task starts with the full per_symbol_cash — apples-to-apples comparison
    of strategy quality across symbols (ADR-011).
    """
    per_symbol_cash: float = 100_000.0
    portfolio_overrides: dict[str, Any] = field(default_factory=dict)

    def build_tasks(self, batch_dir: Path) -> list[BatchTask]:
        symbols = _resolve_universe(self.universe)
        rth = _resolve_rth(self.regular_hours_only)
        tasks: list[BatchTask] = []

        for strategy_class, spec in self.strategies:
            version = _strategy_version(strategy_class)
            for params in _expand_params(spec):
                for symbol in symbols:
                    sym_universe = [symbol]
                    portfolio_config = {
                        "initial_cash": self.per_symbol_cash,
                        "max_positions": 1,             # by construction
                        "max_position_pct": 1.0,        # whole portfolio = one symbol
                        **self.portfolio_overrides,
                    }
                    run_id = compute_run_id(
                        strategy_name=strategy_class.__name__,
                        strategy_version=version,
                        params=params,
                        universe=sym_universe,
                        start_datetime=self.start_datetime,
                        end_datetime=self.end_datetime,
                        portfolio_config=portfolio_config,
                        regular_hours_only=rth,
                    )
                    tasks.append(BatchTask(
                        run_id=run_id,
                        strategy_class=strategy_class,
                        strategy_name=strategy_class.__name__,
                        strategy_version=version,
                        params=params,
                        universe=sym_universe,
                        start_datetime=self.start_datetime,
                        end_datetime=self.end_datetime,
                        portfolio_config=portfolio_config,
                        regular_hours_only=rth,
                        batch_dir=str(batch_dir),
                        save_curves=self.save_curves,
                    ))
        return tasks

    def run(self, resume_dir: Optional[Path] = None, overwrite: bool = False) -> Path:
        # resolve(): BatchTask.batch_dir is documented as absolute, and workers
        # resolve curve and result paths against it. A relative value would be
        # interpreted against each worker's cwd rather than the parent's.
        batch_dir = (Path(resume_dir) if resume_dir else make_batch_dir(self.results_root, self.batch_name)).resolve()
        tasks = self.build_tasks(batch_dir)
        return BatchRunner(batch_dir, n_workers=self.n_workers).run(tasks, overwrite=overwrite)
