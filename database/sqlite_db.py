# database/sqlite_db.py
import sqlite3
from pathlib import Path
from typing import Generator, Tuple
import pandas as pd
import time
from tqdm import tqdm
import logging

from config import RAW_DATA_ROOT, SQLITE_DB_PATH
from schema import SQLITE_CREATE_TABLE, Bar

# ----------------------------------------------------------------------
# Your exact TECH_100 universe — only these symbols will be loaded
# ----------------------------------------------------------------------
TECH_100 = {
    # Giants (Mag 7 + Big Tech)
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "NVDA", "META", "TSLA",
    # Semiconductors
    "AVGO", "AMD", "QCOM", "TXN", "MU", "AMAT", "LRCX", "ADI", "KLAC",
    "MRVL", "NXPI", "MCHP", "ON", "GFS", "ARM", "INTC", "TSM", "ASML",
    "STM", "TER", "ENTG", "SWKS", "QRVO", "WOLF", "LSCC",
    # Software / SaaS / Cloud
    "CRM", "ADBE", "ORCL", "INTU", "NOW", "IBM", "WDAY", "SNPS", "CDNS",
    "ADSK", "PANW", "FTNT", "CRWD", "PLTR", "DDOG", "ZS", "ANET", "TEAM",
    "HUBS", "NET", "DOCU", "OKTA", "MDB", "DT", "ZM", "SSNC", "TYL",
    "PTC", "GEN", "CHKP", "AKAM", "CFLT", "GTLB", "PATH", "SNOW", "TWLO",
    "PCTY", "PAYC", "MANH", "OTEX",
    # Internet / E-commerce / Apps
    "NFLX", "BKNG", "ABNB", "UBER", "DASH", "SHOP", "MELI", "PDD", "JD",
    "BABA", "EBAY", "ETSY", "LYFT", "SNAP", "PINS", "RBLX", "DKNG", "HOOD",
    "COIN", "TTD", "APP", "DUOL", "Z",
    # Hardware / Networking
    "CSCO", "DELL", "HPQ", "HPE", "GLW", "STX", "WDC", "NTAP", "SMCI",
    "PSTG", "IONQ",
    # Fintech / Payments
    "V", "MA", "PYPL", "SQ", "FIS", "FISV", "GPN", "AFRM", "TOST"
}


class SQLiteDatabase:
    """
    This class used pandas for the loading of raw CSV data into a SQLite database. No pandas
    function is used for querying; only sqlite3 is used for that.
    """

    def __init__(self, db_path: Path = SQLITE_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        """Open connection with maximum bulk-load performance settings"""
        if self.conn is not None:
            return self.conn

        self.conn = sqlite3.connect(self.db_path, isolation_level=None)  # autocommit mode for speed
        cur = self.conn.cursor()

        # --- Performance PRAGMAs (battle-tested for 100M+ row loads) ---
        cur.execute("PRAGMA journal_mode = WAL;")          # Allow concurrent reads
        cur.execute("PRAGMA synchronous = NORMAL;")       # Safe + fast
        cur.execute("PRAGMA cache_size = -64000;")         # 64 MB cache (negative = KB)
        cur.execute("PRAGMA temp_store = MEMORY;")        # Temp tables in RAM
        cur.execute("PRAGMA foreign_keys = OFF;")          # Not needed here

        return self.conn

    def create_table_and_index(self) -> None:
        """Create table + critical composite index exactly once"""
        if self.conn is None:
            self.connect()

        self.conn.executescript(SQLITE_CREATE_TABLE)
        logging.info("Table 'bars' and index created/verified")

    def load_all_raw_data(self, show_progress: bool = True) -> None:
        start_time = time.time()
        self.connect()
        self.create_table_and_index()

        # Find every CSV in your E:\stock\...\*.csv structure
        csv_files = list(RAW_DATA_ROOT.rglob("*.csv"))
        if not csv_files:
            raise FileNotFoundError(f"No CSV files found under {RAW_DATA_ROOT}")

        total_inserted = 0
        pbar = tqdm(csv_files, desc="Loading TECH_100 → SQLite", unit="file", disable=not show_progress)

        for csv_path in pbar:
            symbol = csv_path.stem.upper()   # filename without .csv → e.g. "AAPL"
            if symbol not in TECH_100:
                continue  # Skip non-tech stocks → keeps DB small & submittable

            inserted = self._load_single_csv(csv_path, symbol)
            total_inserted += inserted

            pbar.set_postfix({
                "symbol": symbol,
                "inserted": f"{total_inserted:,}",
                "file": csv_path.name
            })

        self.vacuum_and_optimize()

        duration = time.time() - start_time
        print("\nSQLite Database Successfully Created!")
        print(f"   Symbols loaded   : {len(TECH_100)} (TECH_100 only)")
        print(f"   Total bars       : {total_inserted:,}")
        print(f"   Time elapsed     : {duration:.1f} seconds")
        print(f"   DB size          : {self.db_path.stat().st_size / 1024**3:.2f} GB")
        print(f"   Path             : {self.db_path}")

    def _load_single_csv(self, csv_path: Path, symbol: str) -> int:
        """
        Load one symbol's daily CSV using pandas → convert → bulk insert
        Uses 'eob' column → end-of-bar timestamp (broker standard)
        """
        try:
            # Read only needed columns + parse eob directly
            df = pd.read_csv(
                csv_path,
                usecols=["eob", "open", "high", "low", "close", "volume"],
                dtype={"open": "float64", "high": "float64", "low": "float64",
                       "close": "float64", "volume": "float64"},
                parse_dates=["eob"]
            )
        except Exception as e:
            logging.warning(f"Failed to read {csv_path}: {e}")
            return 0

        if df.empty:
            return 0

        # Convert eob → Unix milliseconds (UTC, end-of-bar)
        df["datetime"] = df["eob"].astype('int64') // 1_000_000  # ns → ms
        df = df.drop(columns=["eob"])

        # Build list of tuples: (symbol, datetime_ms, o, h, l, c, v)
        records = [
            (symbol, int(row["datetime"]), row["open"], row["high"], row["low"], row["close"], row["volume"])
            for _, row in df.iterrows()
        ]

        # Bulk insert with duplicate protection
        cur = self.conn.cursor()
        cur.executemany("""
            INSERT OR IGNORE INTO bars (symbol, datetime, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, records)
        self.conn.commit()

        return len(records)

    def vacuum_and_optimize(self) -> None:
        """Final cleanup & optimization — makes queries lightning fast"""
        if self.conn is None:
            return
        logging.info("Running VACUUM + ANALYZE...")
        self.conn.execute("PRAGMA optimize;")
        self.conn.execute("VACUUM;")
        self.conn.execute("ANALYZE;")

    # ------------------------------------------------------------------
    # Utility methods
    # ------------------------------------------------------------------
    def get_all_symbols(self) -> list[str]:
        cur = self.conn.cursor()
        cur.execute("SELECT DISTINCT symbol FROM bars ORDER BY symbol")
        return [row[0] for row in cur.fetchall()]

    def get_date_range(self, symbol: str) -> Tuple[int, int]:
        cur = self.conn.cursor()
        cur.execute("SELECT MIN(datetime), MAX(datetime) FROM bars WHERE symbol = ?", (symbol,))
        row = cur.fetchone()
        return (row[0], row[1]) if row[0] else (0, 0)

    def count_bars(self, symbol: str | None = None) -> int:
        cur = self.conn.cursor()
        if symbol:
            cur.execute("SELECT COUNT(*) FROM bars WHERE symbol = ?", (symbol,))
        else:
            cur.execute("SELECT COUNT(*) FROM bars")
        return cur.fetchone()[0]

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------
    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            self.conn.close()


# ----------------------------------------------------------------------
# Run this file directly → builds your submittable DB
# ----------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db = SQLiteDatabase()
    db.load_all_raw_data(show_progress=True)