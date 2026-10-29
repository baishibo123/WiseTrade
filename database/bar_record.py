# database/bar_record.py
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
