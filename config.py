# config.py
from pathlib import Path
import os

RAW_DATA_ROOT = Path("E:/stock")
PROJECT_ROOT = Path(__file__).parent
DB_DIR = PROJECT_ROOT / "db"
RESULTS_DIR = PROJECT_ROOT / "results"
DB_DIR.mkdir(exist_ok=True)
RESULTS_DIR.mkdir(exist_ok=True)
SQLITE_DB_PATH = DB_DIR / "us_market_1min_adjusted.sqlite"

# Mongo settings (for later)
MONGO_URI = "mongodb://localhost:27017"
MONGO_DB_NAME = "us_market"
MONGO_COLLECTION = "bars_1min"

# Backtesting defaults
INITIAL_CASH = 100_000.0
DEFAULT_TIMEFRAME = "1min"

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
# Backtesting Defaults
# ============================================================================

DEFAULT_INITIAL_CASH = 100_000.0
DEFAULT_MAX_POSITIONS = 10
DEFAULT_MAX_POSITION_PCT = 0.3
DEFAULT_MIN_TRADE_SIZE = 0.1