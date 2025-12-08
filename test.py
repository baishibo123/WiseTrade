import sqlite3
import os
from datetime import datetime
from config import SQLITE_DB_PATH

# 1. SETUP PATHS
# Adjust this path if your config.py points elsewhere
DB_PATH = SQLITE_DB_PATH


def check_data():
    if not os.path.exists(DB_PATH):
        print(f"CRITICAL ERROR: Database not found at {DB_PATH}")
        return

    print(f"--- Inspecting {DB_PATH} ---")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    symbol = "AAPL"
    t_start = 1751414400000
    t_end = 1756771200000

    # A. Check if the table exists
    try:
        cursor.execute("SELECT count(*) FROM bars")
        total_rows = cursor.fetchone()[0]
        print(f"Total rows in 'bars' table: {total_rows:,}")
    except sqlite3.OperationalError as e:
        print(f"SQL Error: {e}")
        return

    # B. Check if Symbol exists at all
    cursor.execute("SELECT count(*) FROM bars WHERE symbol = ?", (symbol,))
    symbol_rows = cursor.fetchone()[0]
    print(f"Total rows for {symbol}: {symbol_rows:,}")

    if symbol_rows == 0:
        print(">> PROBLEM FOUND: Symbol 'AAPL' does not exist in DB.")
        return

    # C. Check the Time Range
    cursor.execute("""
        SELECT MIN(datetime), MAX(datetime) 
        FROM bars WHERE symbol = ?
    """, (symbol,))
    min_ts, max_ts = cursor.fetchone()

    print(f"\n{symbol} Range in DB:")
    print(f"  Min: {min_ts} ({datetime.utcfromtimestamp(min_ts / 1000)})")
    print(f"  Max: {max_ts} ({datetime.utcfromtimestamp(max_ts / 1000)})")

    print(f"\nYour Requested Range:")
    print(f"  Start: {t_start} ({datetime.utcfromtimestamp(t_start / 1000)})")
    print(f"  End:   {t_end}   ({datetime.utcfromtimestamp(t_end / 1000)})")

    # D. Check Overlap
    cursor.execute("""
        SELECT count(*) FROM bars 
        WHERE symbol = ? AND datetime >= ? AND datetime <= ?
    """, (symbol, t_start, t_end))
    target_rows = cursor.fetchone()[0]
    print(f"\n>> Rows found in requested range: {target_rows}")

    conn.close()


if __name__ == "__main__":
    check_data()