"""
Label store: human and model judgments attached to bars (ADR-027).

    api/routers/labels.py ──► [ connect → insert_label / query_labels ] ──► labels.sqlite
                                     ▲             ▲                        (in WISETRADE_LABELS_DIR)
                      config.require_labels_dir    │
                                     TradingCalendar + resample.bucket_span
                                     (coverage, derived at insert)

A label is one row per observation. `label_type` is an open vocabulary and
`payload` an arbitrary JSON object, so a new kind of label is an INSERT, never a
migration: nothing in the schema enumerates label types.

This module holds no DROP and no rebuild path, unlike every other schema in
database/. Those tables are derived and rebuilt freely; this one is the only
data in the system that cannot be regenerated.

Each row carries two kinds of column. The contract's fields are the facts: what
the user saw and judged. `cover_from_ms`, `cover_to_ms` and `within_rth` are
derived from those facts and the session calendar, and can be recomputed from
them at any time; nothing derived is ever accepted from a caller.

Timestamps are stored exactly as given. There is no datetime and no timezone
code in this module, so normalising a timestamp is impossible here rather than
merely avoided.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from .resample import bucket_end_for, bucket_span
from .sessions import TradingCalendar

LABELS_DB_NAME = "labels.sqlite"

# Every CHECK guards a structural rule of the frozen `Label` contract. None of
# them touches `label_type`.
#
# typeof() rather than the column type alone: an INTEGER column silently stores
# 'abc' as text and 1751414400000.5 as real. With the CHECK, only values that
# are exactly integers survive -- a float that is exactly integral is converted
# losslessly by column affinity, anything else is rejected.
LABELS_SCHEMA = """
CREATE TABLE IF NOT EXISTS labels (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol        TEXT    NOT NULL,
    anchor        TEXT    NOT NULL,
    t_from_ms     INTEGER NOT NULL,
    t_to_ms       INTEGER,                         -- NULL iff anchor = 'point'
    period        TEXT    NOT NULL,                -- part of the observation
    rth           INTEGER NOT NULL,                -- so is this; 1 = RTH chart
    label_type    TEXT    NOT NULL,                -- open vocabulary
    payload       TEXT    NOT NULL DEFAULT '{}',   -- JSON object
    source        TEXT    NOT NULL,                -- no default, anywhere
    created_at_ms INTEGER NOT NULL,
    -- Derived, never accepted from a caller (see _coverage).
    cover_from_ms INTEGER NOT NULL,                -- exclusive
    cover_to_ms   INTEGER NOT NULL,                -- inclusive
    within_rth    INTEGER NOT NULL,
    CHECK (cover_from_ms < cover_to_ms),
    CHECK (anchor IN ('point', 'interval')),
    CHECK (source IN ('human', 'model')),
    CHECK (typeof(rth) = 'integer' AND rth IN (0, 1)),
    CHECK (typeof(t_from_ms) = 'integer'),
    CHECK (t_to_ms IS NULL OR typeof(t_to_ms) = 'integer'),
    CHECK ((anchor = 'point'    AND t_to_ms IS NULL)
        OR (anchor = 'interval' AND t_to_ms IS NOT NULL AND t_to_ms >= t_from_ms)),
    CHECK (json_valid(payload) AND json_type(payload) = 'object')
);
CREATE INDEX IF NOT EXISTS ix_labels_cover_to   ON labels(symbol, cover_to_ms);
CREATE INDEX IF NOT EXISTS ix_labels_cover_from ON labels(symbol, cover_from_ms);
CREATE INDEX IF NOT EXISTS ix_labels_kind ON labels(source, label_type);
"""

_COLUMNS = ("id", "symbol", "anchor", "t_from_ms", "t_to_ms", "period", "rth",
            "label_type", "payload", "source", "created_at_ms",
            "cover_from_ms", "cover_to_ms", "within_rth")


class LabelError(ValueError):
    """A label the store refuses. The message says which rule it broke."""


def connect(labels_dir: Optional[Path] = None) -> sqlite3.Connection:
    """
    Open the label store, creating the file and schema on first use.

    `labels_dir` defaults to WISETRADE_LABELS_DIR, which must be set: there is
    deliberately no fallback location (ADR-027).
    """
    if labels_dir is None:
        from config import require_labels_dir
        labels_dir = require_labels_dir()
    conn = sqlite3.connect(Path(labels_dir) / LABELS_DB_NAME)
    conn.executescript(LABELS_SCHEMA)
    return conn


def insert_label(conn: sqlite3.Connection, label: dict[str, Any],
                 cal: TradingCalendar) -> dict[str, Any]:
    """
    Store one label. `id`, `created_at_ms` and the coverage columns are assigned
    here, never by the caller.

    `cal` is the bar database's session calendar. It is needed because a label's
    timestamps are bucket ends, and which minutes a bucket covers depends on the
    session it falls in.
    """
    try:
        facts = (
            label["symbol"], label["anchor"], label["t_from_ms"], label.get("t_to_ms"),
            label["period"], label["rth"], label["label_type"],
            json.dumps(label.get("payload", {})),
            label["source"],
        )
    except KeyError as e:
        raise LabelError(f"missing required field {e.args[0]!r}") from None
    cover = _coverage(cal, label["t_from_ms"], label.get("t_to_ms"),
                      label["period"], label["rth"])
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO labels (symbol, anchor, t_from_ms, t_to_ms, period, rth,"
                " label_type, payload, source, created_at_ms,"
                " cover_from_ms, cover_to_ms, within_rth)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (*facts, int(time.time() * 1000), *cover),
            )
    except sqlite3.IntegrityError as e:
        raise LabelError(str(e)) from None
    return _get(conn, cur.lastrowid)


def query_labels(
    conn: sqlite3.Connection,
    *,
    source: Optional[str],
    symbol: Optional[str] = None,
    t0_ms: Optional[int] = None,
    t1_ms: Optional[int] = None,
    period: Optional[str] = None,
    rth: Optional[bool] = None,
    label_type: Optional[str] = None,
) -> list[dict[str, Any]]:
    """
    Labels matching every supplied filter, ascending by (t_from_ms, id).

    `source` is the one argument without a default. Human labels are ground
    truth and model labels are proposals; a caller that pools them by forgetting
    the filter is training on its own output. Pooling therefore has to be
    written out as `source=None`.

    The time filter tests the label's *coverage* `(cover_from_ms, cover_to_ms]`
    against the instants `[t0_ms, t1_ms]`, not its stamps. The stamps are bucket
    ends, so a 1d point stamped 16:00 covers the whole session; filtering on the
    stamp would hide it from a 5m chart showing 09:30-12:00, and an interval
    that starts before the window would vanish as the user pans.
    """
    where, args = [], []
    for col, val in (("source", source), ("symbol", symbol),
                     ("period", period), ("rth", rth), ("label_type", label_type)):
        if val is not None:
            where.append(f"{col} = ?")
            args.append(val)
    if t0_ms is not None:
        where.append("cover_to_ms >= ?")
        args.append(t0_ms)
    if t1_ms is not None:
        where.append("cover_from_ms < ?")
        args.append(t1_ms)
    sql = f"SELECT {', '.join(_COLUMNS)} FROM labels"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY t_from_ms, id"
    return [_to_dict(r) for r in conn.execute(sql, args)]


def _coverage(cal: TradingCalendar, t_from_ms: Any, t_to_ms: Any,
              period: Any, rth: Any) -> tuple[int, int, int]:
    """
    `(cover_from_ms, cover_to_ms, within_rth)` for one label.

    Coverage is the 1-minute span `(exclusive, inclusive]` the labelled buckets
    occupy: from the clamped start of the first bucket to the end of the last.
    It is what lets a label be drawn and queried on any other period.

    Each stamp must be a bucket end on the grid of its own session for this
    `period` and `rth`, exactly as /bars would emit it. A stamp off the grid is
    refused rather than snapped: it is the signature of a timestamp that did not
    come from a loaded bar, and snapping would hide that.

    `within_rth` is 1 when every minute covered is a regular-hours minute. True
    by construction for an RTH label; for an extended-hours label only when the
    whole span sits inside one session's (rth_open, rth_close]. A view showing
    regular hours hides a label with `within_rth = 0` rather than clipping it,
    because a pattern judged with the pre-market in view is not the same
    observation once the pre-market is cut off.
    """
    if type(rth) is not bool:
        raise LabelError(f"rth must be true or false, got {rth!r}")
    for name, t in (("t_from_ms", t_from_ms), ("t_to_ms", t_to_ms)):
        # bool is an int subclass; True would otherwise pass as 1 ms.
        if t is not None and type(t) is not int:
            raise LabelError(f"{name} must be an integer of Unix ms, got {t!r}")
    ends = [t_from_ms] if t_to_ms is None else [t_from_ms, t_to_ms]
    sessions = []
    for t in ends:
        s = cal.session_for(t)
        try:
            on_grid = s is not None and bucket_end_for(t, s, period, rth) == t
        except ValueError as e:   # unknown period, or outside the rth/ext envelope
            raise LabelError(str(e)) from None
        if not on_grid:
            raise LabelError(
                f"{t} is not the end of a {period} bucket (rth={rth}); "
                f"label stamps must be a bar's t_ms as served by /bars"
            )
        sessions.append(s)
    cover_from, _ = bucket_span(t_from_ms, sessions[0], period, rth)
    cover_to = ends[-1]
    first, last = sessions[0], sessions[-1]
    within = rth or (first is last and first.rth_open_ms <= cover_from
                     and cover_to <= first.rth_close_ms)
    return cover_from, cover_to, int(within)


def _get(conn: sqlite3.Connection, label_id: int) -> Optional[dict[str, Any]]:
    r = conn.execute(f"SELECT {', '.join(_COLUMNS)} FROM labels WHERE id = ?", (label_id,)).fetchone()
    return _to_dict(r) if r else None


def _to_dict(r: tuple) -> dict[str, Any]:
    d = dict(zip(_COLUMNS, r))
    d["rth"] = bool(d["rth"])
    d["within_rth"] = bool(d["within_rth"])
    d["payload"] = json.loads(d["payload"])
    return d
