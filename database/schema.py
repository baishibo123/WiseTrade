# database/schema.py
from collections import namedtuple
from datetime import datetime

# This part referred the design of Backtrader's linebuffer[0]

Bar = namedtuple(
    "Bar",
    [
        "symbol",      # str:  "AAPL", "TSLA", ...
        "datetime",    # int:  Unix milliseconds (UTC)
        "open",
        "high",
        "low",
        "close",
        "volume",      # float or int
    ],
)

# -----------------------------------
# SQLite table definitions (Query strings)
# -----------------------------------

SQLITE_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS bars (
    symbol      TEXT    NOT NULL,
    datetime    INTEGER NOT NULL,       -- Unix milliseconds UTC
    open        REAL    NOT NULL,
    high        REAL    NOT NULL,
    low         REAL    NOT NULL,
    close       REAL    NOT NULL,
    volume      REAL    NOT NULL,

    PRIMARY KEY (symbol, datetime)
);

-- Index for super-fast range queries (this is the magic for speed)
CREATE INDEX IF NOT EXISTS idx_symbol_time ON bars(symbol, datetime);
"""

# -----------------------------------
# MongoDB document layout (one document = one bar)
# -----------------------------------
# {
#   "symbol": "AAPL",
#   "datetime": 1753747200000,   -- end-of-bar Unix ms
#   "o": 172.34,
#   "h": 173.10,
#   "l": 172.20,
#   "c": 172.95,
#   "v": 52341837.0
# }
# Index: {"symbol": 1, "datetime": 1}