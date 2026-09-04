"""
Trading sessions, derived from the bar data and validated against a calendar
package (ADR-019).

    bars (UTC ms) ──► derive_sessions ──► sessions table ──► TradingCalendar
                       convert to ET,                         (bisect lookup)
                       group by ET date

Why the data is truth and the package is only a check:

An exchange-calendar package encodes session hours as constants in hand-written
Python, so it is only as current as its last release. The bars, for a backtest,
*are* the record of when the market was open. Deriving from them removes any
dependence on the package being right about the past and repurposes it as the
data-quality check the project otherwise lacks: package says trading day, data
has no bars => a missing download, not a holiday.

Why ET conversion has to happen here, once:

The vendor buckets files by UTC date, so a session's post-20:00-ET tail lands in
the *next* day's folder -- which is why the raw tree has Saturday folders holding
Friday evening. Measured on the real database: in UTC the sessions straddle date
boundaries and weekends appear to trade; converted to ET it collapses to 479
clean sessions, every one starting 04:01 ET, no weekend dates at all. 2.0% of
distinct timestamps change date under the conversion. Nothing downstream should
re-derive a session from a raw timestamp.

Bar stamps are `eob`, so the bar labelled 09:31 covers 09:30-09:31. Regular hours
are therefore the half-open interval (09:30, 16:00] -- exactly 390 bars. Treating
it as closed at both ends picks up a pre-open bar and drops the closing one.
"""

from __future__ import annotations

import logging
import sqlite3
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo


EXCHANGE_TZ = ZoneInfo("America/New_York")

RTH_OPEN_ET = (9, 30)    # exclusive lower bound: first RTH bar is stamped 09:31
RTH_CLOSE_ET = (16, 0)   # inclusive upper bound
FULL_SESSION_RTH_BARS = 390


def _default_calendar_name() -> str:
    """
    Which exchange calendar bounds a session.

    Defaults to XNYS while the vendor's `exchange` column reads XNAS. That is
    safe only because NYSE and Nasdaq keep identical regular hours and holidays
    for US equities -- it is not safe in general, and the --all ingest brings in
    ETFs and cross-listings. Configurable so the assumption can be changed
    without editing code, and named here so it is findable.
    """
    try:
        from config import EXCHANGE_CALENDAR
        return EXCHANGE_CALENDAR
    except Exception:
        return "XNYS"


SESSIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    et_date      TEXT    NOT NULL PRIMARY KEY,   -- YYYY-MM-DD, exchange-local
    ext_open_ms  INTEGER NOT NULL,               -- first bar of the day
    rth_open_ms  INTEGER NOT NULL,               -- EXCLUSIVE lower bound (09:30 ET)
    rth_close_ms INTEGER NOT NULL,               -- INCLUSIVE upper bound (16:00, or 13:00)
    ext_close_ms INTEGER NOT NULL,               -- last bar of the day
    n_bars_rth   INTEGER NOT NULL,
    is_half_day  INTEGER NOT NULL
);
"""

# Regular-hours view, layered on the adjusted view rather than on raw bars so
# that a caller can never accidentally get RTH-filtered *unadjusted* prices.
RTH_VIEW = """
DROP VIEW IF EXISTS bars_rth;
CREATE VIEW bars_rth AS
SELECT b.symbol, b.datetime, b.open, b.high, b.low, b.close, b.volume
FROM bars_adjusted b
JOIN sessions s
  ON  b.datetime >  s.rth_open_ms
  AND b.datetime <= s.rth_close_ms;
"""


@dataclass(frozen=True)
class Session:
    et_date: date
    ext_open_ms: int
    rth_open_ms: int      # exclusive
    rth_close_ms: int     # inclusive
    ext_close_ms: int
    n_bars_rth: int
    is_half_day: bool

    # Containment is the union of "when bars were observed" and "when the
    # exchange was scheduled to be open". Those differ: ext_* come from the
    # data, rth_* from the calendar package, so a truncated final session has
    # its last bar before the scheduled 16:00 close. Bounding containment by
    # the observed bars alone would make an instant that is plainly inside
    # regular hours resolve to no session at all.
    @property
    def contains_from(self) -> int:
        return min(self.ext_open_ms, self.rth_open_ms)

    @property
    def contains_to(self) -> int:
        return max(self.ext_close_ms, self.rth_close_ms)


def _et_clock_ms(d: date, hh: int, mm: int) -> int:
    """A wall-clock time on an exchange-local date, as Unix ms UTC."""
    return int(datetime(d.year, d.month, d.day, hh, mm, tzinfo=EXCHANGE_TZ).timestamp() * 1000)


def derive_sessions(conn: sqlite3.Connection, validate_against: Optional[str] = "XNYS") -> int:
    """
    Rebuild the sessions table from the bar time axis. Returns the row count.

    Idempotent: dropped and rebuilt, so running twice equals running once.

    Which dates are sessions comes from the data (ADR-019). The intraday RTH
    boundary comes from the calendar package when it is installed, because that
    is the one thing the data genuinely cannot resolve: extended-hours trade
    still prints after an early close, so a purely data-derived boundary put
    the four half-days at 13:01-13:02 instead of 13:00. The package is
    authoritative here and agrees with the data everywhere else.

    Without the package, the fallback walks forward from 09:31 while consecutive
    minutes have bars. The union across all symbols is dense during regular
    hours -- measured 390 of 390 minutes on 475 of 479 sessions -- so the run is
    a good signal, accurate to a minute or two on early closes.
    """
    conn.executescript(SESSIONS_SCHEMA)
    conn.execute("DELETE FROM sessions")

    # One conversion pass over the distinct time axis (~450k values), not over
    # every bar (~25M).
    by_date: dict[date, set[int]] = {}
    for (ts,) in conn.execute("SELECT DISTINCT datetime FROM bars ORDER BY datetime"):
        et = datetime.fromtimestamp(ts / 1000, timezone.utc).astimezone(EXCHANGE_TZ)
        by_date.setdefault(et.date(), set()).add(ts)

    if not by_date:
        raise ValueError(
            "derive_sessions: the bars table is empty, so no sessions can be derived. "
            "Ingest bars before deriving sessions (utils/build_database.py). Writing an "
            "empty sessions table would leave bars_rth silently returning nothing."
        )

    # Resolved once and shared. Previously both the bounds lookup and the cross
    # check imported the package, built a calendar and queried the same range
    # independently -- duplicated work, and two places that could disagree about
    # what counts as a session or handle a missing package differently.
    cal = _load_calendar_package(validate_against)
    session_dates = sorted(by_date)
    bounds = _package_rth_bounds(cal, session_dates)

    rows = []
    short_sessions: list = []
    for et_date, stamps in sorted(by_date.items()):
        if et_date in bounds:
            rth_open, rth_close = bounds[et_date]
        else:
            rth_open = _et_clock_ms(et_date, *RTH_OPEN_ET)
            rth_ceiling = _et_clock_ms(et_date, *RTH_CLOSE_ET)
            minute = 60_000
            cursor = rth_open + minute        # first RTH bar, stamped 09:31
            rth_close = rth_open              # empty run => zero-width RTH
            while cursor <= rth_ceiling and cursor in stamps:
                rth_close = cursor
                cursor += minute

        n_rth = sum(1 for t in stamps if rth_open < t <= rth_close)

        # A half-day is a property of the *schedule*, not of how many bars
        # arrived. Inferring it from the bar count labels any truncated or
        # partially-downloaded session as an early close, which is a data
        # problem wearing a calendar problem's clothes. With package bounds the
        # test is exact: the scheduled close is before 16:00 ET.
        if et_date in bounds:
            is_half = rth_close < _et_clock_ms(et_date, *RTH_CLOSE_ET)
            if not is_half and 0 < n_rth < FULL_SESSION_RTH_BARS - 10:
                short_sessions.append((et_date, n_rth))
        else:
            is_half = 0 < n_rth < FULL_SESSION_RTH_BARS - 10

        rows.append((
            et_date.isoformat(), min(stamps), rth_open, rth_close,
            max(stamps), n_rth, int(is_half),
        ))

    conn.executemany(
        "INSERT INTO sessions (et_date, ext_open_ms, rth_open_ms, rth_close_ms, "
        "ext_close_ms, n_bars_rth, is_half_day) VALUES (?,?,?,?,?,?,?)", rows
    )
    conn.executescript(RTH_VIEW)
    conn.commit()

    n_half = sum(r[6] for r in rows)
    logging.info(f"sessions: {len(rows)} derived from data, {n_half} half-day(s)")

    # Full-length scheduled sessions that are missing bars. Not half-days --
    # incomplete data, which is exactly what the cross-check exists to surface.
    for d, n in short_sessions:
        logging.warning(
            f"{d}: scheduled a full session but only {n} regular-hours bars "
            f"(expected {FULL_SESSION_RTH_BARS}) -- incomplete data, not an early close"
        )

    for problem in _cross_check(cal, rows):
        logging.warning(f"calendar cross-check: {problem}")

    return len(rows)


def _load_calendar_package(calendar_name: Optional[str]):
    """The exchange calendar, or None when disabled or the package is absent."""
    if calendar_name is None:
        return None
    if calendar_name is True or calendar_name == "":
        calendar_name = _default_calendar_name()
    try:
        import exchange_calendars as xcals
    except ImportError:
        logging.info("exchange_calendars not installed; falling back to bar-density boundaries")
        return None
    return xcals.get_calendar(calendar_name)


def _package_rth_bounds(cal, session_dates: list) -> dict:
    """
    Exact (rth_open_exclusive, rth_close_inclusive) per date, from the calendar.

    Returns {} when there is no calendar, so the caller falls back to deriving
    the boundary from bar density. Only the *intraday* boundary comes from the
    package; the session set itself stays data-derived (ADR-019).
    """
    if cal is None or not session_dates:
        return {}
    known = {d.date() for d in cal.sessions_in_range(str(session_dates[0]), str(session_dates[-1]))}
    out = {}
    for d in session_dates:
        if d not in known:
            continue  # data has bars the package does not call a session; _cross_check reports it
        # session_open is 09:30 ET, which is exactly the exclusive lower bound:
        # bars are stamped eob, so the first regular bar is 09:31.
        out[d] = (
            int(cal.session_open(str(d)).timestamp() * 1000),
            int(cal.session_close(str(d)).timestamp() * 1000),
        )
    return out


def _cross_check(cal, rows: list) -> list[str]:
    """
    Compare derived sessions against the exchange calendar.

    Disagreements are reported as data-quality findings, never used to override
    the data (ADR-019). Absence of the package is not an error -- it is an
    optional check, so a machine without it still builds a correct database.
    """
    if cal is None or not rows:
        return []

    derived = {date.fromisoformat(r[0]) for r in rows}
    lo, hi = min(derived), max(derived)
    expected = {d.date() for d in cal.sessions_in_range(str(lo), str(hi))}

    problems = []
    name = getattr(cal, "name", "calendar")
    for d in sorted(expected - derived):
        problems.append(f"{d} is a {name} session but has no bars -- missing data?")
    for d in sorted(derived - expected):
        problems.append(f"{d} has bars but is not a {name} session")
    return problems


class TradingCalendar:
    """
    The single source for every time-derived quantity.

    Loads the derived sessions (479 rows for two years) and answers by bisect,
    so there is no per-bar timezone arithmetic anywhere in the event loop.
    Injected into Strategy as `self.calendar`, exactly as `self.portfolio`
    already is, so `Strategy.next()`'s signature is unchanged.
    """

    def __init__(self, sessions: list[Session]):
        self._sessions = sorted(sessions, key=lambda s: s.contains_from)
        self._starts = [s.contains_from for s in self._sessions]

    @classmethod
    def load(cls, conn: sqlite3.Connection) -> "TradingCalendar":
        rows = conn.execute(
            "SELECT et_date, ext_open_ms, rth_open_ms, rth_close_ms, ext_close_ms, "
            "n_bars_rth, is_half_day FROM sessions ORDER BY ext_open_ms"
        ).fetchall()
        return cls([
            Session(date.fromisoformat(r[0]), r[1], r[2], r[3], r[4], r[5], bool(r[6]))
            for r in rows
        ])

    def __len__(self) -> int:
        return len(self._sessions)

    def session_for(self, ts_ms: int) -> Optional[Session]:
        """The session containing this instant, or None if it falls outside one."""
        i = bisect_right(self._starts, ts_ms) - 1
        if i < 0:
            return None
        s = self._sessions[i]
        return s if ts_ms <= s.contains_to else None

    def is_regular_hours(self, ts_ms: int) -> bool:
        s = self.session_for(ts_ms)
        return bool(s) and s.rth_open_ms < ts_ms <= s.rth_close_ms

    def minutes_to_close(self, ts_ms: int) -> Optional[int]:
        """
        Minutes remaining until this session's regular close; 0 once past it.

        Always the RTH close, even when extended-hours bars are being fed: the
        16:00 (or 13:00) close is the liquidity event a strategy is reasoning
        about, and it is the only definition that stays meaningful when the
        regular-hours filter is toggled.

        Replaces SMA_OS_Dynamic._minutes_to_close(), which hardcoded hour=21 --
        wrong for half the year, blind to early closes, and carrying a dead
        `except ValueError: return 60` that would have reported "60 minutes to
        close" forever on every bar if its hour parameter were ever swept out of
        range.
        """
        s = self.session_for(ts_ms)
        if s is None:
            return None
        return max(0, (s.rth_close_ms - ts_ms) // 60_000)

    def periods_per_year(self, regular_hours_only: bool = True) -> float:
        """
        UNIVERSE-WIDE bars per year, measured from the sessions present.

        This is a property of the database, not of any one symbol, and must not
        be used to annualise a single symbol's returns when extended hours are
        included. Measured across TECH_100: the average is 948 bars/session but
        BKNG trades 154 and NVDA 918, so annualising BKNG with this figure
        overstates its volatility by sqrt(948/154) ~ 2.5x. Analyzer therefore
        derives its own factor from the run's actual return count; this method
        is for describing the dataset.

        Regular hours are exempt from that spread -- every symbol has the same
        390 bars -- which is why the old hardcoded 252 * 390 was within 0.5%
        until the ADR-020 switch made extended hours reachable.
        """
        if not self._sessions:
            return 0.0
        span_days = (self._sessions[-1].et_date - self._sessions[0].et_date).days + 1
        sessions_per_year = len(self._sessions) / max(span_days / 365.25, 1e-9)
        if regular_hours_only:
            # Sessions contributing no regular-hours bars are excluded, not
            # averaged in as zeros. A partial download or a date the calendar
            # does not recognise produces one, and each would pull the mean
            # below 390 and understate annualised volatility for every run in
            # the database -- silently, since nothing else reports it.
            counts = [s.n_bars_rth for s in self._sessions if s.n_bars_rth > 0]
            if not counts:
                return 0.0
            bars = sum(counts) / len(counts)
        else:
            bars = self._avg_total_bars()
        return sessions_per_year * bars

    def _avg_total_bars(self) -> float:
        # Extended sessions are ragged (liquidity, not structure), so the mean
        # span in minutes is a better estimator than any single day's count.
        spans = [(s.ext_close_ms - s.ext_open_ms) / 60_000 + 1 for s in self._sessions]
        return sum(spans) / len(spans)
