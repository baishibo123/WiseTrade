"""
Dump real `/bars`, `/sessions` and `/symbols` payloads to JSON.

    api/routers/bars.py ──► [ dump_fixtures ] ──► fixtures/*.json ──► feat/chart-ui

Run:  python scripts/dump_fixtures.py

Why this exists and why it ships before the service is finished: `feat/chart-ui`
develops against these files, not against a running process. An agent building
against an imagined API cannot detect that it imagined it -- the first signal
would be at integration, after the chart has been written twice.

The cases below are chosen to be the *shapes* a renderer has to survive, not a
sample of pretty data. Each one is a payload that looks normal and is not:
a short first bucket, an early close, a bucket built from a third of its
minutes, a session that is simply absent, a series with no completeness
information at all.

The routes are called as plain functions rather than over HTTP. Every field in
these payloads is already a JSON scalar, so FastAPI's encoder is a no-op and the
bytes are the bytes the service serves -- without needing a test client that is
not part of the `[api]` extra.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import FIXTURES_DIR, SQLITE_DB_PATH          # noqa: E402
from api.routers.bars import (                            # noqa: E402
    count_bars,
    get_bars,
    list_sessions,
    list_symbols,
)

# Session bounds are hardcoded from the sessions table rather than recomputed, so
# a fixture cannot silently follow a bug in the code that produced it.
#   2024-01-02  normal      rth (09:30, 16:00]  1704205800000 .. 1704229200000
#   2024-01-04  normal      rth                 1704378600000 .. 1704402000000
#   2024-07-03  HALF DAY    rth (09:30, 13:00]  1720013400000 .. 1720026000000
#   2025-09-24  normal, but GOOG has no bars at all
DAY_2024_01_02 = (1704205800000, 1704229200000)
DAY_2024_01_04 = (1704378600000, 1704402000000)
HALF_2024_07_03 = (1720013400000, 1720026000000)
WEEK_AROUND_GOOG_GAP = (1758513600000, 1759032000000)   # 2025-09-22 .. 2025-09-28
MONTH_2025_09 = (1756699200000, 1759291200000)


def _bars(name: str, note: str, **params) -> dict:
    return {
        "name": name,
        "note": note,
        "route": "/bars",
        "params": params,
        "response": get_bars(**params),
    }


def _cases() -> list[dict]:
    lo, hi = DAY_2024_01_02
    half_lo, half_hi = HALF_2024_07_03
    gap_lo, gap_hi = WEEK_AROUND_GOOG_GAP
    gappy_lo, gappy_hi = DAY_2024_01_04
    month_lo, month_hi = MONTH_2025_09

    return [
        {
            "name": "symbols",
            "note": "Every symbol the service can serve. 115 of them.",
            "route": "/symbols",
            "params": {},
            "response": list_symbols(),
        },
        {
            "name": "sessions_week_around_goog_gap",
            "note": (
                "Five sessions. 2025-09-24 is present here even though GOOG has no "
                "bars on it at all -- that pairing is how a consumer tells a real "
                "hole from a weekend, and it is why this route exists."
            ),
            "route": "/sessions",
            "params": {"t0_ms": gap_lo, "t1_ms": gap_hi},
            "response": list_sessions(t0_ms=gap_lo, t1_ms=gap_hi),
        },
        {
            "name": "sessions_half_day",
            "note": "2024-07-03 closes at 13:00 ET. is_half_day is true and rth_close_ms is three hours early.",
            "route": "/sessions",
            "params": {"t0_ms": half_lo, "t1_ms": half_hi},
            "response": list_sessions(t0_ms=half_lo, t1_ms=half_hi),
        },
        _bars(
            "bars_1d_one_session_5m",
            "The baseline: one full session at 5m. 78 buckets, every n_src == n_expected == 5.",
            symbol="AAPL", period="5m", t0_ms=lo, t1_ms=hi, rth=True, limit=5000,
        ),
        _bars(
            "bars_1h_short_first_bucket",
            "60 does not divide 390. Buckets are anchored on the CLOSE, so the "
            "short one is the session's FIRST (09:30-10:00, n_expected 30) and every "
            "session still ends exactly at 16:00. Seven buckets, not six or eight.",
            symbol="AAPL", period="1h", t0_ms=lo, t1_ms=hi, rth=True, limit=5000,
        ),
        _bars(
            "bars_2h_short_first_bucket",
            "Same again at 2h: four buckets, the first spanning only 30 minutes.",
            symbol="AAPL", period="2h", t0_ms=lo, t1_ms=hi, rth=True, limit=5000,
        ),
        _bars(
            "bars_1h_half_day",
            "The half day at 1h: four buckets ending 10:00, 11:00, 12:00, 13:00. "
            "Nothing after 13:00 exists and that is not missing data.",
            symbol="AAPL", period="1h", t0_ms=half_lo, t1_ms=half_hi, rth=True, limit=5000,
        ),
        _bars(
            "bars_2h_half_day",
            "The half day at 2h: two buckets, the first 90 minutes wide (09:30-11:00). "
            "A period's short bucket is not a fixed width -- it depends on the session.",
            symbol="AAPL", period="2h", t0_ms=half_lo, t1_ms=half_hi, rth=True, limit=5000,
        ),
        _bars(
            "bars_5m_incomplete_buckets",
            "AKAM 2024-01-04 has 346 of 390 RTH minutes. Several 5m buckets report "
            "n_src < n_expected, one of them n_src=1. These are the bars a chart should "
            "outline differently; the service does not decide what to do about them.",
            symbol="AKAM", period="5m", t0_ms=gappy_lo, t1_ms=gappy_hi, rth=True, limit=5000,
        ),
        _bars(
            "bars_1d_goog_missing_session",
            "GOOG across 2025-09-22..28. Four daily bars for five sessions: 2025-09-24 "
            "has no bars at all, so it has no bucket. Compare against "
            "sessions_week_around_goog_gap, which still lists it.",
            symbol="GOOG", period="1d", t0_ms=gap_lo, t1_ms=gap_hi, rth=True, limit=5000,
        ),
        _bars(
            "bars_5m_extended_hours",
            "rth=false. n_expected is null on every bar, because extended-hours "
            "coverage varies by orders of magnitude between symbols and no theoretical "
            "count exists. A renderer must not treat null as zero.",
            symbol="AAPL", period="5m", t0_ms=lo - 6 * 3600_000, t1_ms=hi + 4 * 3600_000,
            rth=False, limit=5000,
        ),
        _bars(
            "bars_5m_paged_has_more",
            "One month of 5m truncated to 120 buckets. The NEWEST 120 are returned and "
            "has_more is true; panning left means refetching with an earlier t1_ms. The "
            "period is never coarsened to make a range fit.",
            symbol="AAPL", period="5m", t0_ms=month_lo, t1_ms=month_hi, rth=True, limit=120,
        ),
        {
            "name": "bars_count_5m_one_month",
            "note": (
                "How many bars /bars would return for the same range with no limit. "
                "Counts buckets that have data, so it cannot disagree with len(bars)."
            ),
            "route": "/bars/count",
            "params": {"symbol": "AAPL", "period": "5m", "t0_ms": month_lo,
                       "t1_ms": month_hi, "rth": True},
            "response": count_bars(symbol="AAPL", period="5m", t0_ms=month_lo,
                                   t1_ms=month_hi, rth=True),
        },
    ]


def _summarise(case: dict) -> str:
    r = case["response"]
    if isinstance(r, list):
        return f"{len(r)} items"
    if isinstance(r, dict) and "bars" in r:
        bars = r["bars"]
        if not bars:
            return f"0 bars, has_more={r['has_more']}"
        short = sum(1 for b in bars if b["n_expected"] is not None and b["n_src"] < b["n_expected"])
        return (f"{len(bars)} bars, has_more={r['has_more']}, "
                f"{short} incomplete, t_ms {bars[0]['t_ms']}..{bars[-1]['t_ms']}")
    return json.dumps(r)


def main() -> int:
    if not SQLITE_DB_PATH.exists():
        print(f"bar database not found at {SQLITE_DB_PATH}\n"
              f"set WISETRADE_SQLITE_DB_PATH, or build it with utils/build_database.py",
              file=sys.stderr)
        return 1

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    index = []
    for case in _cases():
        path = FIXTURES_DIR / f"{case['name']}.json"
        path.write_text(json.dumps(case, indent=2) + "\n", encoding="utf-8")
        index.append({
            "name": case["name"],
            "file": path.name,
            "route": case["route"],
            "params": case["params"],
            "note": case["note"],
            "summary": _summarise(case),
        })
        print(f"  {path.name:44s} {_summarise(case)}")

    index_path = FIXTURES_DIR / "index.json"
    index_path.write_text(json.dumps({
        # The database name only. Its absolute path is machine-specific, and
        # config.py is explicit that such paths must never be committed as
        # literals -- committing one would also give feat/chart-ui a spurious
        # diff every time the fixtures are regenerated on another machine.
        "generated_from": SQLITE_DB_PATH.name,
        "contract": "docs/interfaces.md",
        "cases": index,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"\n{len(index)} fixtures + index.json -> {FIXTURES_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
