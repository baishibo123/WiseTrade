# utils/load_splits_to_db.py
"""
Import splits.csv into the splits table.

Idempotent: PRIMARY KEY (symbol, ex_date) plus INSERT OR REPLACE, so running
twice is identical to running once. The previous version had an AUTOINCREMENT
id and a plain INSERT, so a second run duplicated every split -- and the
adjuster then applied each one twice, turning NVDA's 1:10 into 1:100 with no
warning anywhere.

Dates are stored exactly as published (YYYY-MM-DD). Converting to an instant is
database/adjustments.py's job, so the timezone decision lives in one place.
"""

import csv
import sqlite3
import sys
from pathlib import Path


def load_splits_csv_to_db(csv_path: str, db_path: str) -> int:
    from database.adjustments import SPLITS_SCHEMA

    conn = sqlite3.connect(db_path)
    conn.executescript(SPLITS_SCHEMA)

    rows, skipped = [], 0
    # utf-8-sig: the vendor writes a BOM, which would otherwise become part of
    # the first column's name.
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            try:
                symbol = row["symbol"].strip().upper()
                ex_date = row["date"].strip()
                from_shares = float(row["from"])
                to_shares = float(row["to"])
                if not symbol or not from_shares or not to_shares:
                    raise ValueError("empty field")
            except (KeyError, ValueError, AttributeError):
                skipped += 1
                continue
            rows.append((symbol, ex_date, from_shares, to_shares))

    conn.executemany(
        "INSERT OR REPLACE INTO splits (symbol, ex_date, from_shares, to_shares) VALUES (?,?,?,?)",
        rows,
    )
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM splits").fetchone()[0]
    conn.close()

    print(f"  loaded {len(rows)} split rows ({skipped} skipped); table now holds {total}")
    return len(rows)


if __name__ == "__main__":
    # Project root on sys.path so config.py is the only place any path is spelled out.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import require_splits_csv, SQLITE_DB_PATH

    load_splits_csv_to_db(str(require_splits_csv()), str(SQLITE_DB_PATH))
