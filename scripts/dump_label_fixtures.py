"""
Dump real `/labels` payloads to JSON for `feat/chart-ui`.

    api/routers/labels.py ──► [ dump_label_fixtures ] ──► fixtures/labels/*.json ──► feat/chart-ui

Run:  python scripts/dump_label_fixtures.py

The labels are written into a fresh temporary store, never the real one, by the
same route functions a request reaches -- so every payload here is one the
service actually produced, stamps included. They sit on AAPL 2024-01-02, the
day of the bar fixtures `bars_1d_one_session_5m` and `bars_5m_extended_hours`,
so the chart can draw them over those bars.

The scenario is one round of the active-learning loop plus the shapes a
renderer has to survive: a review is never drawn itself but styles its target
(ADR-027), and the latest review of a target wins; a proposal can be pending,
accepted, rejected, or re-reviewed; a review can outlive its target; a 1d label
must appear on a 5m chart; an extended-hours label must vanish from an RTH view.

`created_at_ms` comes from a fixed fake clock so regenerating the fixtures does
not produce a diff. Writes to `fixtures/labels/` with its own `index.json`;
`fixtures/index.json` belongs to `dump_fixtures.py` and is not touched.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="wisetrade-label-fixtures-"))
os.environ["WISETRADE_LABELS_DIR"] = str(_TMP)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException                                     # noqa: E402

from config import FIXTURES_DIR, LABELS_DIR, SQLITE_DB_PATH           # noqa: E402
from api.routers.bars import get_bars                                 # noqa: E402
from api.routers.labels import (                                      # noqa: E402
    create_label,
    delete_label,
    list_labels,
    update_label,
)
from database import label_store                                      # noqa: E402

OUT_DIR = FIXTURES_DIR / "labels"

# 2024-01-02. Regular session (09:30, 16:00] ET; the extended window matches
# the bar fixture bars_5m_extended_hours.
RTH = (1704205800000, 1704229200000)
EXT = (RTH[0] - 6 * 3600_000, RTH[1] + 4 * 3600_000)
MORNING = (RTH[0], RTH[0] + 150 * 60_000)        # 09:30 .. 12:00 ET


class _FixedClock:
    """Stands in for the `time` module inside label_store: 1 s per label from 2026-01-01."""

    def __init__(self) -> None:
        self._ms = 1767225600000

    def time(self) -> float:
        self._ms += 1000
        return self._ms / 1000


def _ends(period: str, rth: bool = True) -> list[int]:
    lo, hi = RTH if rth else EXT
    return [b["t_ms"] for b in
            get_bars(symbol="AAPL", period=period, t0_ms=lo, t1_ms=hi, rth=rth, limit=5000)["bars"]]


def _label(t_from: int, *, t_to: int | None = None, period: str = "5m", rth: bool = True,
           label_type: str, source: str, payload: dict) -> dict:
    return {"symbol": "AAPL", "anchor": "point" if t_to is None else "interval",
            "t_from_ms": t_from, "t_to_ms": t_to, "period": period, "rth": rth,
            "label_type": label_type, "source": source, "payload": payload}


def _review(target: dict, verdict: str, round_: int) -> dict:
    """A review carries its target's own coordinates (ADR-027)."""
    return _label(target["t_from_ms"], t_to=target["t_to_ms"], period=target["period"],
                  rth=target["rth"], label_type="review", source="human",
                  payload={"target_id": target["id"], "verdict": verdict, "round": round_})


def _error(fn, *args) -> dict:
    try:
        fn(*args)
    except HTTPException as e:
        return {"status": e.status_code, "body": {"detail": e.detail}}
    raise AssertionError(f"{fn.__name__}{args} was expected to fail")


def _build() -> list[dict]:
    m5, h1, d1, m1 = _ends("5m"), _ends("1h"), _ends("1d"), _ends("1m")
    ext_h1 = _ends("1h", rth=False)
    model = {"model": "proto-v0", "round": 1}

    flag = create_label(_label(m5[10], t_to=m5[14], label_type="setup", source="human",
                               payload={"pattern": "bull_flag", "note": "tight consolidation after the open drive"}))
    create_label(_label(m5[15], label_type="entry", source="human", payload={"side": "long"}))
    create_label(_label(m1[200], period="1m", label_type="entry", source="human",
                        payload={"side": "short", "note": "1m label; on a 5m chart it marks the bar containing it"}))
    create_label(_label(d1[0], period="1d", label_type="trend_day", source="human",
                        payload={"direction": "up"}))
    create_label(_label(ext_h1[5], period="1h", rth=False, label_type="setup", source="human",
                        payload={"pattern": "premarket_gap", "note": "covers 09:00-10:00, so within_rth is false"}))

    accepted = create_label(_label(m5[30], t_to=m5[33], label_type="setup", source="model",
                                   payload={"pattern": "bull_flag", "conf": 0.91, **model}))
    rejected = create_label(_label(m5[40], t_to=m5[42], label_type="setup", source="model",
                                   payload={"pattern": "bull_flag", "conf": 0.74, **model}))
    pending = create_label(_label(m5[50], t_to=m5[53], label_type="setup", source="model",
                                  payload={"pattern": "bull_flag", "conf": 0.68, **model}))
    flipped = create_label(_label(m5[60], t_to=m5[62], label_type="setup", source="model",
                                  payload={"pattern": "bull_flag", "conf": 0.66, **model}))
    retired = create_label(_label(m5[70], label_type="setup", source="model",
                                  payload={"pattern": "bull_flag", "conf": 0.55, **model}))
    create_label(_review(accepted, "accept", 1))
    create_label(_review(rejected, "reject", 1))
    create_label(_review(flipped, "reject", 1))
    create_label(_review(flipped, "accept", 2))
    create_label(_review(retired, "reject", 1))
    delete_label(retired["id"])

    patched = update_label(flag["id"], {"payload": {**flag["payload"], "note": "edited: flag broke out at 10:50"}})

    probe = create_label(_label(m5[75], label_type="scratch", source="human", payload={}))
    delete_label(probe["id"])

    return [
        {
            "name": "labels_day_all",
            "note": (
                "Every label on AAPL 2024-01-02, extended-hours window, no source filter -- "
                "so human and model labels both come back, which is what a chart drawing both "
                "wants. Contains: a human interval and two human points; a 1d point and a 1m "
                "point; an rth=false 1h label with within_rth=false; five model proposals of "
                "which one is deleted; and five reviews. Review rules (ADR-027): a review is "
                f"never drawn, it styles its target, highest id wins. #{accepted['id']} accepted, "
                f"#{rejected['id']} rejected, #{pending['id']} pending (no review), "
                f"#{flipped['id']} rejected in round 1 then accepted in round 2. The review of "
                f"#{retired['id']} is an orphan: its target was deleted, so there is nothing to "
                f"style and it draws nothing. #{probe['id']} was deleted and is absent."
            ),
            "route": "/labels",
            "params": {"symbol": "AAPL", "t0_ms": EXT[0], "t1_ms": EXT[1]},
            "response": list_labels(symbol="AAPL", t0_ms=EXT[0], t1_ms=EXT[1], source=None),
        },
        {
            "name": "labels_day_human",
            "note": "source=human: ground truth only -- the human labels and every review.",
            "route": "/labels",
            "params": {"symbol": "AAPL", "t0_ms": EXT[0], "t1_ms": EXT[1], "source": "human"},
            "response": list_labels(symbol="AAPL", t0_ms=EXT[0], t1_ms=EXT[1], source="human"),
        },
        {
            "name": "labels_day_model",
            "note": "source=model: the live proposals. The deleted one is not here.",
            "route": "/labels",
            "params": {"symbol": "AAPL", "t0_ms": EXT[0], "t1_ms": EXT[1], "source": "model"},
            "response": list_labels(symbol="AAPL", t0_ms=EXT[0], t1_ms=EXT[1], source="model"),
        },
        {
            "name": "labels_morning_window",
            "note": (
                "A 5m chart showing 09:30-12:00. The 1d trend_day label is stamped 16:00 yet is "
                "returned, because the filter tests coverage (cover_from_ms, cover_to_ms], not "
                "the stamp. Draw a label on every loaded bar whose own bucket intersects its "
                "coverage. On an RTH view, hide labels with within_rth=false entirely rather "
                "than clipping them."
            ),
            "route": "/labels",
            "params": {"symbol": "AAPL", "t0_ms": MORNING[0], "t1_ms": MORNING[1]},
            "response": list_labels(symbol="AAPL", t0_ms=MORNING[0], t1_ms=MORNING[1], source=None),
        },
        {
            "name": "label_post_response",
            "note": "POST /labels returns 201 and the stored label, id and derived fields assigned.",
            "route": "POST /labels",
            "params": {},
            "response": flag,
        },
        {
            "name": "label_patch_response",
            "note": "PATCH replaces payload whole; coverage and created_at_ms never change.",
            "route": "PATCH /labels/{id}",
            "params": {"label_id": flag["id"]},
            "response": patched,
        },
        {
            "name": "label_errors",
            "note": (
                "Error bodies are FastAPI's {\"detail\": str}. 422 when the store refuses a label "
                "or a patch, 404 for an unknown or deleted id. A stamp off the bar grid is "
                "refused, never snapped -- it means the timestamp did not come from a loaded bar."
            ),
            "route": "errors",
            "params": {},
            "response": {
                "post_off_grid_stamp": _error(create_label, _label(m5[5] + 60_000, label_type="setup",
                                                                   source="human", payload={})),
                "post_missing_rth": _error(create_label, {k: v for k, v in _label(
                    m5[5], label_type="setup", source="human", payload={}).items() if k != "rth"}),
                "patch_source": _error(update_label, accepted["id"], {"source": "human"}),
                "patch_deleted": _error(update_label, retired["id"], {"payload": {}}),
                "delete_deleted": _error(delete_label, probe["id"]),
            },
        },
    ]


def main() -> int:
    if not SQLITE_DB_PATH.exists():
        print(f"bar database not found at {SQLITE_DB_PATH}\n"
              f"set WISETRADE_SQLITE_DB_PATH, or build it with scripts/build_database.py",
              file=sys.stderr)
        return 1
    if LABELS_DIR != _TMP:
        print(f"refusing to run: label store resolved to {LABELS_DIR}, not the temp dir", file=sys.stderr)
        return 1

    label_store.time = _FixedClock()
    try:
        cases = _build()
    finally:
        shutil.rmtree(_TMP)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    index = []
    for case in cases:
        path = OUT_DIR / f"{case['name']}.json"
        path.write_text(json.dumps(case, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        r = case["response"]
        summary = f"{len(r)} labels" if isinstance(r, list) else f"{len(r)} keys"
        index.append({"name": case["name"], "file": path.name, "route": case["route"],
                      "params": case["params"], "note": case["note"], "summary": summary})
        print(f"  {path.name:34s} {summary}")

    (OUT_DIR / "index.json").write_text(json.dumps({
        "generated_from": SQLITE_DB_PATH.name,
        "contract": "docs/interfaces.md",
        "bar_fixtures": ["bars_1d_one_session_5m", "bars_5m_extended_hours"],
        "cases": index,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\n{len(index)} fixtures + index.json -> {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
