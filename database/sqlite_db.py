# database/sqlite_db.py
import sqlite3
from pathlib import Path
from typing import Generator, Tuple
import pandas as pd
import time
from tqdm import tqdm
import logging

from config import RAW_DATA_ROOT, SQLITE_DB_PATH, require_raw_data_root
from .schema import SQLITE_CREATE_TABLE, Bar

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
    # Fiserv trades as FI through 2025-10-31 and as FISV from 2025-11-11, with
    # six sessions where neither ticker has data. FI is listed because it covers
    # 478 sessions against FISV's 13, but neither alone spans the window -- see
    # ADR-024. A backtest ending after 2025-10-31 will report reduced session
    # coverage for this symbol, which is correct rather than a bug.
    "V", "MA", "PYPL", "FIS", "FI", "GPN", "AFRM", "TOST"
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

    def load_all_raw_data(self, show_progress: bool = True, symbols=None) -> None:
        """
        Ingest raw 1-minute CSVs into the bars table.

        Layout expected: <root>/YYYYMM/YYYYMMDD/SYMBOL.csv (the vendor buckets
        by UTC date, so a session's post-20:00-ET tail lands in the next day's
        folder -- harmless here because bars are keyed by their own timestamp).

        symbols: iterable to ingest; defaults to TECH_100.
        """
        start_time = time.time()
        self.connect()
        self.create_table_and_index()

        raw_data_root = require_raw_data_root()
        wanted = set(TECH_100 if symbols is None else symbols)

        # Address files directly instead of rglob("*.csv"). The tree holds ~2.5M
        # files; rglob would walk and materialise every one of them into a list
        # before the first row is read, to then discard all but ~57k. Composing
        # <day>/<SYMBOL>.csv costs one stat per candidate instead.
        day_dirs = sorted(d for d in raw_data_root.glob("*/*") if d.is_dir())
        if not day_dirs:
            # Flat or unknown layout: fall back to a walk.
            logging.warning(f"No YYYYMM/YYYYMMDD folders under {raw_data_root}; falling back to rglob")
            candidates = [(p, p.stem.upper()) for p in raw_data_root.rglob("*.csv")]
            candidates = [(p, sym) for p, sym in candidates if sym in wanted]
        else:
            candidates = [(d / f"{sym}.csv", sym) for d in day_dirs for sym in sorted(wanted)]

        if not candidates:
            raise FileNotFoundError(f"No CSV files found under {raw_data_root}")

        total_inserted = 0
        missing = 0
        pending: list[tuple] = []
        cur = self.conn.cursor()
        pbar = tqdm(candidates, desc="Loading → SQLite", unit="file", disable=not show_progress)

        # Batch across files. The connection is in autocommit mode, so one
        # executemany per file would be one transaction (and one fsync) per
        # file -- ~57k of them. Committing every BATCH_ROWS keeps each
        # transaction large enough to amortise that.
        BATCH_ROWS = 500_000

        def flush():
            nonlocal pending
            if not pending:
                return 0
            cur.execute("BEGIN")
            cur.executemany(
                "INSERT OR IGNORE INTO bars (symbol, datetime, open, high, low, close, volume) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                pending,
            )
            self.conn.commit()
            n, pending = len(pending), []
            return n

        for csv_path, symbol in pbar:
            if not csv_path.exists():
                missing += 1
                continue
            pending.extend(self._read_csv_records(csv_path, symbol))
            if len(pending) >= BATCH_ROWS:
                total_inserted += flush()
                pbar.set_postfix(rows=f"{total_inserted:,}")
        total_inserted += flush()

        self.vacuum_and_optimize()

        duration = time.time() - start_time
        print("\nSQLite Database Successfully Created!")
        print(f"   Symbols requested: {len(wanted)}")
        print(f"   Files read       : {len(candidates) - missing:,} ({missing:,} absent)")
        print(f"   Total bars       : {total_inserted:,}")
        print(f"   Time elapsed     : {duration:.1f} seconds")
        print(f"   DB size          : {self.db_path.stat().st_size / 1024**3:.2f} GB")
        print(f"   Path             : {self.db_path}")

    def _read_csv_records(self, csv_path: Path, symbol: str) -> list:
        """
        Parse one symbol-day CSV into (symbol, datetime_ms, o, h, l, c, v) rows.

        Uses 'eob' (end of bar): a bar timestamped at its close can be acted on
        at that instant. Using 'bob' would let a strategy see a close one minute
        before it happened.
        """
        try:
            df = pd.read_csv(
                csv_path,
                usecols=["eob", "open", "high", "low", "close", "volume"],
                dtype={"open": "float64", "high": "float64", "low": "float64",
                       "close": "float64", "volume": "float64"},
                parse_dates=["eob"],
            )
        except Exception as e:
            logging.warning(f"Failed to read {csv_path}: {e}")
            return []

        if df.empty:
            return []

        # Resolution-independent ns->ms. NOT `.astype("int64") // 1_000_000`:
        # that assumed datetime64[ns], but pandas >= 2.0 infers resolution from
        # the source and these files parse as datetime64[us], so the division
        # silently produced SECONDS -- every bar landing in January 1970, with
        # no error raised and every backtest then matching zero rows.
        stamps = df["eob"].values.astype("datetime64[ms]").astype("int64")

        # itertuples, not iterrows: measured ~10x faster, and iterrows boxes
        # each row into a Series.
        return [
            (symbol, int(ts), r.open, r.high, r.low, r.close, r.volume)
            for ts, r in zip(stamps, df.itertuples(index=False))
        ]

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