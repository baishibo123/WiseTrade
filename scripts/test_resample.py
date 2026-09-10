"""
Acceptance tests for `database/resample.py` and the `/bars` routes.

Run:  python scripts/test_resample.py

Plain asserts and a `main()` rather than pytest, because the repo declares no
test tooling and `tests/` is gitignored -- these are acceptance criteria and have
to survive a fresh clone.

Several of these exist because the failure they catch renders as a perfectly
normal-looking chart. Where a test hardcodes a number, that number is
hand-computed from the exchange clock via `zoneinfo`, not read back out of the
code under test.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import SQLITE_DB_PATH                                    # noqa: E402
from database.resample import (                                       # noqa: E402
    PERIODS,
    aggregate_bars,
    bucket_end_for,
    bucket_span,
    count_buckets,
    n_expected,
    period_minutes,
    plan_window,
    session_bucket_ends,
)
from database.sessions import TradingCalendar                         # noqa: E402

ET = ZoneInfo("America/New_York")

NORMAL_DAY = date(2024, 1, 2)      # 390 RTH minutes
GAPPY_DAY = date(2024, 1, 4)       # AKAM has 346 of 390
HALF_DAY = date(2024, 7, 3)        # closes 13:00 ET, 210 RTH minutes
GOOG_GAP_DAY = date(2025, 9, 24)   # GOOG has no bars at all

_failures: list[str] = []
_checks = 0


def check(cond: bool, what: str) -> None:
    global _checks
    _checks += 1
    if not cond:
        _failures.append(what)
        print(f"  FAIL  {what}")


def et_ms(d: date, hh: int, mm: int) -> int:
    """A wall-clock ET time as Unix ms UTC -- the independent clock for this file."""
    return int(datetime(d.year, d.month, d.day, hh, mm, tzinfo=ET).timestamp() * 1000)


def session(cal: TradingCalendar, d: date):
    s = cal.session_for(et_ms(d, 12, 0))
    assert s is not None and s.et_date == d, f"no session for {d}"
    return s


# ---------------------------------------------------------------------------
# 1. Boundaries, hand-computed
# ---------------------------------------------------------------------------

def test_boundaries(cal: TradingCalendar) -> None:
    print("\n1. bucket boundaries, hand-computed from the exchange clock")

    normal = session(cal, NORMAL_DAY)
    half = session(cal, HALF_DAY)

    check(normal.rth_open_ms == et_ms(NORMAL_DAY, 9, 30), "normal session opens 09:30 ET")
    check(normal.rth_close_ms == et_ms(NORMAL_DAY, 16, 0), "normal session closes 16:00 ET")
    check(half.rth_close_ms == et_ms(HALF_DAY, 13, 0), "half day closes 13:00 ET")
    check(half.is_half_day, "half day is flagged")

    # A period that divides the session evenly: 390 / 30 == 13, every bucket full.
    ends = session_bucket_ends(normal, "30m")
    check(len(ends) == 13, f"30m on a 390-minute session gives 13 buckets, got {len(ends)}")
    check(ends[0] == et_ms(NORMAL_DAY, 10, 0), "first 30m bucket ends 10:00 ET")
    check(ends[-1] == et_ms(NORMAL_DAY, 16, 0), "last 30m bucket ends 16:00 ET")
    check(all(n_expected(e, normal, "30m") == 30 for e in ends), "every 30m bucket expects 30 bars")

    # A period that does not: 390 / 60 == 6.5. Close-anchored, so the short
    # bucket is the session's FIRST and the last still ends exactly at the close.
    ends = session_bucket_ends(normal, "1h")
    check(len(ends) == 7, f"1h on a 390-minute session gives 7 buckets, got {len(ends)}")
    check(ends[0] == et_ms(NORMAL_DAY, 10, 0), "first 1h bucket ends 10:00 ET, not 10:30")
    check(ends[-1] == et_ms(NORMAL_DAY, 16, 0), "last 1h bucket ends 16:00 ET exactly")
    check(n_expected(ends[0], normal, "1h") == 30, "first 1h bucket expects 30 bars (09:30-10:00)")
    check(n_expected(ends[-1], normal, "1h") == 60, "last 1h bucket expects a full 60")
    check(bucket_span(ends[0], normal, "1h")[0] == normal.rth_open_ms,
          "the first bucket's span is clamped to the open, not end-minus-period")

    # 2h: 390 / 120 == 3.25 -> 4 buckets, first one 30 minutes.
    ends = session_bucket_ends(normal, "2h")
    check(len(ends) == 4, f"2h on a normal session gives 4 buckets, got {len(ends)}")
    check(n_expected(ends[0], normal, "2h") == 30, "first 2h bucket expects 30 bars")

    # The half day. The short bucket's width is a property of the session, not
    # of the period: 30 minutes at 1h but 90 at 2h.
    ends = session_bucket_ends(half, "1h")
    check(len(ends) == 4, f"1h on a 210-minute session gives 4 buckets, got {len(ends)}")
    check(ends[-1] == et_ms(HALF_DAY, 13, 0), "last 1h bucket of the half day ends 13:00 ET")
    check(n_expected(ends[0], half, "1h") == 30, "first 1h bucket of the half day expects 30")

    ends = session_bucket_ends(half, "2h")
    check(len(ends) == 2, f"2h on a 210-minute session gives 2 buckets, got {len(ends)}")
    check(ends[0] == et_ms(HALF_DAY, 11, 0), "first 2h bucket of the half day ends 11:00 ET")
    check(n_expected(ends[0], half, "2h") == 90, "first 2h bucket of the half day expects 90")

    # 1d is one session, and the half day proves it is not a fixed 390.
    check(session_bucket_ends(normal, "1d") == [normal.rth_close_ms], "1d is one bucket per session")
    check(n_expected(normal.rth_close_ms, normal, "1d") == 390, "1d expects 390 on a normal session")
    check(n_expected(half.rth_close_ms, half, "1d") == 210, "1d expects 210 on a half day")

    # The property that catches a clamp error or an off-by-one in the grid,
    # across the whole vocabulary and both session shapes.
    for s in (normal, half):
        span_min = (s.rth_close_ms - s.rth_open_ms) // 60_000
        for p in PERIODS:
            total = sum(n_expected(e, s, p) for e in session_bucket_ends(s, p))
            check(total == span_min,
                  f"sum(n_expected) == {span_min} for {p} on {s.et_date}, got {total}")

    # Extended hours have no theoretical count at all.
    check(n_expected(normal.rth_close_ms, normal, "1h", rth=False) is None,
          "n_expected is None when rth=False")

    # The one-sided 1d invariant: a period longer than the session is free, but a
    # shorter one splits the session into two daily bars and renders as a normal
    # chart, so it must be loud.
    import database.resample as rs
    saved = rs.PERIOD_MINUTES["1d"]
    rs.PERIOD_MINUTES["1d"] = 300          # shorter than a 390-minute session
    try:
        session_bucket_ends(normal, "1d")
        check(False, "a 1d period shorter than the session must raise, not split silently")
    except ValueError:
        check(True, "a 1d period shorter than the session raises")
    finally:
        rs.PERIOD_MINUTES["1d"] = saved

    # bucket_end_for refuses a bar outside the session: Python's // floors and
    # SQLite's / truncates, so they only agree while the numerator is >= 0.
    for ts, why in ((normal.rth_open_ms, "a bar stamped exactly at the open (excluded bound)"),
                    (normal.rth_close_ms + 60_000, "a bar after the close")):
        try:
            bucket_end_for(ts, normal, "5m")
            check(False, f"bucket_end_for must refuse {why}")
        except ValueError:
            check(True, f"bucket_end_for refuses {why}")


# ---------------------------------------------------------------------------
# 2. The SQL agrees with the pure function on real data
# ---------------------------------------------------------------------------

def py_aggregate(conn, symbol: str, s, period: str, t0: int, t1: int, rth: bool) -> dict:
    """
    Independent aggregation in Python from the 1-minute rows.

    Groups with `bucket_end_for` -- the pure half of the shared formula -- and
    folds OHLCV by hand, so a disagreement with the SQL is a real disagreement
    and not two views of the same expression.
    """
    rows = conn.execute(
        "SELECT datetime, open, high, low, close, volume FROM bars_adjusted "
        "WHERE symbol = ? AND datetime > ? AND datetime <= ? ORDER BY datetime",
        (symbol, t0, t1),
    ).fetchall()
    out: dict[int, dict] = {}
    for dt, o, h, lo, c, v in rows:
        b = out.setdefault(bucket_end_for(dt, s, period, rth), {
            "open": o, "high": h, "low": lo, "close": c, "volume": 0.0, "n_src": 0})
        b["high"] = max(b["high"], h)
        b["low"] = min(b["low"], lo)
        b["close"] = c            # rows are ordered, so the last one wins
        b["volume"] += v
        b["n_src"] += 1
    return out


def test_sql_matches_python(conn, cal: TradingCalendar) -> None:
    print("\n2. SQL grouping == pure-Python grouping, on real bars")

    combos = [
        ("AAPL", NORMAL_DAY, "1m", True), ("AAPL", NORMAL_DAY, "5m", True),
        ("AAPL", NORMAL_DAY, "1h", True), ("AAPL", NORMAL_DAY, "2h", True),
        ("AAPL", NORMAL_DAY, "1d", True), ("AAPL", HALF_DAY, "1h", True),
        ("AAPL", HALF_DAY, "2h", True),   ("AKAM", GAPPY_DAY, "5m", True),
        ("AKAM", GAPPY_DAY, "15m", True), ("AAPL", NORMAL_DAY, "30m", False),
        ("AAPL", NORMAL_DAY, "1d", False),
    ]
    for symbol, d, period, rth in combos:
        s = session(cal, d)
        lo, hi = ((s.rth_open_ms, s.rth_close_ms) if rth
                  else (s.ext_open_ms - 60_000, s.ext_close_ms))
        bars, _ = aggregate_bars(conn, symbol, lo, hi, period, rth=rth, cal=cal)
        ref = py_aggregate(conn, symbol, s, period, lo, hi, rth)
        tag = f"{symbol} {d} {period} rth={rth}"

        check(set(b["t_ms"] for b in bars) == set(ref), f"{tag}: identical bucket set")
        ok = True
        for b in bars:
            r = ref.get(b["t_ms"])
            if r is None:
                ok = False
                break
            ok &= (b["open"] == r["open"] and b["high"] == r["high"]
                   and b["low"] == r["low"] and b["close"] == r["close"]
                   and b["n_src"] == r["n_src"]
                   and abs(b["volume"] - r["volume"]) < 1e-6 * max(1.0, abs(r["volume"])))
        check(ok, f"{tag}: identical OHLCV and n_src in every bucket ({len(bars)} buckets)")


# ---------------------------------------------------------------------------
# 3. OHLCV against a bucket checked row by row
# ---------------------------------------------------------------------------

def test_ohlcv_by_hand(conn, cal: TradingCalendar) -> None:
    print("\n3. OHLCV against the five source rows of one bucket, printed")

    s = session(cal, NORMAL_DAY)
    end = et_ms(NORMAL_DAY, 9, 35)          # the session's first 5m bucket
    src = conn.execute(
        "SELECT datetime, open, high, low, close, volume FROM bars_adjusted "
        "WHERE symbol = 'AAPL' AND datetime > ? AND datetime <= ? ORDER BY datetime",
        (et_ms(NORMAL_DAY, 9, 30), end),
    ).fetchall()
    for dt, o, h, lo, c, v in src:
        print(f"     {datetime.fromtimestamp(dt/1000, ET):%H:%M}  "
              f"O={o:.4f} H={h:.4f} L={lo:.4f} C={c:.4f} V={v:.0f}")

    check(len(src) == 5, f"the 09:35 bucket has five source bars, got {len(src)}")
    bars, _ = aggregate_bars(conn, "AAPL", s.rth_open_ms, end, "5m", cal=cal)
    check(len(bars) == 1, f"one bucket returned, got {len(bars)}")
    b = bars[0]
    print(f"     -> t_ms={b['t_ms']} O={b['open']:.4f} H={b['high']:.4f} "
          f"L={b['low']:.4f} C={b['close']:.4f} V={b['volume']:.0f} "
          f"n_src={b['n_src']}/{b['n_expected']}")

    check(b["t_ms"] == end, "t_ms is the bucket END (09:35), the eob stamp")
    check(b["open"] == src[0][1], "open is the FIRST source bar's open, not the min or the low")
    check(b["close"] == src[-1][4], "close is the LAST source bar's close")
    check(b["high"] == max(r[2] for r in src), "high is the max of the source highs")
    check(b["low"] == min(r[3] for r in src), "low is the min of the source lows")
    check(abs(b["volume"] - sum(r[5] for r in src)) < 1e-6, "volume is the sum of source volumes")
    check(b["open"] != b["low"] or b["close"] != b["high"],
          "open/close are not accidentally equal to low/high on this bucket")


# ---------------------------------------------------------------------------
# 4. n_src < n_expected on real gaps
# ---------------------------------------------------------------------------

def test_incomplete_buckets(conn, cal: TradingCalendar) -> None:
    print("\n4. incomplete buckets on verified real gaps")

    # A partial gap: AKAM 2024-01-04 has 346 of 390 RTH minutes.
    s = session(cal, GAPPY_DAY)
    bars, _ = aggregate_bars(conn, "AKAM", s.rth_open_ms, s.rth_close_ms, "5m", cal=cal)
    short = [b for b in bars if b["n_src"] < b["n_expected"]]
    total = sum(b["n_src"] for b in bars)
    print(f"     AKAM {GAPPY_DAY}: {total} of 390 minutes, "
          f"{len(short)} of {len(bars)} 5m buckets incomplete, "
          f"sparsest n_src={min(b['n_src'] for b in bars)}")
    check(total == 346, f"AKAM {GAPPY_DAY} has 346 RTH bars, got {total}")
    check(len(short) > 0, "at least one 5m bucket reports n_src < n_expected")
    check(all(b["n_expected"] == 5 for b in bars), "every 5m bucket expects 5 regardless of what arrived")
    check(min(b["n_src"] for b in bars) == 1, "the sparsest bucket was built from a single minute")

    # n_expected must be theoretical. sessions.n_bars_rth is an observed count
    # and would make the check vacuous.
    check(s.n_bars_rth == 390 and total == 346,
          "n_expected is not derived from sessions.n_bars_rth (390 observed vs 346 for this symbol)")

    # A whole missing session: GOOG 2025-09-24. Buckets with no source bars are
    # absent rather than zero-filled -- Bar's OHLC is non-nullable.
    s = session(cal, GOOG_GAP_DAY)
    bars, _ = aggregate_bars(conn, "GOOG", s.rth_open_ms, s.rth_close_ms, "5m", cal=cal)
    check(bars == [], f"GOOG {GOOG_GAP_DAY} returns no bars at all, got {len(bars)}")
    check(cal.session_for(s.rth_close_ms) is not None,
          "the session still exists, so /sessions can distinguish this from a weekend")
    day = et_ms(GOOG_GAP_DAY, 0, 0)
    bars, _ = aggregate_bars(conn, "GOOG", day - 4 * 86_400_000, day + 4 * 86_400_000, "1d", cal=cal)
    check(all(b["t_ms"] != s.rth_close_ms for b in bars),
          "the missing session contributes no daily bar either")
    check(len(bars) > 0, "surrounding daily bars are unaffected")


# ---------------------------------------------------------------------------
# 5. Nothing from outside the requested range
# ---------------------------------------------------------------------------

def test_range_containment(conn, cal: TradingCalendar) -> None:
    print("\n5. no bar and no bucket from outside (t0_ms, t1_ms]")

    s = session(cal, NORMAL_DAY)
    # A range that cuts mid-session at both ends and lands off the grid on purpose.
    t0, t1 = et_ms(NORMAL_DAY, 11, 3), et_ms(NORMAL_DAY, 14, 47)
    for period in ("5m", "15m", "1h"):
        bars, _ = aggregate_bars(conn, "AAPL", t0, t1, period, cal=cal)
        w = plan_window(cal, t0, t1, period, True, None)
        inside = all(b.start_ms >= t0 and b.end_ms <= t1 for b in w.buckets)
        check(inside, f"{period}: every planned bucket's span is inside the range")
        check(all(t0 < b["t_ms"] <= t1 for b in bars), f"{period}: every t_ms is inside the range")
        # The aggregate itself must not have eaten a bar from outside: rebuild it
        # from only the rows in range and compare.
        ref = py_aggregate(conn, "AAPL", s, period, t0, t1, True)
        for b in bars:
            r = ref[b["t_ms"]]
            check(b["n_src"] == r["n_src"] and b["open"] == r["open"] and b["close"] == r["close"],
                  f"{period} bucket {b['t_ms']} built from in-range rows only")
        # A cut bucket is dropped, not truncated: the 1h grid has a bucket ending
        # 12:00 whose span starts 11:00, before t0, so it must be absent.
        if period == "1h":
            check(all(b["t_ms"] != et_ms(NORMAL_DAY, 12, 0) for b in bars),
                  "1h: the bucket cut by t0 is dropped, not returned partially filled")
            check(all(b["t_ms"] != et_ms(NORMAL_DAY, 15, 0) for b in bars),
                  "1h: the bucket cut by t1 is dropped, so no t_ms exceeds the cursor")

    # The replay case stated as the guarantee: nothing after the cursor, ever.
    cursor = et_ms(NORMAL_DAY, 11, 17)
    bars, _ = aggregate_bars(conn, "AAPL", s.rth_open_ms, cursor, "1h", cal=cal)
    check(all(b["t_ms"] <= cursor for b in bars),
          "no bucket ends after the replay cursor")
    check(bars and bars[-1]["t_ms"] == et_ms(NORMAL_DAY, 11, 0),
          "the last bar at an 11:17 cursor is the 11:00 bucket, and the forming one is withheld")


# ---------------------------------------------------------------------------
# 6. Paging partitions the grid: no hole, no duplicate
# ---------------------------------------------------------------------------

def test_paging(conn, cal: TradingCalendar) -> None:
    print("\n6. paging left partitions the grid exactly")

    t0, t1 = et_ms(date(2025, 9, 1), 0, 0), et_ms(date(2025, 10, 1), 0, 0)
    full, _ = aggregate_bars(conn, "AAPL", t0, t1, "5m", cal=cal)
    seen: list[int] = []
    cursor, pages = t1, 0
    while pages < 20:
        page, has_more = aggregate_bars(conn, "AAPL", t0, cursor, "5m", limit=200, cal=cal)
        if not page:
            break
        seen = [b["t_ms"] for b in page] + seen
        pages += 1
        cursor = page[0]["t_ms"] - 1        # strictly before the oldest bar held
        if not has_more:
            break
    check(seen == [b["t_ms"] for b in full],
          f"{pages} pages of 200 reassemble the {len(full)}-bar series exactly, got {len(seen)}")
    check(len(seen) == len(set(seen)), "no bucket is served twice across pages")
    n = count_buckets(conn, "AAPL", t0, t1, "5m", cal=cal)
    check(n == len(full), f"/bars/count ({n}) equals len(/bars) ({len(full)})")


# ---------------------------------------------------------------------------
# 7. Regressions from the /review pass
# ---------------------------------------------------------------------------

def test_review_regressions(conn, cal: TradingCalendar) -> None:
    print("\n7. regressions found by /review")

    # A non-positive limit used to return the whole grid, because spans[-0:] is
    # spans[0:], while simultaneously reporting has_more.
    for bad in (0, -1):
        try:
            plan_window(cal, et_ms(NORMAL_DAY, 0, 0), et_ms(NORMAL_DAY, 23, 0), "5m", True, bad)
            check(False, f"limit={bad} must raise, not return the entire grid")
        except ValueError:
            check(True, f"limit={bad} raises")

    # has_more must consider the symbol, not just the session grid: at the left
    # edge of a symbol's history there is nothing older to pan to.
    first = conn.execute("SELECT MIN(datetime) FROM bars WHERE symbol='AAPL'").fetchone()[0]
    s = cal.session_for(first)
    _, edge_more = aggregate_bars(conn, "AAPL", s.rth_open_ms, s.rth_close_ms, "1h", cal=cal)
    check(edge_more is False, "has_more is false on the oldest session AAPL has")
    mid = session(cal, HALF_DAY)
    _, mid_more = aggregate_bars(conn, "AAPL", mid.rth_open_ms, mid.rth_close_ms, "1h", cal=cal)
    check(mid_more is True, "has_more is true mid-history, where older bars really exist")

    # An empty sessions table must keep failing loudly rather than caching the
    # empty calendar and serving empty 200s from the second request onwards.
    try:
        import api.routers.bars as routes
        from fastapi import HTTPException
    except ImportError:
        print("     (fastapi absent -- skipping the router regression)")
        return
    empty = sqlite3.connect(":memory:")
    empty.executescript(
        "CREATE TABLE sessions (et_date TEXT PRIMARY KEY, ext_open_ms INT, rth_open_ms INT,"
        " rth_close_ms INT, ext_close_ms INT, n_bars_rth INT, is_half_day INT);"
    )
    saved, routes._CALENDAR = routes._CALENDAR, None
    try:
        raised = 0
        for _ in range(3):
            try:
                routes._calendar(empty)
            except HTTPException as e:
                raised += e.status_code == 503
        check(raised == 3, f"an empty sessions table raises 503 every time, got {raised}/3")
        check(routes._CALENDAR is None, "an empty calendar is never cached")
    finally:
        routes._CALENDAR = saved
        empty.close()


def main() -> int:
    if not SQLITE_DB_PATH.exists():
        print(f"bar database not found at {SQLITE_DB_PATH}\n"
              f"set WISETRADE_SQLITE_DB_PATH, or build it with utils/build_database.py",
              file=sys.stderr)
        return 2
    conn = sqlite3.connect(f"file:{SQLITE_DB_PATH}?mode=ro", uri=True)
    cal = TradingCalendar.load(conn)
    print(f"database: {SQLITE_DB_PATH}  ({len(cal)} sessions)")

    test_boundaries(cal)
    test_sql_matches_python(conn, cal)
    test_ohlcv_by_hand(conn, cal)
    test_incomplete_buckets(conn, cal)
    test_range_containment(conn, cal)
    test_paging(conn, cal)
    test_review_regressions(conn, cal)

    print(f"\n{_checks - len(_failures)}/{_checks} checks passed")
    if _failures:
        print(f"{len(_failures)} FAILED:")
        for f in _failures:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
