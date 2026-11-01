"""
Acceptance tests for `database/label_store.py` and the `/labels` routes.

Run:  python scripts/acceptance_label_store.py

Plain asserts and a `main()` rather than pytest, for the same reason as
`acceptance_resample.py`: the repo declares no test tooling and `tests/` is
gitignored.

The routes are called as plain functions, as `dump_fixtures.py` does, so these
exercise the same path a request takes minus the HTTP framing. Every label is
stamped with a real bar's `t_ms` taken from `/bars`, never a computed one.

**The store under test is a fresh temporary directory.** WISETRADE_LABELS_DIR is
overridden before `config` is imported, so this script cannot touch the real
label store whatever the environment says.
"""

from __future__ import annotations

import inspect
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="wisetrade-labels-acceptance-"))
_REAL_LABELS_DIR = os.environ.get("WISETRADE_LABELS_DIR")
os.environ["WISETRADE_LABELS_DIR"] = str(_TMP)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException                                     # noqa: E402

from config import LABELS_DIR, SQLITE_DB_PATH                         # noqa: E402
from api.routers.bars import get_bars                                 # noqa: E402
from api.routers.labels import (                                      # noqa: E402
    create_label,
    delete_label,
    list_labels,
    update_label,
)
from database import label_store                                      # noqa: E402
from database.label_journal import JOURNAL_NAME                       # noqa: E402
from database.sessions import TradingCalendar                         # noqa: E402

# 2024-01-02, regular session (09:30, 16:00] ET -- the same day as the bar
# fixture bars_1d_one_session_5m.
DAY = (1704205800000, 1704229200000)

_failures: list[str] = []
_checks = 0


def check(cond: bool, what: str) -> None:
    global _checks
    _checks += 1
    if not cond:
        _failures.append(what)
        print(f"  FAIL  {what}")


def bar_ends(symbol: str, period: str, rth: bool = True) -> list[int]:
    lo, hi = DAY
    if not rth:
        lo, hi = lo - 6 * 3600_000, hi + 4 * 3600_000
    r = get_bars(symbol=symbol, period=period, t0_ms=lo, t1_ms=hi, rth=rth, limit=5000)
    return [b["t_ms"] for b in r["bars"]]


def label(t_from: int, *, t_to: int | None = None, symbol: str = "AAPL", period: str = "5m",
          rth: bool = True, label_type: str = "setup", source: str = "human",
          payload: dict | None = None) -> dict:
    d = {"symbol": symbol, "anchor": "point" if t_to is None else "interval",
         "t_from_ms": t_from, "t_to_ms": t_to, "period": period, "rth": rth,
         "label_type": label_type, "source": source}
    if payload is not None:
        d["payload"] = payload
    return d


def ids(rows: list[dict]) -> list[int]:
    return sorted(r["id"] for r in rows)


def get(**filters) -> list[dict]:
    """GET /labels. `source` is passed explicitly: calling the route function
    directly would otherwise hand it FastAPI's Query object, not its default."""
    filters.setdefault("source", None)
    return list_labels(**filters)


def status(fn, *args) -> int:
    try:
        fn(*args)
        return 200
    except HTTPException as e:
        return e.status_code


# ---------------------------------------------------------------------------

def test_open_vocabulary() -> None:
    print("\n1. a new label_type round-trips with a nested payload and no migration")
    get()                                   # the store creates its schema on first open
    conn = sqlite3.connect(_TMP / label_store.LABELS_DB_NAME)
    schema_before = conn.execute("SELECT group_concat(sql, ';') FROM sqlite_master").fetchone()[0]
    payload = {
        "pattern": {"name": "cup-and-handle", "legs": [{"from": 1, "to": 3}, {"from": 3, "to": 7}]},
        "conf": 0.8125, "tags": ["量价背离", "gap"], "flags": {"reviewed": False, "note": None},
        "depth": {"a": {"b": {"c": {"d": [1, [2, [3]]]}}}},
    }
    t = bar_ends("AAPL", "15m")[3]
    made = create_label(label(t, label_type="never_seen_before_type", payload=payload))
    back = [r for r in get(label_type="never_seen_before_type")]
    schema_after = conn.execute("SELECT group_concat(sql, ';') FROM sqlite_master").fetchone()[0]
    check(len(back) == 1 and back[0]["payload"] == payload, "nested payload equal after GET")
    check(json.dumps(back[0]["payload"], ensure_ascii=False) == json.dumps(payload, ensure_ascii=False),
          "payload key order and unicode preserved")
    check(made == back[0], "POST response equals GET response")
    check(schema_before == schema_after, "schema unchanged by a new label_type")
    check("never_seen_before_type" not in schema_after and "setup" not in schema_after,
          "no label type is named anywhere in the schema")
    conn.close()


def test_filters() -> None:
    print("\n2. every filter round-trips; omitting source returns both kinds")
    for lab in list(get()):
        delete_label(lab["id"])
    t5, t1h = bar_ends("AAPL", "5m"), bar_ends("AAPL", "1h")
    ext5 = bar_ends("AAPL", "5m", rth=False)
    made = [
        create_label(label(t5[10], source="human")),
        create_label(label(t5[11], source="model", payload={"conf": 0.7})),
        create_label(label(t5[12], symbol="MSFT", source="human")),
        create_label(label(t1h[2], period="1h", source="model", label_type="breakout")),
        create_label(label(ext5[5], rth=False, source="human", label_type="breakout")),
    ]
    facts = {m["id"]: m for m in made}

    declared = inspect.signature(list_labels).parameters["source"].default
    check(getattr(declared, "default", declared) is None,
          "GET /labels declares source default None (omitted == not filtered)")
    both = get()
    check(ids(both) == sorted(facts), "omitting source returns every label")
    check({r["source"] for r in both} == {"human", "model"}, "...and that includes both kinds")

    for name, value in (("source", "human"), ("source", "model"), ("symbol", "MSFT"),
                        ("period", "1h"), ("rth", False), ("label_type", "breakout")):
        expect = sorted(i for i, m in facts.items() if m[name] == value)
        got = ids(get(**{name: value}))
        check(got == expect, f"{name}={value!r} -> {got}, expected {expect}")

    got = ids(get(source="model", label_type="breakout"))
    check(got == [made[3]["id"]], "filters AND together")

    w = made[2]
    got = ids(get(t0_ms=w["cover_to_ms"], t1_ms=w["cover_to_ms"]))
    check(w["id"] in got, "time filter returns a label at its own stamp")


def test_byte_exact() -> None:
    print("\n3. t_from_ms / t_to_ms are byte-exact through POST, GET and the journal")
    for period in ("1m", "5m", "1h", "1d"):
        ends = bar_ends("AAPL", period)
        t_from, t_to = ends[0], ends[-1]
        made = create_label(label(t_from, t_to=t_to, period=period, label_type="exact"))
        back = [r for r in get(label_type="exact", period=period)][0]
        check(type(back["t_from_ms"]) is int and type(back["t_to_ms"]) is int,
              f"{period}: stamps come back as int")
        check(json.dumps(back["t_from_ms"]) == json.dumps(t_from) == str(t_from)
              and json.dumps(back["t_to_ms"]) == str(t_to),
              f"{period}: serialised digits identical to the bar's t_ms")
        check(made["t_from_ms"] == t_from, f"{period}: POST response stamp unchanged")
    line = (_TMP / JOURNAL_NAME).read_text().splitlines()[-1]
    check(f'"t_from_ms":{t_from},' in line and f'"t_to_ms":{t_to},' in line,
          "journal line carries the same digits")
    near = bar_ends("AAPL", "5m")[7]
    for bad in (near + 1, near - 60_000, float(near), True, str(near)):
        check(status(create_label, label(bad)) == 422,
              f"{bad!r} is refused rather than stored or snapped")


def test_interval_and_point() -> None:
    print("\n4. interval and point labels, including an interval across a range boundary")
    t = bar_ends("AAPL", "5m")
    iv = create_label(label(t[20], t_to=t[30], label_type="span"))     # (t[19], t[30]]
    pt = create_label(label(t[25], label_type="span"))                  # (t[24], t[25]]
    check(iv["anchor"] == "interval" and pt["anchor"] == "point" and pt["t_to_ms"] is None,
          "anchors stored as sent")
    check(iv["cover_from_ms"] == t[19] and iv["cover_to_ms"] == t[30],
          "interval covers from the start of its first bucket to the end of its last")

    def hit(t0, t1):
        return {r["id"] for r in get(label_type="span", t0_ms=t0, t1_ms=t1)}

    check(iv["id"] in hit(t[10], t[22]), "range ending inside the interval returns it")
    check(iv["id"] in hit(t[28], t[40]), "range starting inside the interval returns it")
    check(iv["id"] in hit(t[22], t[23]), "range strictly inside the interval returns it")
    check(iv["id"] in hit(t[30], t[40]), "range starting exactly at its last stamp returns it (inclusive)")
    check(iv["id"] not in hit(t[31], t[40]), "range starting after it does not")
    check(iv["id"] not in hit(t[0], t[19]), "range ending at its exclusive start does not")
    check(iv["id"] in hit(t[0], t[19] + 60_000), "one minute into its first bucket does")
    check(hit(t[25], t[25]) == {iv["id"], pt["id"]}, "a point inside the interval: both returned")
    check(pt["id"] not in hit(t[26], t[30]), "point not returned after its bucket")

    day = create_label(label(bar_ends("AAPL", "1d")[0], period="1d", label_type="span"))
    check(day["id"] in hit(t[0], t[30]), "a 1d point stamped 16:00 is found from a 5m morning window")
    ext = create_label(label(bar_ends("AAPL", "1h", rth=False)[5], period="1h", rth=False, label_type="span"))
    check(ext["within_rth"] is False and iv["within_rth"] is True,
          "within_rth separates extended-hours coverage from regular-hours coverage")


def test_delete_unreachable() -> None:
    print("\n5. a deleted label is unreachable by any filter")
    t = bar_ends("AAPL", "5m")
    keep = create_label(label(t[40], source="model", label_type="victim"))
    gone = create_label(label(t[41], source="model", label_type="victim", payload={"conf": 0.6}))
    delete_label(gone["id"])
    reachable = set()
    for f in ({}, {"source": "model"}, {"source": "human"}, {"symbol": "AAPL"}, {"period": "5m"},
              {"rth": True}, {"label_type": "victim"},
              {"t0_ms": gone["cover_from_ms"], "t1_ms": gone["cover_to_ms"]},
              {"source": "model", "symbol": "AAPL", "period": "5m", "rth": True, "label_type": "victim",
               "t0_ms": gone["cover_to_ms"], "t1_ms": gone["cover_to_ms"]}):
        reachable |= set(ids(get(**f)))
    check(gone["id"] not in reachable and keep["id"] in reachable,
          "no filter combination returns the deleted label; its neighbour still found")
    check(status(update_label, gone["id"], {"payload": {}}) == 404, "PATCH of a deleted label is 404")
    check(status(delete_label, gone["id"]) == 404, "DELETE twice is 404")
    last = json.loads((_TMP / JOURNAL_NAME).read_text().splitlines()[-1])
    check(last["op"] == "delete" and last["labels"][0]["before"]["payload"] == {"conf": 0.6},
          "the journal keeps the deleted label's before-image")


def test_branch_invariants(cal: TradingCalendar) -> None:
    print("\n6. this branch's own invariants: PATCH scope, journal rebuild, restore, orphans")
    t = bar_ends("AAPL", "5m")
    prop = create_label(label(t[50], source="model", label_type="prop", payload={"conf": 0.9}))
    for field, value in (("source", "human"), ("t_from_ms", t[51]), ("period", "1m"), ("rth", False)):
        check(status(update_label, prop["id"], {field: value}) == 422, f"PATCH {field} is 422")
    patched = update_label(prop["id"], {"payload": {"conf": 0.95}})
    check(patched["payload"] == {"conf": 0.95} and patched["cover_from_ms"] == prop["cover_from_ms"],
          "PATCH payload replaces it and leaves coverage alone")

    rev = create_label(label(t[50], label_type="review",
                             payload={"target_id": prop["id"], "verdict": "reject", "round": 1}))
    with label_store.LabelStore(cal) as s:
        gone = s.delete_where(source="model", label_type="prop")
        check(gone == [prop["id"]], "delete_where removes exactly the filtered proposals")
        check([r["id"] for r in s.orphan_reviews()] == [rev["id"]], "its review is kept, as an orphan")
        s.restore(gone)
        check(s.get(prop["id"]) == patched and s.orphan_reviews() == [],
              "restore brings it back under its id, PATCH included, and re-attaches the review")
        snapshot = s.query(source=None)

    (_TMP / label_store.LABELS_DB_NAME).unlink()
    check(get() == snapshot, "deleting labels.sqlite and reopening rebuilds an identical index")


def main() -> int:
    if not SQLITE_DB_PATH.exists():
        print(f"bar database not found at {SQLITE_DB_PATH}\n"
              f"set WISETRADE_SQLITE_DB_PATH, or build it with scripts/build_database.py",
              file=sys.stderr)
        return 2
    if LABELS_DIR != _TMP or (_REAL_LABELS_DIR and Path(_REAL_LABELS_DIR) == _TMP):
        print(f"refusing to run: label store resolved to {LABELS_DIR}, not the temp dir", file=sys.stderr)
        return 2
    cal = TradingCalendar.load(sqlite3.connect(f"file:{SQLITE_DB_PATH}?mode=ro", uri=True))
    print(f"database: {SQLITE_DB_PATH}\nlabel store (temporary): {_TMP}")

    test_open_vocabulary()
    test_filters()
    test_byte_exact()
    test_interval_and_point()
    test_delete_unreachable()
    test_branch_invariants(cal)

    print(f"\n{_checks - len(_failures)}/{_checks} checks passed")
    if _failures:
        print(f"{len(_failures)} FAILED:")
        for f in _failures:
            print(f"  - {f}")
        print(f"label store kept for inspection: {_TMP}")
        return 1
    shutil.rmtree(_TMP)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
