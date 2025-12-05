# datafeed/db_feed.py
import sqlite3
from typing import Iterator, Optional
from pathlib import Path

from database.schema import Bar
from config import SQLITE_DB_PATH


class BaseFeed:
    """
    Abstract base — defines the iterator protocol all feeds must follow
    """
    def __iter__(self) -> Iterator[Bar]:
        return self

    def __next__(self) -> Bar:
        raise NotImplementedError


class SQLiteFeed(BaseFeed):
    """
    High-performance bar-by-bar iterator from SQLite
    → Never loads full history into memory
    → Uses composite index → blazing fast even on 100M+ rows
    → Perfect for backtesting + real-time simulation
    """

    def __init__(
        self,
        symbol: str,
        start_datetime: Optional[int] = None,   # Unix ms
        end_datetime: Optional[int] = None,     # Unix ms (inclusive)
        db_path: Path = SQLITE_DB_PATH
    ):
        self.symbol = symbol.upper()
        self.start_datetime = start_datetime
        self.end_datetime = end_datetime
        self.db_path = Path(db_path)

        self.conn: Optional[sqlite3.Connection] = None
        self.cursor: Optional[sqlite3.Cursor] = None
        self._setup_connection_and_query()

    def _setup_connection_and_query(self) -> None:
        """Open connection and prepare the streaming query"""
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row   # allows dict-like access

        # Build WHERE clause
        where_conditions = ["symbol = ?"]
        params = [self.symbol]

        if self.start_datetime is not None:
            where_conditions.append("datetime >= ?")
            params.append(self.start_datetime)

        if self.end_datetime is not None:
            where_conditions.append("datetime <= ?")
            params.append(self.end_datetime)

        where_clause = " AND " + " AND ".join(where_conditions) if len(where_conditions) > 1 else where_conditions[0]

        query = f"""
            SELECT symbol, datetime, open, high, low, close, volume
            FROM bars
            WHERE {where_clause}
            ORDER BY datetime ASC
        """

        self.cursor = self.conn.cursor()
        self.cursor.execute(query, params)

    def __next__(self) -> Bar:
        """
        Called by the Engine in the main loop:
            for bar in feed:
                strategy.next(bar)
        """
        if self.cursor is None:
            raise StopIteration

        row = self.cursor.fetchone()
        if row is None:
            self.close()
            raise StopIteration

        # Convert SQLite Row → our immutable Bar namedtuple
        return Bar(
            symbol=row["symbol"],
            datetime=row["datetime"],
            open=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=row["volume"]
        )

    def close(self) -> None:
        """Clean up DB connection"""
        if self.cursor:
            self.cursor.close()
            self.cursor = None
        if self.conn:
            self.conn.close()
            self.conn = None

    def __del__(self):
        """Safety net — always close connection"""
        self.close()

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------
    def rewind(self) -> None:
        """Reset cursor to beginning — useful for multiple backtest runs"""
        self.close()
        self._setup_connection_and_query()

    def get_first_datetime(self) -> Optional[int]:
        """Quick peek at first available bar time"""
        cur = self.conn.cursor()
        cur.execute("SELECT MIN(datetime) FROM bars WHERE symbol = ?", (self.symbol,))
        row = cur.fetchone()
        return row[0] if row else None

    def get_last_datetime(self) -> Optional[int]:
        """Quick peek at last available bar time"""
        cur = self.conn.cursor()
        cur.execute("SELECT MAX(datetime) FROM bars WHERE symbol = ?", (self.symbol,))
        row = cur.fetchone()
        return row[0] if row else None