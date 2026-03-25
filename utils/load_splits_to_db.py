# utils/load_splits_to_db.py

import sqlite3
import csv
from datetime import datetime, timezone


def load_splits_csv_to_db(csv_path: str, db_path: str):
    """
    Load splits from CSV into database table
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Create table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS splits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            effective_date INTEGER NOT NULL,
            from_shares REAL NOT NULL,
            to_shares REAL NOT NULL,
            factor REAL NOT NULL,
            created_at INTEGER DEFAULT (strftime('%s', 'now') * 1000)
        )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_splits_symbol_date ON splits(symbol, effective_date)")

    print("Loading splits from CSV...")

    count = 0

    with open(csv_path, 'r', encoding='utf-8-sig') as f:  # ← Added encoding to handle BOM
        reader = csv.DictReader(f)

        # DEBUG: Print actual headers found
        print(f"Headers found in CSV: {reader.fieldnames}")
        print(f"Number of columns: {len(reader.fieldnames)}")

        for i, row in enumerate(reader):
            # DEBUG: Print first row to see structure
            if i == 0:
                print(f"\nFirst row keys: {list(row.keys())}")
                print(f"First row values: {list(row.values())}")

            # Try to access with different possible column names
            try:
                # Try exact match first
                symbol = row['symbol'].strip()
            except KeyError:
                # Try with spaces or different case
                possible_keys = [k for k in row.keys() if 'symbol' in k.lower()]
                if possible_keys:
                    symbol = row[possible_keys[0]].strip()
                else:
                    print(f"  ⚠ Row {i}: Cannot find 'symbol' column")
                    print(f"     Available columns: {list(row.keys())}")
                    break

            try:
                date_str = row['date'].strip()
            except KeyError:
                date_str = row[[k for k in row.keys() if 'date' in k.lower()][0]].strip()

            try:
                from_shares = float(row['from'])
                to_shares = float(row['to'])
            except KeyError:
                # Try alternate column names
                from_shares = float(row[[k for k in row.keys() if 'from' in k.lower()][0]])
                to_shares = float(row[[k for k in row.keys() if 'to' in k.lower()][0]])

            # Calculate factor
            factor = to_shares / from_shares

            # Convert date to Unix millis
            for fmt in ['%m/%d/%Y', '%Y-%m-%d', '%Y/%m/%d', '%m-%d-%Y']:
                try:
                    date = datetime.strptime(date_str, fmt).replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    continue
            else:
                print(f"  ⚠ Skipping {symbol}: Invalid date format '{date_str}'")
                continue

            effective_date = int(date.timestamp() * 1000)

            # Insert
            cursor.execute("""
                INSERT INTO splits (symbol, effective_date, from_shares, to_shares, factor)
                VALUES (?, ?, ?, ?, ?)
            """, (symbol, effective_date, from_shares, to_shares, factor))

            count += 1

    conn.commit()
    print(f"\n✓ Loaded {count} split events")

    # Show summary
    cursor.execute("""
        SELECT symbol, COUNT(*) as num_splits
        FROM splits
        GROUP BY symbol
        ORDER BY num_splits DESC
        LIMIT 10
    """)

    print("\nTop symbols by number of splits:")
    for symbol, num in cursor.fetchall():
        print(f"  {symbol}: {num} splits")

    conn.close()

if __name__ == "__main__":
    load_splits_csv_to_db("E:/baiduDiskDownload/splits.csv","../db/us_market_1min.sqlite")
