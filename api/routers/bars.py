"""
Bar and calendar routes. Owned by `feat/bar-service`.

Payload shape and field semantics are frozen in `docs/interfaces.md`; the route
signatures below are part of that contract. Implement against them -- do not
change them without raising it, because `feat/chart-ui` is already building
against these query parameters.

    routers/bars.py ──► database/resample.py ──► bars_adjusted (x) sessions
                    └─► database/sessions.py:TradingCalendar

Aggregation and bucket policy live in `database/resample.py`. This module is
transport: it validates parameters, owns the connection, and caches the calendar.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from config import SQLITE_DB_PATH
from database.resample import PERIODS, aggregate_bars, count_buckets
from database.sessions import TradingCalendar

router = APIRouter(tags=["bars"])


# The sessions table is immutable for the life of the process, so the calendar
# is loaded once. Two threads racing here both build the same 479-row object and
# one assignment wins, which is harmless. A rebuilt database needs a restart --
# acceptable for a loopback-only service, and stated so it is not a surprise.
_CALENDAR: Optional[TradingCalendar] = None


def _connect() -> sqlite3.Connection:
    """
    A read-only connection, one per request.

    Read-only is a property worth having rather than a formality: this service
    exists to serve a labelling UI and must never be able to write to the bar
    database. FastAPI runs sync endpoints in a threadpool and `sqlite3`
    connections are not shareable across threads, so per-request it is; opening
    the file costs well under a millisecond.
    """
    if not SQLITE_DB_PATH.exists():
        raise HTTPException(
            503,
            f"bar database not found at {SQLITE_DB_PATH}. Set "
            f"WISETRADE_SQLITE_DB_PATH, or build it with scripts/build_database.py.",
        )
    return sqlite3.connect(f"file:{SQLITE_DB_PATH}?mode=ro", uri=True)


def _calendar(conn: sqlite3.Connection) -> TradingCalendar:
    global _CALENDAR
    if _CALENDAR is None:
        # Validate BEFORE caching. Caching first meant an underived sessions
        # table raised 503 exactly once and then served empty 200s forever,
        # because the empty calendar was already in the cache by the time the
        # guard ran -- a broken install reading as "no data in this range".
        loaded = TradingCalendar.load(conn)
        if len(loaded) == 0:
            raise HTTPException(
                503,
                "sessions table is empty; run scripts/build_database.py to derive it.",
            )
        _CALENDAR = loaded
    return _CALENDAR


def _check_range(t0_ms: int, t1_ms: int) -> None:
    if t1_ms < t0_ms:
        raise HTTPException(422, f"t1_ms ({t1_ms}) precedes t0_ms ({t0_ms})")


def _check_period(period: str) -> None:
    if period not in PERIODS:
        raise HTTPException(422, f"unknown period {period!r}; expected one of {list(PERIODS)}")


def _check_symbol(conn: sqlite3.Connection, symbol: str) -> None:
    """
    404 rather than an empty series on an unknown symbol.

    An empty result would be indistinguishable from a range with no data, and
    `has_more` would still be true because the grid exists regardless of the
    symbol -- so a typo would render as a chart that looks merely unloaded.

    Checked against `bars_adjusted` rather than `bars`, because that is the
    relation the bars are actually served from: a symbol ingested before
    `build_adj_factors` was re-run has rows in `bars` and none in the view, and
    would otherwise pass this check and then return an empty series. Two index
    seeks instead of one, measured at 2.8 us against 1.5 us.
    """
    if conn.execute(
        "SELECT 1 FROM bars_adjusted WHERE symbol = ? LIMIT 1", (symbol,)
    ).fetchone() is None:
        raise HTTPException(
            404,
            f"unknown symbol {symbol!r}, or it has no adj_factors rows; see GET /symbols",
        )


@router.get("/symbols")
def list_symbols() -> list[str]:
    """Every symbol present in `bars`, ascending."""
    with closing(_connect()) as conn:
        # DISTINCT, not GROUP BY. Both produce the identical query plan --
        # `SCAN bars USING COVERING INDEX idx_symbol_time` -- and measure 0.3 ms
        # against 503 ms over 25.5M rows, because only DISTINCT gets SQLite's
        # skip optimisation. The plan text does not distinguish them; the clock
        # does. See the NOTE in docs/principles.md.
        return [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM bars ORDER BY symbol")]


@router.get("/sessions")
def list_sessions(
    t0_ms: int = Query(..., description="inclusive lower bound, Unix ms UTC"),
    t1_ms: int = Query(..., description="inclusive upper bound, Unix ms UTC"),
) -> list[dict]:
    """
    Session bounds overlapping the range.

    The chart needs these to draw session breaks and, more importantly, to tell a
    legitimate overnight gap from missing data -- without them every night looks
    like a hole.
    """
    _check_range(t0_ms, t1_ms)
    with closing(_connect()) as conn:
        cal = _calendar(conn)
        # Widest envelope (extended bounds), not the regular-hours one: /bars may
        # be called with rth=false, and a session whose returned bars had no
        # session row would be exactly the hole this route exists to rule out.
        sessions = cal.sessions_in_range(t0_ms, t1_ms, regular_hours_only=False)
    return [
        {
            "et_date": s.et_date.isoformat(),
            "ext_open_ms": s.ext_open_ms,
            "rth_open_ms": s.rth_open_ms,
            "rth_close_ms": s.rth_close_ms,
            "ext_close_ms": s.ext_close_ms,
            "n_bars_rth": s.n_bars_rth,
            "is_half_day": s.is_half_day,
        }
        for s in sessions
    ]


@router.get("/bars")
def get_bars(
    symbol: str,
    period: str = Query(..., description="one of docs/interfaces.md period vocabulary"),
    t0_ms: int = Query(..., description="inclusive lower bound, Unix ms UTC"),
    t1_ms: int = Query(..., description="inclusive upper bound, Unix ms UTC"),
    rth: bool = Query(True, description="regular trading hours only"),
    limit: int = Query(5000, ge=1, le=20000, description="page size, newest-first truncation"),
) -> dict:
    """
    Aggregated bars, ascending by `t_ms`. Returns `{"bars": [...], "has_more": bool}`.

    `t1_ms` is also the replay cursor: the no-future guarantee for the replay
    driver is enforced here, by the range query, not by the client hiding bars it
    already holds. A client that has the future in memory will eventually leak it.

    When the range holds more than `limit` buckets, the *newest* `limit` are
    returned and `has_more` is true -- panning left refetches with an earlier
    `t1_ms`. The period is never silently changed to make the range fit.
    """
    _check_period(period)
    _check_range(t0_ms, t1_ms)
    with closing(_connect()) as conn:
        _check_symbol(conn, symbol)
        bars, has_more = aggregate_bars(
            conn, symbol, t0_ms, t1_ms, period, rth=rth, limit=limit, cal=_calendar(conn)
        )
    return {"bars": bars, "has_more": has_more}


@router.get("/bars/count")
def count_bars(
    symbol: str,
    period: str,
    t0_ms: int,
    t1_ms: int,
    rth: bool = True,
) -> dict:
    """Bucket count for the range, `{"n": int}`, without transferring the bars."""
    _check_period(period)
    _check_range(t0_ms, t1_ms)
    with closing(_connect()) as conn:
        _check_symbol(conn, symbol)
        n = count_buckets(conn, symbol, t0_ms, t1_ms, period, rth=rth, cal=_calendar(conn))
    return {"n": n}
