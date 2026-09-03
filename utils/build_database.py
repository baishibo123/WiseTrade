"""
Build the bar database end to end.

    raw CSVs ──► bars          (database/sqlite_db.py)
    splits.csv ─► splits       (utils/load_splits_to_db.py)
                  adj_factors  ┐
                  bars_adjusted├─ (database/adjustments.py)
                               ┘
Replaces utils/adjust_database_sql.py, which copied the whole database and
applied splits with an in-place UPDATE. That needed 2x disk, rewrote every
touched row, and could double-adjust silently if pointed at its own output.
Adjustment is now derived, so none of those failure modes exist.

    python utils/build_database.py            # TECH_100
    python utils/build_database.py --all      # every symbol in the CSV tree
"""

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import SQLITE_DB_PATH, require_splits_csv          # noqa: E402
from database.adjustments import build_adj_factors, validate_adjustments  # noqa: E402
from database.sessions import derive_sessions  # noqa: E402
from database.sqlite_db import SQLiteDatabase                   # noqa: E402
from utils.load_splits_to_db import load_splits_csv_to_db       # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="ingest every symbol found, not just TECH_100")
    ap.add_argument("--db", default=str(SQLITE_DB_PATH))
    ap.add_argument("--skip-bars", action="store_true", help="only rebuild splits/factors")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if not args.skip_bars:
        print("[1/4] Ingesting bars")
        symbols = _all_symbols() if args.all else None
        SQLiteDatabase(Path(args.db)).load_all_raw_data(symbols=symbols)

    print("\n[2/4] Loading splits")
    load_splits_csv_to_db(str(require_splits_csv()), args.db)

    print("\n[3/4] Building adjustment factors")
    conn = sqlite3.connect(args.db)
    build_adj_factors(conn)

    print("\n[4/5] Deriving trading sessions")
    derive_sessions(conn)

    print("\n[5/5] Validating")
    problems = validate_adjustments(conn)
    conn.close()
    if problems:
        print(f"  {len(problems)} problem(s):")
        for p in problems[:40]:
            print(f"    {p}")
        return 1
    print("  clean — every split boundary is continuous in bars_adjusted")
    return 0


def _all_symbols() -> list:
    """Every distinct SYMBOL.csv stem in the raw tree."""
    from config import require_raw_data_root
    root = require_raw_data_root()
    day_dirs = sorted(d for d in root.glob("*/*") if d.is_dir())
    probe = day_dirs[len(day_dirs) // 2] if day_dirs else root
    return sorted({p.stem.upper() for p in probe.glob("*.csv")})


if __name__ == "__main__":
    raise SystemExit(main())
