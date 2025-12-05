# config.py
from pathlib import Path

RAW_DATA_ROOT = Path("E:/stock")
PROJECT_ROOT = Path(__file__).parent
DB_DIR = PROJECT_ROOT / "db"
DB_DIR.mkdir(exist_ok=True)

SQLITE_DB_PATH = DB_DIR / "us_market_1min.sqlite"

# Mongo settings (for later)
MONGO_URI = "mongodb://localhost:27017"
MONGO_DB_NAME = "us_market"
MONGO_COLLECTION = "bars_1min"

# Backtesting defaults
INITIAL_CASH = 100_000.0
DEFAULT_TIMEFRAME = "1min"
