# config.py
from pathlib import Path
import os

# ============================================================================
# Project layout
# ============================================================================
# Everything here is DERIVED from this file's own location. These paths are
# portable across Windows/macOS/Linux by construction and are deliberately not
# configurable -- they name directories that live inside the repo.
#
# Do not add platform detection (`if os.name == "nt"`) to path expressions.
# Correct cross-platform pathing here means "derive from PROJECT_ROOT" or
# "read from the environment", never "branch on the OS".

PROJECT_ROOT = Path(__file__).parent
DB_DIR = PROJECT_ROOT / "data"
RESULTS_DIR = PROJECT_ROOT / "results"
# One database. Split adjustment is a view over immutable raw bars
# (database/split_adjustments.py), so there is no separate "adjusted" file to keep in
# sync -- bars, splits, adj_factors and the bars_adjusted view all live here.
SQLITE_DB_PATH = DB_DIR / "us_market_1min.sqlite"

# The default above stays repo-derived and portable. The override exists because
# without it there is no way to point a run at a database other than the one
# real file -- which makes the system untestable against a fixture, and means a
# DB kept on an external drive cannot be used at all.
_DB_PATH_ENV = os.getenv("WISETRADE_SQLITE_DB_PATH")
if _DB_PATH_ENV:
    SQLITE_DB_PATH = Path(_DB_PATH_ENV)


def ensure_dirs() -> None:
    """
    Create the project's output directories. Call from entry points.

    Deliberately NOT done at import time: config is imported transitively by
    every batch worker (run_ranking -> core.batch.batch_config -> database.ingest
    -> config), so an import-time mkdir means N worker processes doing
    filesystem work on startup for no reason.
    """
    DB_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================================
# Machine-specific paths
# ============================================================================
# These point outside the repo, so they differ on every machine and must never
# be committed as literals. Environment only, and no fallback guess: unset
# means "this machine cannot do that task", which is the correct state on a dev
# Mac that only ever reads a prebuilt database.
#
# Validation happens at point of use via the require_* helpers below, not here.
# Importing config must never fail just because an unrelated task's data root
# is absent -- every batch worker imports this module.
#
#   Windows:  set WISETRADE_RAW_DATA_ROOT=E:/stock
#   macOS:    export WISETRADE_RAW_DATA_ROOT=/Volumes/data/stock

_RAW_DATA_ROOT_ENV = os.getenv("WISETRADE_RAW_DATA_ROOT")
RAW_DATA_ROOT = Path(_RAW_DATA_ROOT_ENV) if _RAW_DATA_ROOT_ENV else None

_SPLITS_CSV_ENV = os.getenv("WISETRADE_SPLITS_CSV")
SPLITS_CSV_PATH = Path(_SPLITS_CSV_ENV) if _SPLITS_CSV_ENV else None


def _require_path(value, env_var: str, what: str, expect_dir: bool) -> Path:
    """Shared validation for the machine-specific paths above."""
    if value is None:
        raise RuntimeError(
            f"{env_var} is not set, so {what} cannot be located.\n"
            f"  Windows:  set {env_var}=E:/stock\n"
            f"  macOS:    export {env_var}=/Volumes/data/stock"
        )
    ok = value.is_dir() if expect_dir else value.is_file()
    if not ok:
        kind = "directory" if expect_dir else "file"
        raise FileNotFoundError(f"{env_var} points at a missing {kind}: {value}")
    return value


def require_raw_data_root() -> Path:
    """Validated RAW_DATA_ROOT. Call immediately before reading raw CSVs."""
    return _require_path(
        RAW_DATA_ROOT, "WISETRADE_RAW_DATA_ROOT", "the raw 1-minute CSV tree", expect_dir=True
    )


def require_splits_csv() -> Path:
    """Validated SPLITS_CSV_PATH. Call immediately before loading splits."""
    return _require_path(
        SPLITS_CSV_PATH, "WISETRADE_SPLITS_CSV", "the corporate-splits CSV", expect_dir=False
    )


# ============================================================================
# Market Data Scope
# ============================================================================
# Restrict backtests to regular trading hours, 09:30-16:00 ET (ADR-020).
#
# Default on. The two regimes differ in data quality, not just hours: regular
# session bars are dense (measured 390 of 390 minutes on 475 of 479 sessions),
# while extended and overnight bars are sparse -- on one sample day AAPL had
# 797 bars and a thin name had 15. TimeAlignedIterator forward-fills across
# gaps, and that fill has not been validated, so including extended hours
# compounds an unvalidated fill with the data most likely to stress it.
#
# A switch rather than a deletion: extended-hours behaviour is a legitimate
# research target and the bars are already in the database. Turning it off
# changes results, so it is part of the run_id hash.
REGULAR_HOURS_ONLY = os.getenv("WISETRADE_REGULAR_HOURS_ONLY", "1").lower() not in ("0", "false", "no")

# Which exchange calendar bounds a trading session (database/sessions.py).
#
# XNYS while the vendor's own `exchange` column reads XNAS. That is safe only
# because NYSE and Nasdaq keep identical regular hours and holidays for US
# equities -- it is not safe in general, and the --all ingest brings in ETFs and
# cross-listings. Configurable so the assumption is visible and changeable
# rather than compiled in.
EXCHANGE_CALENDAR = os.getenv("WISETRADE_EXCHANGE_CALENDAR", "XNYS")

# ============================================================================
# Parallelism
# ============================================================================
# Start method for batch worker processes: "spawn" or "fork" (ADR-015, OPEN).
#
# Default is spawn: it is the only method Windows supports at all, and it is
# the safe one here because the parent process is multi-threaded by the time
# workers are created -- BatchRunner starts its QueueListener thread before it
# builds the pool, and forking a multi-threaded process risks deadlock (Python
# 3.12 warns about this explicitly).
#
# "fork" is exposed so the startup-cost question in ADR-015 can actually be
# measured rather than argued about. Treat it as experimental: it is POSIX-only
# and BatchRunner will warn when it is selected. An unsupported value falls
# back to spawn with a warning rather than crashing.
MP_START_METHOD = os.getenv("WISETRADE_MP_START_METHOD", "spawn")

# ============================================================================
# Database Configuration
# ============================================================================

# Default database type: "sqlite" or "postgresql"
DB_TYPE = os.getenv("WISETRADE_DB_TYPE", "sqlite")

# SQLite configuration (development)
# Absolute path is required: workers spawned by multiprocessing may not share CWD.
SQLITE_CONFIG = {
    "type": "sqlite",
    "path": str(SQLITE_DB_PATH),
}

# PostgreSQL configuration (production)
POSTGRESQL_CONFIG = {
    "type": "postgresql",
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": int(os.getenv("POSTGRES_PORT", 5432)),
    "database": os.getenv("POSTGRES_DB", "wisetrade"),
    "user": os.getenv("POSTGRES_USER", "postgres"),
    "password": os.getenv("POSTGRES_PASSWORD", "")
}

# Active database configuration
DATABASE_CONFIG = POSTGRESQL_CONFIG if DB_TYPE == "postgresql" else SQLITE_CONFIG

# ============================================================================
# Strategy Defaults
# ============================================================================

# How many bars of per-symbol history a Strategy retains (base.Strategy keeps a
# deque of this length). Named here rather than buried as a literal because it
# is a silent ceiling: an indicator period longer than this can never be
# satisfied, so calculate_sma returns None on every tick, the strategy emits no
# signals, and the run completes "ok" with zero trades and 0.00% return. Nothing
# reports the conflict -- see ADR-018 for the four points at which it is
# swallowed. If you sweep a lookback-like parameter, check it against this.
DEFAULT_MAX_LOOKBACK = 300

# ============================================================================
# HTTP service (api/)
# ============================================================================
# Appended by feat/bar-service. Additions here go at the end of the file, never
# in the middle, so parallel branches cannot produce a textual conflict.

# Where scripts/dump_fixtures.py writes the real JSON payloads that
# feat/chart-ui develops against. Committed rather than gitignored: a UI built
# against an imagined API cannot detect that it imagined it, and the whole point
# of the fixture is to be the thing that was actually served.
FIXTURES_DIR = PROJECT_ROOT / "fixtures"

# ============================================================================
# Label store (database/label_store.py)
# ============================================================================
# Appended by feat/label-store. Labels are the only irreplaceable data in the
# system, so they live outside every git working tree (ADR-027): worktrees are
# recycled and `git clean -x` removes ignored files, and either would take a
# store derived from PROJECT_ROOT with it. Environment only, with no default --
# a default would give each worktree its own empty store, and a service that
# starts against an empty store does not say the real one is elsewhere.
#
#   macOS:    export WISETRADE_LABELS_DIR=/Users/<you>/WiseTradeData/labels

_LABELS_DIR_ENV = os.getenv("WISETRADE_LABELS_DIR")
LABELS_DIR = Path(_LABELS_DIR_ENV) if _LABELS_DIR_ENV else None


def require_labels_dir() -> Path:
    """Validated LABELS_DIR. Call immediately before opening the label store."""
    return _require_path(LABELS_DIR, "WISETRADE_LABELS_DIR", "the label store", expect_dir=True)
