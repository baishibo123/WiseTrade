"""
Apply corporate action adjustments using pure SQL (most efficient method)
Loads splits from database instead of CSV
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def create_adjusted_database(source_db: str, output_db: str, splits_db: str = None):
    """
    Create adjusted database copy with corporate actions applied

    Args:
        source_db: Path to original database
        output_db: Path to output adjusted database
        splits_db: Path to database with splits table (default: same as source_db)
    """

    print("="*70)
    print("CORPORATE ACTION ADJUSTMENT - SQL METHOD")
    print("="*70)

    # Use same database for splits if not specified
    if splits_db is None:
        splits_db = source_db

    # Step 1: Copy database
    print(f"\n[1/4] Copying database...")
    print(f"  Source: {source_db}")
    print(f"  Output: {output_db}")

    import shutil
    shutil.copy2(source_db, output_db)
    print("  ✓ Database copied")

    # Step 2: Load splits from database
    print(f"\n[2/4] Loading splits from database...")
    splits = load_splits_from_db(splits_db)

    if not splits:
        print("  ⚠ No splits found in database")
        print("  ℹ Run load_splits_to_db.py first to import splits from CSV")
        return

    print(f"  ✓ Loaded splits for {len(splits)} symbols")

    # Step 3: Connect to output database
    print(f"\n[3/4] Connecting to output database...")
    conn = sqlite3.connect(output_db)
    cursor = conn.cursor()
    print("  ✓ Connected")

    # Step 4: Apply adjustments
    print(f"\n[4/4] Applying adjustments via SQL...")

    total_bars_adjusted = 0

    for symbol, events in splits.items():
        # Sort by date (newest first) for forward adjustment
        events.sort(key=lambda x: x['effective_date'], reverse=True)

        print(f"\n  {symbol}:")

        for event in events:
            effective_date = event['effective_date']
            factor = event['factor']
            date_str = datetime.fromtimestamp(effective_date / 1000, tz=timezone.utc).strftime('%Y-%m-%d')

            # Apply adjustment to all bars BEFORE split
            # Factor = to_shares / from_shares
            # Divide historical prices, multiply historical volume
            cursor.execute("""
                UPDATE bars
                SET 
                    open = open / ?,
                    high = high / ?,
                    low = low / ?,
                    close = close / ?,
                    volume = volume * ?
                WHERE symbol = ? AND datetime < ?
            """, (factor, factor, factor, factor, factor, symbol, effective_date))

            rows_affected = cursor.rowcount
            total_bars_adjusted += rows_affected

            print(f"    {date_str}: {event['from_shares']:.2f}→{event['to_shares']:.2f} "
                  f"(factor={factor:.2f}x) → {rows_affected:,} bars")

    # Commit changes
    conn.commit()

    print(f"\n{'='*70}")
    print(f"✓ ADJUSTMENT COMPLETE")
    print(f"{'='*70}")
    print(f"  Total bars adjusted: {total_bars_adjusted:,}")
    print(f"  Output database: {output_db}")
    print(f"\n  Next steps:")
    print(f"    1. Validate: python utils/validate_adjustments.py {output_db}")
    print(f"    2. Update config.py to use: {output_db}")
    print(f"    3. Re-run backtests")

    conn.close()


def load_splits_from_db(db_path: str) -> dict:
    """
    Load splits from database

    Returns:
        {symbol: [{'effective_date': ms, 'factor': float, 'from_shares': float, 'to_shares': float}, ...]}
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Check if splits table exists
    cursor.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name='splits'
    """)

    if not cursor.fetchone():
        print("  ⚠ 'splits' table not found in database")
        conn.close()
        return {}

    cursor.execute("""
        SELECT symbol, effective_date, from_shares, to_shares, factor
        FROM splits
        ORDER BY symbol, effective_date
    """)

    splits = {}
    for symbol, effective_date, from_shares, to_shares, factor in cursor.fetchall():
        if symbol not in splits:
            splits[symbol] = []

        splits[symbol].append({
            'effective_date': effective_date,
            'from_shares': from_shares,
            'to_shares': to_shares,
            'factor': factor
        })

    conn.close()
    return splits


def validate_adjustments(adjusted_db: str, splits_db: str = None):
    """
    Validate adjustments by checking price continuity around split dates
    """
    if splits_db is None:
        splits_db = adjusted_db

    print("\n" + "="*70)
    print("VALIDATION: Price Continuity Around Split Dates")
    print("="*70)

    conn_bars = sqlite3.connect(adjusted_db)
    conn_splits = sqlite3.connect(splits_db)

    cursor_bars = conn_bars.cursor()
    cursor_splits = conn_splits.cursor()

    # Get all splits
    cursor_splits.execute("SELECT symbol, effective_date, factor, from_shares, to_shares FROM splits")

    issues_found = 0

    for symbol, split_date, factor, from_shares, to_shares in cursor_splits.fetchall():
        # Get bar right before split
        cursor_bars.execute("""
            SELECT datetime, close, volume
            FROM bars
            WHERE symbol = ? AND datetime < ?
            ORDER BY datetime DESC
            LIMIT 1
        """, (symbol, split_date))

        before = cursor_bars.fetchone()

        # Get bar at or after split
        cursor_bars.execute("""
            SELECT datetime, close, volume
            FROM bars
            WHERE symbol = ? AND datetime >= ?
            ORDER BY datetime ASC
            LIMIT 1
        """, (symbol, split_date))

        after = cursor_bars.fetchone()

        if not before or not after:
            continue

        before_dt, before_price, before_vol = before
        after_dt, after_price, after_vol = after

        # Check price continuity (should be similar after adjustment)
        price_change_pct = abs((after_price - before_price) / before_price * 100)

        before_date = datetime.fromtimestamp(before_dt / 1000, tz=timezone.utc).date()
        after_date = datetime.fromtimestamp(after_dt / 1000, tz=timezone.utc).date()
        split_date_formatted = datetime.fromtimestamp(split_date / 1000, tz=timezone.utc).date()

        status = "✓" if price_change_pct < 5 else "⚠"

        print(f"\n{status} {symbol} ({from_shares:.0f}:{to_shares:.0f} split on {split_date_formatted}):")
        print(f"    Before ({before_date}): ${before_price:.2f}")
        print(f"    After ({after_date}): ${after_price:.2f}")
        print(f"    Change: {price_change_pct:.2f}%")

        if price_change_pct > 5:
            print(f"    ⚠ WARNING: Large gap suggests adjustment error or missing data")
            issues_found += 1

    print(f"\n{'='*70}")
    if issues_found == 0:
        print("✓ All splits validated - no major issues detected")
    else:
        print(f"⚠ Found {issues_found} potential issues - review above")

    conn_bars.close()
    conn_splits.close()


# ============================================================================
# Main Execution
# ============================================================================

if __name__ == "__main__":
    import sys

    # Paths
    SOURCE_DB = "../db/us_market_1min.sqlite"
    OUTPUT_DB = "../db/us_market_1min_adjusted.sqlite"

    print("\nCORPORATE ACTION ADJUSTMENT SCRIPT (SQL)")
    print("="*70)

    # Check source database
    if not Path(SOURCE_DB).exists():
        print(f"✗ Source database not found: {SOURCE_DB}")
        sys.exit(1)

    # Check if splits table exists in source
    conn = sqlite3.connect(SOURCE_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='splits'")
    has_splits = cursor.fetchone() is not None
    conn.close()

    if not has_splits:
        print(f"\n✗ 'splits' table not found in {SOURCE_DB}")
        print(f"\nRun this first to load splits from CSV:")
        print(f"  python utils/load_splits_to_db.py")
        sys.exit(1)

    # Confirm
    print(f"\nConfiguration:")
    print(f"  Source DB:  {SOURCE_DB}")
    print(f"  Output DB:  {OUTPUT_DB}")

    if Path(OUTPUT_DB).exists():
        print(f"\n⚠ WARNING: {OUTPUT_DB} already exists and will be overwritten!")

    confirm = input("\nProceed? (yes/no): ")

    if confirm.lower() != 'yes':
        print("Aborted.")
        sys.exit(0)

    # Run
    create_adjusted_database(SOURCE_DB, OUTPUT_DB, SOURCE_DB)

    # Validate
    validate_adjustments(OUTPUT_DB, SOURCE_DB)

    print("\n✓ Complete!")
    print(f"\n  Update config.py:")
    print(f"    SQLITE_CONFIG['path'] = '{OUTPUT_DB}'")