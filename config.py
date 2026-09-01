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
DB_DIR = PROJECT_ROOT / "db"
RESULTS_DIR = PROJECT_ROOT / "results"
# The adjusted DB is what backtests read; the raw one is the ingest staging
# target that utils/adjust_database_sql.py reads from. Both named here so no
# script has to hardcode a CWD-relative "../db/..." literal again.
SQLITE_RAW_DB_PATH = DB_DIR / "us_market_1min.sqlite"
SQLITE_DB_PATH = DB_DIR / "us_market_1min_adjusted.sqlite"

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
    every batch worker (run_ranking -> core.batch.config -> database.sqlite_db
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
# Backtesting Defaults
# ============================================================================
# UNREFERENCED as of this commit: nothing outside config.py reads any name in
# this section. The values Engine actually uses are hardcoded at
# core/engine.py:88-92, and callers override them via portfolio_config. Kept
# rather than deleted because tests/ is gitignored and its imports cannot be
# checked from a fresh clone. Resolve by either deleting these or making
# Engine read them -- three sources of truth is one too many.

INITIAL_CASH = 100_000.0
DEFAULT_TIMEFRAME = "1min"

DEFAULT_INITIAL_CASH = 100_000.0
DEFAULT_MAX_POSITIONS = 10
DEFAULT_MAX_POSITION_PCT = 0.3
DEFAULT_MIN_TRADE_SIZE = 0.1

# Mongo settings (for later; also unreferenced)
MONGO_URI = "mongodb://localhost:27017"
MONGO_DB_NAME = "us_market"
MONGO_COLLECTION = "bars_1min"
