"""
Label store: human and model judgments attached to bars (ADR-027).

    api/routers/labels.py ──► [ connect → insert_label / query_labels ] ──► labels.sqlite
                                     ▲                                       (in WISETRADE_LABELS_DIR)
                              config.require_labels_dir

A label is one row per observation. `label_type` is an open vocabulary and
`payload` an arbitrary JSON object, so a new kind of label is an INSERT, never a
migration: nothing in the schema enumerates label types.

This module holds no DROP and no rebuild path, unlike every other schema in
database/. Those tables are derived and rebuilt freely; this one is the only
data in the system that cannot be regenerated.

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
    CHECK (anchor IN ('point', 'interval')),
    CHECK (source IN ('human', 'model')),
    CHECK (typeof(rth) = 'integer' AND rth IN (0, 1)),
    CHECK (typeof(t_from_ms) = 'integer'),
    CHECK (t_to_ms IS NULL OR typeof(t_to_ms) = 'integer'),
    CHECK ((anchor = 'point'    AND t_to_ms IS NULL)
        OR (anchor = 'interval' AND t_to_ms IS NOT NULL AND t_to_ms >= t_from_ms)),
    CHECK (json_valid(payload) AND json_type(payload) = 'object')
);
CREATE INDEX IF NOT EXISTS ix_labels_span ON labels(symbol, period, COALESCE(t_to_ms, t_from_ms));
CREATE INDEX IF NOT EXISTS ix_labels_from ON labels(symbol, period, t_from_ms);
CREATE INDEX IF NOT EXISTS ix_labels_kind ON labels(source, label_type);
"""

_COLUMNS = ("id", "symbol", "anchor", "t_from_ms", "t_to_ms", "period", "rth",
            "label_type", "payload", "source", "created_at_ms")


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


def insert_label(conn: sqlite3.Connection, label: dict[str, Any]) -> dict[str, Any]:
    """Store one label. `id` and `created_at_ms` are assigned here, never by the caller."""
    try:
        row = (
            label["symbol"], label["anchor"], label["t_from_ms"], label.get("t_to_ms"),
            label["period"], label["rth"], label["label_type"],
            json.dumps(label.get("payload", {})),
            label["source"], int(time.time() * 1000),
        )
    except KeyError as e:
        raise LabelError(f"missing required field {e.args[0]!r}") from None
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO labels (symbol, anchor, t_from_ms, t_to_ms, period, rth,"
                " label_type, payload, source, created_at_ms) VALUES (?,?,?,?,?,?,?,?,?,?)",
                row,
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

    The time filter is overlap, not containment, with both bounds inclusive as
    in /bars: an interval that starts before `t0_ms` and ends inside the window
    is returned. Testing `t_from_ms BETWEEN t0 AND t1` would drop it, and the
    label would vanish from the chart as the user pans.
    """
    where, args = [], []
    for col, val in (("source", source), ("symbol", symbol),
                     ("period", period), ("rth", rth), ("label_type", label_type)):
        if val is not None:
            where.append(f"{col} = ?")
            args.append(val)
    if t0_ms is not None:
        where.append("COALESCE(t_to_ms, t_from_ms) >= ?")
        args.append(t0_ms)
    if t1_ms is not None:
        where.append("t_from_ms <= ?")
        args.append(t1_ms)
    sql = f"SELECT {', '.join(_COLUMNS)} FROM labels"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY t_from_ms, id"
    return [_to_dict(r) for r in conn.execute(sql, args)]


def _get(conn: sqlite3.Connection, label_id: int) -> Optional[dict[str, Any]]:
    r = conn.execute(f"SELECT {', '.join(_COLUMNS)} FROM labels WHERE id = ?", (label_id,)).fetchone()
    return _to_dict(r) if r else None


def _to_dict(r: tuple) -> dict[str, Any]:
    d = dict(zip(_COLUMNS, r))
    d["rth"] = bool(d["rth"])
    d["payload"] = json.loads(d["payload"])
    return d
