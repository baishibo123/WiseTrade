"""
Split adjustment as a derived view over immutable raw bars.

The bars table is never modified. Splits are stored as published, a small
interval table records the cumulative factor in force over each span of time,
and `bars_adjusted` is a VIEW joining the two:

    splits ──► adj_factors ──┐
                             ├──► bars_adjusted (VIEW) ──► DatabaseFeed
    bars (immutable) ────────┘

Why a view rather than a second copy of the data:

- Double-adjustment is impossible. There is no mutable adjusted state to apply
  a factor to twice, so re-running is idempotent by construction rather than
  by discipline.
- Splits are rare per symbol -- NVDA has four in its entire history -- so the
  cumulative factor is a step function with a handful of steps. Storing the
  steps costs ~2 rows per symbol instead of a duplicate bar table.
- No copy, no billion-row UPDATE, no 2x disk.

Materialising the view later is a pure performance change with no API change,
so that decision can wait for a measurement.

Convention (verified against the data, not the vendor doc): raw prices are
UNADJUSTED, factor = to_shares / from_shares, and bars *before* a split are
divided by it while volume is multiplied. NVDA 2024-06-07 closed at 1206 and
2024-06-10 opened at 120.2 across a 1:10 split; 1206/10 = 120.6. The provider's
`美股复权计算方法.md` states `前复权价格 = 原始价格 × 累计因子`, which is the
wrong operator for a factor defined this way -- the data settles it.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo


# Sentinels for the open ends of the first and last interval.
TIME_MIN = 0
TIME_MAX = 9_000_000_000_000  # year 2255, comfortably past any bar

EXCHANGE_TZ = ZoneInfo("America/New_York")


SPLITS_SCHEMA = """
CREATE TABLE IF NOT EXISTS splits (
    symbol      TEXT NOT NULL,
    ex_date     TEXT NOT NULL,          -- YYYY-MM-DD exactly as published
    from_shares REAL NOT NULL,
    to_shares   REAL NOT NULL,
    -- The ratio is part of the key, not just (symbol, ex_date). splits.csv
    -- contains 15 symbols with two rows on one date, and they are not all the
    -- same kind of thing: CZFS 2014-06-18 has 100->101 and 1->1.01, which are
    -- the same 1.01 ratio written two ways, while MDRR 2024-07-03 has 1->5 and
    -- 10->1, which is a forward split and a reverse on the same day. Keying on
    -- (symbol, ex_date) alone would have silently kept whichever row the CSV
    -- listed last -- an arbitrary choice between factor 5 and factor 0.1.
    -- Keeping the ratio preserves the information; build_adj_factors warns on
    -- same-date collisions and validate_adjustments catches any that are wrong.
    PRIMARY KEY (symbol, ex_date, from_shares, to_shares)
);
"""

ADJ_FACTORS_SCHEMA = """
CREATE TABLE IF NOT EXISTS adj_factors (
    symbol     TEXT    NOT NULL,
    valid_from INTEGER NOT NULL,        -- inclusive, Unix ms UTC
    valid_to   INTEGER NOT NULL,        -- exclusive, Unix ms UTC
    cum_factor REAL    NOT NULL,        -- divide price by this, multiply volume
    PRIMARY KEY (symbol, valid_from)
);
"""

# INNER JOIN, deliberately. build_adj_factors() emits full coverage for every
# symbol present in bars, so nothing can be dropped -- and if a coverage bug
# ever did appear, losing bars fails loudly, whereas a LEFT JOIN with
# COALESCE(factor, 1.0) would quietly serve unadjusted prices instead.
ADJUSTED_VIEW = """
DROP VIEW IF EXISTS bars_adjusted;
CREATE VIEW bars_adjusted AS
SELECT
    b.symbol                  AS symbol,
    b.datetime                AS datetime,
    b.open   / f.cum_factor   AS open,
    b.high   / f.cum_factor   AS high,
    b.low    / f.cum_factor   AS low,
    b.close  / f.cum_factor   AS close,
    b.volume * f.cum_factor   AS volume
FROM bars b
JOIN adj_factors f
  ON  f.symbol    = b.symbol
  AND b.datetime >= f.valid_from
  AND b.datetime <  f.valid_to;
"""


def adjustment_boundary_ms(ex_date: str | date) -> int:
    """
    The instant at which post-split pricing begins, as Unix ms UTC.

    Midnight in the exchange's own timezone, not UTC. A split is effective from
    the start of the ex-date's trading day -- pre-market quotes on that date
    already arrive adjusted -- so any instant inside the overnight gap is
    correct, and exchange-local midnight sits squarely in it (the session runs
    roughly 04:00-20:00 ET).

    Midnight UTC, which this replaces, is 19:00 or 20:00 ET on the *previous*
    day: inside the prior session. That left the final bar or two before a
    split unadjusted, a discontinuity exactly one factor wide.

    zoneinfo handles DST, so the UTC offset is right in both halves of the year.
    """
    if isinstance(ex_date, str):
        ex_date = date.fromisoformat(ex_date.strip())
    midnight_et = datetime(ex_date.year, ex_date.month, ex_date.day, tzinfo=EXCHANGE_TZ)
    return int(midnight_et.timestamp() * 1000)


def build_adj_factors(conn: sqlite3.Connection, as_of_ms: Optional[int] = None) -> int:
    """
    Rebuild adj_factors from splits. Returns the number of interval rows.

    Idempotent: the table is dropped and rebuilt, so running twice is identical
    to running once.

    as_of_ms bounds which splits count. splits.csv carries announced but
    not-yet-effective splits (DPU 2026-12-17, SOXX 2026-11-05); every bar
    predates those, so without this filter each would be applied to a symbol's
    entire history. Returns are unaffected by a uniform divisor, but price
    levels would be wrong -- and price level drives min_trade_size and share
    sizing. Defaults to now.
    """
    if as_of_ms is None:
        as_of_ms = int(datetime.now(EXCHANGE_TZ).timestamp() * 1000)

    conn.executescript(SPLITS_SCHEMA + ADJ_FACTORS_SCHEMA)
    conn.execute("DELETE FROM adj_factors")

    symbols = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM bars")]
    rows: list[tuple] = []
    skipped_future = 0

    for symbol in symbols:
        events = []
        for ex_date, from_sh, to_sh in conn.execute(
            "SELECT ex_date, from_shares, to_shares FROM splits WHERE symbol = ?", (symbol,)
        ):
            if not from_sh or not to_sh:
                logging.warning(f"{symbol} {ex_date}: degenerate split {from_sh}->{to_sh}, skipped")
                continue
            boundary = adjustment_boundary_ms(ex_date)
            if boundary > as_of_ms:
                skipped_future += 1
                continue
            events.append((boundary, to_sh / from_sh))

        # Two corporate actions on one date must collapse into ONE interval
        # boundary, with their factors multiplied. Leaving them as separate
        # events produced two intervals sharing a valid_from and a zero-width
        # span between them -- caught by the adj_factors primary key.
        #
        # The product is right for a genuine same-day forward+reverse pair
        # (MDRR 1->5 and 10->1) and wrong for one ratio written two ways
        # (CZFS 100->101 and 1->1.01, both 1.01). Only a human can tell those
        # apart, so this warns and validate_adjustments() is the backstop: if
        # the compound is wrong, the continuity check at that boundary fails.
        merged: dict[int, float] = {}
        counts: dict[int, int] = {}
        for boundary, f in events:
            merged[boundary] = merged.get(boundary, 1.0) * f
            counts[boundary] = counts.get(boundary, 0) + 1
        for boundary, count in counts.items():
            if count > 1:
                logging.warning(
                    f"{symbol}: {count} splits share ex-date "
                    f"{datetime.fromtimestamp(boundary / 1000, EXCHANGE_TZ).date()}; "
                    f"compounded to factor {merged[boundary]:g}. Verify against the validator."
                )

        events = sorted(merged.items())

        # Walk backwards from the newest split. A bar is divided by the product
        # of every split strictly after it, so the factor for the span before
        # boundary i is (factor i) x (factor of everything after i). Emitting
        # a covering interval even when there are no splits is what lets the
        # view use an INNER JOIN safely.
        cum = 1.0
        edges = [TIME_MIN] + [b for b, _ in events] + [TIME_MAX]
        factors = []
        for _, f in reversed(events):
            factors.append(cum)
            cum *= f
        factors.append(cum)
        factors.reverse()  # factors[i] applies to [edges[i], edges[i+1])

        for i, cf in enumerate(factors):
            rows.append((symbol, edges[i], edges[i + 1], cf))

    conn.executemany(
        "INSERT INTO adj_factors (symbol, valid_from, valid_to, cum_factor) VALUES (?,?,?,?)", rows
    )
    conn.executescript(ADJUSTED_VIEW)
    conn.commit()

    if skipped_future:
        logging.info(f"Ignored {skipped_future} split(s) dated after as_of; they are not yet effective.")
    logging.info(f"adj_factors: {len(rows)} intervals across {len(symbols)} symbols")
    return len(rows)


def validate_adjustments(conn: sqlite3.Connection, tolerance: float = 0.25) -> list[str]:
    """
    Check the adjusted view. Returns human-readable failures; empty means clean.

    The decisive test is continuity across each split boundary. On adjusted
    data the only thing left at the seam is the ordinary overnight gap, so the
    ratio of the first adjusted close after the boundary to the last one before
    should be ~1. Every failure mode has its own signature:

        ratio ~ 1          correct
        ratio ~ 1/factor   adjustment never applied
        ratio ~ factor     applied twice (or applied in the wrong direction)
        ratio ~ 1/factor^2 applied twice in one pass

    Plus three cheap invariants: every bar covered by exactly one interval, no
    non-positive adjusted price, and factors non-increasing as time advances.
    """
    problems: list[str] = []

    covered, total = conn.execute(
        "SELECT (SELECT COUNT(*) FROM bars_adjusted), (SELECT COUNT(*) FROM bars)"
    ).fetchone()
    if covered != total:
        problems.append(
            f"coverage: bars_adjusted has {covered:,} rows but bars has {total:,} "
            f"({total - covered:,} bars fall outside every adj_factors interval)"
        )

    n_bad = conn.execute("SELECT COUNT(*) FROM bars_adjusted WHERE low <= 0 OR close <= 0").fetchone()[0]
    if n_bad:
        problems.append(f"{n_bad:,} adjusted bars have a non-positive price")

    for symbol, ex_date, from_sh, to_sh in conn.execute(
        "SELECT s.symbol, s.ex_date, s.from_shares, s.to_shares FROM splits s "
        "WHERE s.symbol IN (SELECT DISTINCT symbol FROM bars) ORDER BY s.symbol, s.ex_date"
    ):
        boundary = adjustment_boundary_ms(ex_date)
        before = conn.execute(
            "SELECT close FROM bars_adjusted WHERE symbol=? AND datetime < ? "
            "ORDER BY datetime DESC LIMIT 1", (symbol, boundary)).fetchone()
        after = conn.execute(
            "SELECT close FROM bars_adjusted WHERE symbol=? AND datetime >= ? "
            "ORDER BY datetime ASC LIMIT 1", (symbol, boundary)).fetchone()
        if not before or not after or not before[0]:
            continue  # split outside the loaded date range

        factor = to_sh / from_sh
        ratio = after[0] / before[0]
        if abs(ratio - 1.0) > tolerance:
            if abs(ratio - 1.0 / factor) < tolerance:
                diagnosis = "looks UNADJUSTED"
            elif abs(ratio - factor) < tolerance:
                diagnosis = "looks DOUBLE-ADJUSTED"
            else:
                diagnosis = "unexplained discontinuity"
            problems.append(
                f"{symbol} {ex_date} ({from_sh:g}->{to_sh:g}, factor {factor:g}): "
                f"close {before[0]:.4f} -> {after[0]:.4f}, ratio {ratio:.4f} -- {diagnosis}"
            )

    return problems
