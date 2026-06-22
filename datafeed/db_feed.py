"""
Database feed implementations
Supports both SQLite (development) and PostgreSQL (production)
"""

from abc import ABC, abstractmethod
from typing import Optional
import logging

from database.schema import Bar


class BaseFeed(ABC):
    """Base interface for data feeds"""

    @abstractmethod
    def __iter__(self):
        """Make feed iterable"""
        pass

    @abstractmethod
    def __next__(self) -> Bar:
        """Fetch next bar"""
        pass


class DatabaseFeed(BaseFeed):
    """
    Generic database feed - works with both SQLite and PostgreSQL
    Automatically detects database type from config
    """

    def __init__(
            self,
            symbol: str,
            start_datetime: int,
            end_datetime: int,
            db_config: Optional[dict] = None
    ):
        """
        Initialize database feed

        Args:
            symbol: Ticker symbol
            start_datetime: Start timestamp (Unix millis)
            end_datetime: End timestamp (Unix millis)
            db_config: Database configuration (None = use default from config.py)
        """
        self.symbol = symbol
        self.start_datetime = start_datetime
        self.end_datetime = end_datetime

        # Load database config
        if db_config is None:
            from config import DATABASE_CONFIG
            db_config = DATABASE_CONFIG

        self.db_type = db_config.get("type", "sqlite")
        self.db_config = db_config

        # Connection state (initialized in __iter__)
        self.connection = None
        self.cursor = None

    def __iter__(self):
        """Initialize database connection and cursor"""

        if self.db_type == "sqlite":
            self._init_sqlite()
        elif self.db_type == "postgresql":
            self._init_postgresql()
        else:
            raise ValueError(f"Unsupported database type: {self.db_type}")

        return self

    def _init_sqlite(self):
        """Initialize SQLite connection with Row factory for named access"""
        import sqlite3

        db_path = self.db_config.get("path", "data/bars.db")
        self.connection = sqlite3.connect(db_path)

        # CRITICAL: Enable dictionary-like access to rows
        self.connection.row_factory = sqlite3.Row

        self.cursor = self.connection.cursor()

        self.cursor.execute("""
            SELECT symbol, datetime, open, high, low, close, volume
            FROM bars
            WHERE symbol = ? AND datetime >= ? AND datetime <= ?
            ORDER BY datetime
        """, (self.symbol, self.start_datetime, self.end_datetime))

    def _init_postgresql(self):
        """Initialize PostgreSQL connection with RealDictCursor for named access"""
        import psycopg2
        from psycopg2.extras import RealDictCursor

        self.connection = psycopg2.connect(
            host=self.db_config.get("host", "localhost"),
            port=self.db_config.get("port", 5432),
            database=self.db_config.get("database", "wisetrade"),
            user=self.db_config.get("user", "postgres"),
            password=self.db_config.get("password", "")
        )

        # CRITICAL: Use RealDictCursor for dictionary-like access
        self.cursor = self.connection.cursor(cursor_factory=RealDictCursor)

        # PostgreSQL uses %s placeholders
        self.cursor.execute("""
            SELECT symbol, datetime, open, high, low, close, volume
            FROM bars
            WHERE symbol = %s AND datetime >= %s AND datetime <= %s
            ORDER BY datetime
        """, (self.symbol, self.start_datetime, self.end_datetime))

    def __next__(self) -> Bar:
        """
        Fetch next bar from cursor
        Uses named column access for robustness
        """
        if self.cursor is None:
            raise StopIteration

        row = self.cursor.fetchone()

        if row is None:
            self.close()
            raise StopIteration

        # ROBUST: Use explicit column names (not indices)
        return Bar(
            symbol=row["symbol"],
            datetime=row["datetime"],
            open=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=row["volume"]
        )

    def close(self):
        """Close database connection"""
        if self.connection:
            self.connection.close()
            self.connection = None
            self.cursor = None


# ============================================================================
# Testing utility
# ============================================================================

def test_database_feed():
    """
    Test DatabaseFeed with both SQLite and PostgreSQL
    """
    print("\n=== DatabaseFeed Test ===")

    # Test SQLite
    print("\n--- Testing SQLite ---")
    try:
        sqlite_config = {
            "type": "sqlite",
            "path": "data/bars.db"
        }

        feed = DatabaseFeed(
            symbol="AAPL",
            start_datetime=1609459200000,  # 2021-01-01
            end_datetime=1609545600000,  # 2021-01-02
            db_config=sqlite_config
        )

        count = 0
        for bar in feed:
            if count < 3:
                print(f"  Bar {count}: {bar.symbol} @ {bar.datetime} = ${bar.close:.2f}")
            count += 1

        print(f"✓ SQLite: Fetched {count} bars")

    except Exception as e:
        print(f"✗ SQLite test failed: {e}")

    # Test PostgreSQL (if available)
    print("\n--- Testing PostgreSQL ---")
    try:
        postgresql_config = {
            "type": "postgresql",
            "host": "localhost",
            "database": "wisetrade",
            "user": "postgres",
            "password": ""
        }

        feed = DatabaseFeed(
            symbol="AAPL",
            start_datetime=1609459200000,
            end_datetime=1609545600000,
            db_config=postgresql_config
        )

        count = 0
        for bar in feed:
            if count < 3:
                print(f"  Bar {count}: {bar.symbol} @ {bar.datetime} = ${bar.close:.2f}")
            count += 1

        print(f"✓ PostgreSQL: Fetched {count} bars")

    except Exception as e:
        print(f"⚠ PostgreSQL test skipped (not available): {e}")


if __name__ == "__main__":
    test_database_feed()