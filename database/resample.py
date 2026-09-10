"""
Aggregate 1-minute bars into the period vocabulary frozen in `docs/interfaces.md`.

    sessions table ──► TradingCalendar ──► [ plan_window ──► aggregate_bars ] ──► Bar[]
                                             pure Python      one SQL query

Two things are worth knowing before reading the code.

**Buckets are anchored on the session CLOSE, not the open.** A bucket is the
half-open interval `(end - period, end]`, clamped at the session open, so the
bucket that does not divide evenly is the session's *first* one, not its last.
Every session's final bucket therefore ends exactly at 16:00 (or 13:00), which
puts the closing auction inside a full bar rather than a stub. This is also the
only alignment consistent with the `n_expected` formula in `interfaces.md`,
which clamps at `rth_open_ms` and nowhere else. The choice is observable only at
`1h` and `2h`: every shorter period divides both 390 and 210 evenly, so both
alignments produce an identical grid. See ADR-025.

**`1d` is not a special case.** It is a period longer than any session, so the
session clamp collapses it to one bucket per session on its own -- half days
included, with no branch anywhere. What that buys in simplicity it pays for with
a one-sided invariant: a `1d` period *larger* than the longest session is free
(the join bounds already exclude everything outside the session, so a longer
period cannot reach further), while one *smaller* silently splits a session into
two daily bars and renders as a perfectly normal chart. `session_bucket_ends`
raises rather than allowing that. Measured headroom: the longest extended
session in the database spans 959 minutes against `1d`'s 1440.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

from .sessions import Session, TradingCalendar


MS_PER_MIN = 60_000

# The frozen vocabulary. Order is the resolution ladder, ascending.
PERIOD_MINUTES: dict[str, int] = {
    "1m": 1,
    "2m": 2,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "2h": 120,
    # Deliberately not the session length: that is 390 on a normal day and 210
    # on a half day, so any fixed number here would be wrong somewhere. 1440 is
    # "longer than any session", which is what makes the clamp do the work.
    "1d": 1440,
}

ONE_SESSION_PERIOD = "1d"

PERIODS: tuple[str, ...] = tuple(PERIOD_MINUTES)


def period_minutes(period: str) -> int:
    """`"5m"` -> 5. Raises on anything outside the frozen vocabulary."""
    try:
        return PERIOD_MINUTES[period]
    except KeyError:
        raise ValueError(
            f"unknown period {period!r}; expected one of {list(PERIODS)}"
        ) from None


def session_bounds(s: Session, rth: bool = True) -> tuple[int, int]:
    """
    The session's `(exclusive_lo, inclusive_hi]` envelope in Unix ms UTC.

    Half-open because bars are `eob`-stamped: the bar labelled 09:31 covers
    (09:30, 09:31], so 09:30 is excluded and 16:00 included -- exactly 390 bars.

    `ext_open_ms` is the first observed *bar* of the day, so the instant that bar
    opens is one minute earlier. Backing the bound off keeps `(lo, hi]` uniform
    across both regimes instead of silently dropping the session's first bar.
    """
    if rth:
        return s.rth_open_ms, s.rth_close_ms
    return s.ext_open_ms - MS_PER_MIN, s.ext_close_ms


def bucket_end_for(ts_ms: int, s: Session, period: str, rth: bool = True) -> int:
    """
    Which bucket a 1-minute bar belongs to, as that bucket's end stamp (`t_ms`).

    This is the pure-Python half of the "one formula, two implementations" pair;
    `_BUCKET_EXPR` below is the SQL half and must agree with it bar for bar.

    The precondition is load-bearing rather than defensive. Python's `//` floors
    and SQLite's `/` truncates toward zero (`-1 // 5 == -1` but `-1 / 5 == 0`),
    so the two implementations diverge the moment `hi - ts_ms` goes negative.
    The SQL can never see such a row -- the join bounds exclude it -- but this
    function is callable with anything, so it refuses instead of returning a
    bucket SQL would never produce.
    """
    lo, hi = session_bounds(s, rth)
    if not lo < ts_ms <= hi:
        raise ValueError(
            f"{ts_ms} is outside session {s.et_date} bounds ({lo}, {hi}] "
            f"for rth={rth}; a bar outside the session has no bucket"
        )
    p = period_minutes(period) * MS_PER_MIN
    return hi - (hi - ts_ms) // p * p


def session_bucket_ends(s: Session, period: str, rth: bool = True) -> list[int]:
    """Every bucket end in one session, ascending. The grid."""
    lo, hi = session_bounds(s, rth)
    p = period_minutes(period) * MS_PER_MIN
    n = -(-(hi - lo) // p)  # ceil, in integers
    if period == ONE_SESSION_PERIOD and n != 1:
        raise ValueError(
            f"{period!r} is {period_minutes(period)} min but session {s.et_date} "
            f"spans {(hi - lo) // MS_PER_MIN} min (rth={rth}), which would split it "
            f"into {n} buckets. '1d' must exceed the longest session span."
        )
    return [hi - k * p for k in range(n - 1, -1, -1)]


def bucket_span(bucket_end_ms: int, s: Session, period: str,
                rth: bool = True) -> tuple[int, int]:
    """
    A bucket's `(exclusive_start, inclusive_end]`, clamped at the session open.

    One definition, two users: `n_expected` measures this span, and
    `plan_window` tests containment against it. Deriving both from here is what
    keeps a bucket's reported completeness consistent with the reason it was
    included at all -- and it is why the clamped start is used rather than the
    nominal `end - period`, which for a session's first bucket reaches back
    before the open.
    """
    lo, _ = session_bounds(s, rth)
    p = period_minutes(period) * MS_PER_MIN
    return max(lo, bucket_end_ms - p), bucket_end_ms


def n_expected(bucket_end_ms: int, s: Session, period: str,
               rth: bool = True) -> Optional[int]:
    """
    How many 1-minute bars this bucket *should* contain; `None` when `rth` is off.

    Theoretical, never observed: it is the bucket's clamped span in minutes and
    must not be read from `sessions.n_bars_rth`, which is an ingest-time count
    that already reflects gaps. Deriving it from an observed count would make
    `n_src == n_expected` always and silently disable the completeness check.

    `None` for extended hours because coverage there varies by orders of
    magnitude between symbols (797 bars against 15 on the same day), so no
    theoretical count exists and flagging incompleteness would be noise.
    """
    if not rth:
        return None
    start, end = bucket_span(bucket_end_ms, s, period, rth)
    return (end - start) // MS_PER_MIN


# ---------------------------------------------------------------------------
# Window planning: the grid, paged, with the bar scan range derived from it
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Bucket:
    start_ms: int              # exclusive
    end_ms: int                # inclusive -- this is the Bar's `t_ms`
    n_expected: Optional[int]


@dataclass(frozen=True)
class Window:
    """What one `/bars` response will contain, decided before any bar is read."""
    buckets: list[Bucket]
    scan_from_ms: int          # exclusive; the earliest returned bucket's start
    scan_to_ms: int            # inclusive; the latest returned bucket's end
    has_more: bool             # older buckets exist that this response omits

    @property
    def ends(self) -> list[int]:
        return [b.end_ms for b in self.buckets]


def plan_window(cal: TradingCalendar, t0_ms: int, t1_ms: int, period: str,
                rth: bool = True, limit: Optional[int] = None) -> Window:
    """
    The buckets a request resolves to, and the bar range needed to build them.

    A bucket is returned iff its clamped span lies entirely within
    `(t0_ms, t1_ms]`. Both edges drop rather than truncate, which is what makes
    `t1_ms` a structural no-future bound: a bucket still forming at the replay
    cursor is simply absent, so no timestamp in the future reaches the payload
    and no bucket's `t_ms` shifts as the cursor advances. A `t_ms` that shifted
    would silently re-anchor any label stored against it (`interfaces.md`,
    `Label.t_from_ms`).

    Planning in Python before querying is not only about paging. `limit` narrows
    the *scan* range, not just the payload: a `1m` request over two years spans
    185,700 buckets, of which only the newest `limit` are read.
    """
    spans: list[tuple[int, int, Optional[int]]] = []
    for s in cal.sessions_in_range(t0_ms, t1_ms, regular_hours_only=rth):
        for end in session_bucket_ends(s, period, rth):
            start, end = bucket_span(end, s, period, rth)
            if start >= t0_ms and end <= t1_ms:
                spans.append((start, end, n_expected(end, s, period, rth)))
    spans.sort(key=lambda b: b[1])

    if limit is not None and limit < 1:
        # `spans[-0:]` is `spans[0:]`, so limit=0 would return the entire grid
        # while reporting that it had been truncated. The route already enforces
        # ge=1; direct callers get an error rather than the opposite of what
        # they asked for.
        raise ValueError(f"limit must be >= 1 or None, got {limit}")
    truncated = limit is not None and len(spans) > limit
    if truncated:
        spans = spans[-limit:]

    buckets = [Bucket(*b) for b in spans]
    if buckets:
        scan_from, scan_to = buckets[0].start_ms, buckets[-1].end_ms
        floor_ms = buckets[0].end_ms
    else:
        # Nothing to scan. Keep the range empty rather than degenerate, so an
        # empty plan cannot accidentally select a bar.
        scan_from = scan_to = t1_ms
        floor_ms = t1_ms + 1

    return Window(
        buckets=buckets,
        scan_from_ms=scan_from,
        scan_to_ms=scan_to,
        has_more=truncated or _has_older_bucket(cal, floor_ms, period, rth),
    )


def _has_older_bucket(cal: TradingCalendar, floor_ms: int, period: str,
                      rth: bool) -> bool:
    """
    Whether any bucket in the calendar ends strictly before `floor_ms`.

    This is what `has_more` reports: "can the client pan further left", which is
    the question a chart actually asks. It is deliberately broader than "the
    range held more buckets than `limit`" -- under the drop rule a bucket cut by
    `t0_ms` is also omitted, and the frozen response body has only `bars` and
    `has_more`, so this flag is the only channel that can say so.

    Walks sessions backwards and stops at the first hit, so it touches one or
    two sessions rather than building the whole grid for a boolean.
    """
    for s in reversed(cal.sessions_in_range(0, floor_ms, regular_hours_only=rth)):
        for end in reversed(session_bucket_ends(s, period, rth)):
            if end < floor_ms:
                return True
    return False


# ---------------------------------------------------------------------------
# The query
# ---------------------------------------------------------------------------

# The SQL half of the shared formula. Mirrors `bucket_end_for` exactly.
_BUCKET_EXPR = "{hi} - ({hi} - b.datetime) / :p_ms * :p_ms"

# `sessions` is the OUTER relation and `CROSS JOIN` is what pins it there.
#
# This is not a style choice. With `bars` outer, SQLite's plan is
# `SEARCH b USING INDEX / SCAN s` -- a 479-row scan of `sessions` for every
# single bar. Same text, same results, 167 ms against 4.2 ms measured on one
# symbol / 5m / one month. `CROSS JOIN` in SQLite means "do not reorder", so the
# fast plan is pinned rather than hoped for. See ADR-026, and the NOTE in
# `docs/principles.md`.
#
# Always `bars_adjusted`, never `bars`: raw prices are unadjusted and NVDA drops
# 10x on 2024-06-10. `bars_rth` is not usable here even though it filters the
# right rows, because it projects only OHLCV and the bucket expression needs
# `sessions.rth_close_ms`.
_SRC_CTE = """
SELECT """ + _BUCKET_EXPR + """ AS t_ms,
       b.datetime AS dt, b.open, b.high, b.low, b.close, b.volume
FROM sessions s CROSS JOIN bars_adjusted b
  ON  b.symbol    = :symbol
  AND b.datetime  > MAX({lo}, :t0)
  AND b.datetime <= MIN({hi}, :t1)
WHERE {hi} > :t0 AND {lo} < :t1
"""

# `open` and `close` must be the first and last *source bar's* values, not
# aggregates of the bucket -- an `open` taken as `MIN(open)` is a different
# number that still draws a plausible candle.
#
# So the bucket is reduced to the timestamps of its two end bars, which is a
# plain aggregate, and those two bars are then fetched by primary key. Ranking
# the whole bucket with `ROW_NUMBER() OVER (PARTITION BY t_ms ORDER BY dt)` also
# works and was measured against this: identical rows, 325 ms against 106 ms on
# `1d` over two years, because ranking costs a sort over every source row while
# the join-back costs two index seeks per *bucket*. It loses only where buckets
# and rows are the same thing -- `1m` measures 13.6 ms against 16.7 ms -- which
# is 3 ms on the one period where the aggregation is a no-op anyway.
#
# This assumes one `bars_adjusted` row per `(symbol, datetime)`, i.e. that no
# symbol has overlapping `adj_factors` intervals. Verified zero on the current
# database, and `aggregate_bars` refuses a duplicated `t_ms` rather than
# trusting it, because a fan-out here would double a bucket rather than fail.
_AGG_SQL = """
WITH src AS (""" + _SRC_CTE + """),
folded AS (
    SELECT t_ms, MIN(dt) AS t_first, MAX(dt) AS t_last,
           MAX(high) AS high, MIN(low) AS low,
           SUM(volume) AS volume, COUNT(*) AS n_src
    FROM src GROUP BY t_ms
)
SELECT f.t_ms, first.open, f.high, f.low, last.close, f.volume, f.n_src
FROM folded f
JOIN bars_adjusted first ON first.symbol = :symbol AND first.datetime = f.t_first
JOIN bars_adjusted last  ON last.symbol  = :symbol AND last.datetime  = f.t_last
ORDER BY f.t_ms
"""

_COUNT_SQL = """
WITH src AS (""" + _SRC_CTE + """)
SELECT COUNT(DISTINCT t_ms) FROM src
"""


def _bounds_sql(rth: bool) -> dict[str, str]:
    """The session-bound column expressions, chosen once per query."""
    if rth:
        return {"lo": "s.rth_open_ms", "hi": "s.rth_close_ms"}
    return {"lo": f"(s.ext_open_ms - {MS_PER_MIN})", "hi": "s.ext_close_ms"}


def _params(symbol: str, w: Window, period: str) -> dict:
    return {
        "symbol": symbol,
        "t0": w.scan_from_ms,
        "t1": w.scan_to_ms,
        "p_ms": period_minutes(period) * MS_PER_MIN,
    }


def aggregate_bars(conn: sqlite3.Connection, symbol: str, t0_ms: int, t1_ms: int,
                   period: str, rth: bool = True, limit: Optional[int] = None,
                   cal: Optional[TradingCalendar] = None) -> tuple[list[dict], bool]:
    """
    Aggregated bars for one symbol, ascending by `t_ms`, and whether more exist.

    Returns `(bars, has_more)` where each bar is the `Bar` payload of
    `docs/interfaces.md`. Buckets with no source bars are absent rather than
    zero-filled: `Bar`'s OHLC is non-nullable, and a bucket built from nothing
    has no open. GOOG 2025-09-24 is the real instance -- a whole session with no
    bars at all, so the day is missing from a `1d` series while `/sessions`
    still reports it, which is exactly how a consumer tells a real hole from a
    weekend.

    `cal` is loaded from `conn` when not supplied, which keeps a bare call
    self-sufficient; a service passes its cached one, since the sessions table
    is immutable for the life of the process.
    """
    cal = cal or TradingCalendar.load(conn)
    w = plan_window(cal, t0_ms, t1_ms, period, rth, limit)
    if not w.buckets:
        return [], w.has_more

    sql = _AGG_SQL.format(**_bounds_sql(rth))
    rows = conn.execute(sql, _params(symbol, w, period)).fetchall()

    # `has_more` from the plan is grid-only, so it claims older history exists
    # for a symbol that has none -- a client following the pan-left protocol
    # would then loop on empty pages. Narrow it to this symbol's own data. One
    # covering-index seek, measured at 1.5 us.
    has_more = w.has_more and conn.execute(
        "SELECT 1 FROM bars WHERE symbol = ? AND datetime <= ? LIMIT 1",
        (symbol, w.scan_from_ms),
    ).fetchone() is not None

    expected_by_end = {b.end_ms: b.n_expected for b in w.buckets}
    if len(rows) != len({r[0] for r in rows}):
        # Two rows for one bucket means the join back to `bars_adjusted` fanned
        # out, which means some symbol has overlapping `adj_factors` intervals.
        # That doubles a bucket's volume and can pick the wrong open -- and it
        # draws a normal candle either way, so it is checked rather than assumed.
        raise AssertionError(
            f"duplicate bucket for {symbol} {period}: overlapping adj_factors intervals"
        )
    bars = []
    for t_ms, o, h, lo, c, v, n_src in rows:
        if t_ms not in expected_by_end:
            # The two implementations of the boundary formula have diverged.
            # Cheap enough to check on every call, and the failure it catches --
            # bars silently landing in the wrong bucket -- renders as a normal
            # chart, so there is nothing else that would notice.
            raise AssertionError(
                f"SQL produced bucket {t_ms} for {symbol} {period} that "
                f"plan_window did not: the Python and SQL bucket formulas disagree"
            )
        bars.append({
            "t_ms": t_ms,
            "open": o,
            "high": h,
            "low": lo,
            "close": c,
            "volume": v,
            "n_src": n_src,
            "n_expected": expected_by_end[t_ms],
        })
    return bars, has_more


def count_buckets(conn: sqlite3.Connection, symbol: str, t0_ms: int, t1_ms: int,
                  period: str, rth: bool = True,
                  cal: Optional[TradingCalendar] = None) -> int:
    """
    How many bars `/bars` would return for this range, without transferring them.

    Counts buckets that actually have source data rather than grid slots, so it
    cannot disagree with `len(aggregate_bars(...))` for the same range. A count
    that disagrees with the thing it counts is worse than no count. The saving is
    the transfer, not the query -- the scan is the same one, at the same cost.
    """
    cal = cal or TradingCalendar.load(conn)
    w = plan_window(cal, t0_ms, t1_ms, period, rth, limit=None)
    if not w.buckets:
        return 0
    sql = _COUNT_SQL.format(**_bounds_sql(rth))
    return conn.execute(sql, _params(symbol, w, period)).fetchone()[0]
