"""
Label store: human and model judgments attached to bars (ADR-027, ADR-028).

    api/routers/labels.py ──► LabelStore ──► BEGIN; apply ──► label_journal.append ──► COMMIT
                                  │            (CHECKs)          labels.jsonl (truth)    labels.sqlite
                                  │                                                       (index)
                                  └── on open: label_journal.read_from(offset) ──► apply each ──► labels.sqlite

`labels.jsonl` is the record; `labels.sqlite` is an index that can be deleted and
rebuilt from it at any time (ADR-028). The index is written only by applying
journal operations, and a write is journalled only after SQLite has accepted it,
so a label the CHECKs refuse never reaches the record.

A label is one row per observation. `label_type` is an open vocabulary and
`payload` an arbitrary JSON object, so a new kind of label is an INSERT, never a
migration: nothing in the schema enumerates label types.

Each row carries two kinds of column. The contract's fields are the facts: what
the user saw and judged, and the only thing journalled. `cover_from_ms`,
`cover_to_ms` and `within_rth` are derived from those facts and the session
calendar, recomputed whenever an operation is applied, and never accepted from
a caller.

Timestamps are stored exactly as given. There is no datetime and no timezone
code in this module, so normalising a timestamp is impossible here rather than
merely avoided.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from . import label_journal
from .label_journal import JournalError
from .resample import bucket_end_for, bucket_span
from .sessions import TradingCalendar

LABELS_DB_NAME = "labels.sqlite"

log = logging.getLogger(__name__)

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


# Where the index stands in the journal: the last `seq` applied and the byte
# offset just past its line. Updated in the same transaction as the operation
# itself, so the two can never disagree after a crash.
STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS journal_state (
    id     INTEGER PRIMARY KEY CHECK (id = 1),
    seq    INTEGER NOT NULL,
    offset INTEGER NOT NULL
);
INSERT OR IGNORE INTO journal_state (id, seq, offset) VALUES (1, 0, 0);
"""

# The contract's fields, in contract order: exactly what a journal line holds.
_FACTS = ("symbol", "anchor", "t_from_ms", "t_to_ms", "period", "rth",
          "label_type", "payload", "source", "created_at_ms")


class LabelError(ValueError):
    """A label the store refuses. The message says which rule it broke."""


class LabelStore:
    """
    One open label store: the index connection, caught up with the journal.

    Open one per request or per script run; `sqlite3` connections are not
    shareable across threads. `cal` is the bar database's session calendar --
    coverage is computed on exactly the grid /bars serves.
    """

    def __init__(self, cal: TradingCalendar, labels_dir: Optional[Path] = None):
        if labels_dir is None:
            from config import require_labels_dir
            labels_dir = require_labels_dir()
        self.dir = Path(labels_dir)
        self.cal = cal
        with label_journal.locked(self.dir):
            self.conn = self._open_index()
            self._catch_up()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "LabelStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- writes -----------------------------------------------------------

    def insert(self, label: dict[str, Any]) -> dict[str, Any]:
        """
        Store one label. `id`, `created_at_ms` and the coverage columns are
        assigned here; anything else sent beyond the contract's fields is
        ignored and never journalled.
        """
        for k in _FACTS:
            if k not in label and k not in ("t_to_ms", "payload", "created_at_ms"):
                raise LabelError(f"missing required field {k!r}")
        facts = {k: label.get(k) for k in _FACTS}
        facts["payload"] = label.get("payload", {})
        facts["created_at_ms"] = int(time.time() * 1000)
        cover = _coverage(self.cal, facts["t_from_ms"], facts["t_to_ms"],
                          facts["period"], facts["rth"])

        with label_journal.locked(self.dir):
            self._catch_up()          # another process may have written since we opened
            seq, _ = self._state()
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                label_id = self._insert_row(None, facts, cover)
                op = {"seq": seq + 1, "op": "insert", "at_ms": facts["created_at_ms"],
                      "id": label_id, "label": facts}
                end = label_journal.append(self.dir, op)
                self._set_state(seq + 1, end)
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
        return self.get(label_id)

    # -- reads ------------------------------------------------------------

    def get(self, label_id: int) -> Optional[dict[str, Any]]:
        r = self.conn.execute(f"SELECT {', '.join(_COLUMNS)} FROM labels WHERE id = ?",
                              (label_id,)).fetchone()
        return _to_dict(r) if r else None

    def query(
        self,
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
        truth and model labels are proposals; a caller that pools them by
        forgetting the filter is training on its own output. Pooling therefore
        has to be written out as `source=None`.

        The time filter tests the label's *coverage* `(cover_from_ms,
        cover_to_ms]` against the instants `[t0_ms, t1_ms]`, not its stamps. The
        stamps are bucket ends, so a 1d point stamped 16:00 covers the whole
        session; filtering on the stamp would hide it from a 5m chart showing
        09:30-12:00, and an interval that starts before the window would vanish
        as the user pans.
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
        return [_to_dict(r) for r in self.conn.execute(sql, args)]

    # -- index maintenance (caller holds the journal lock) ----------------

    def _open_index(self) -> sqlite3.Connection:
        """
        Open the index, moving it aside and starting empty if it is damaged.

        Starting empty is safe only because the index is derived: `_catch_up`
        then replays the whole journal into it. The damaged file is kept, not
        deleted, in case the journal turns out to be the thing that is wrong.
        """
        path = self.dir / LABELS_DB_NAME
        conn = sqlite3.connect(path, isolation_level=None)
        try:
            ok = conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        except sqlite3.DatabaseError as e:
            ok, reason = False, str(e)
        else:
            reason = "quick_check failed"
        if not ok:
            conn.close()
            aside = path.with_name(f"{path.name}.damaged-{int(time.time() * 1000)}")
            log.error("label index %s is damaged (%s); moved to %s and rebuilding from %s",
                      path, reason, aside.name, label_journal.JOURNAL_NAME)
            path.rename(aside)
            for suffix in ("-journal", "-wal", "-shm"):
                side = path.with_name(path.name + suffix)
                if side.exists():
                    side.rename(aside.with_name(aside.name + suffix))
            conn = sqlite3.connect(path, isolation_level=None)
        conn.executescript(LABELS_SCHEMA + STATE_SCHEMA)
        return conn

    def _catch_up(self) -> None:
        """
        Apply every journal operation the index has not seen, one transaction each.

        An operation that cannot be applied stops the store from opening (ADR-028).
        Skipping it would leave the index silently disagreeing with the record;
        the known case is an `rth: false` stamp left off-grid by a shifted
        extended-hours session.
        """
        seq, offset = self._state()
        for op, end in label_journal.read_from(self.dir, offset, seq):
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                self._apply(op)
                self._set_state(op["seq"], end)
                self.conn.execute("COMMIT")
            except (LabelError, sqlite3.Error, KeyError, TypeError) as e:
                self.conn.execute("ROLLBACK")
                raise JournalError(
                    f"journal seq {op['seq']} ({op.get('op')} id={op.get('id')}) cannot be "
                    f"applied to the index: {e}. Not opening; the journal is untouched."
                ) from None
            log.info("replayed journal seq %d (%s id=%s)", op["seq"], op["op"], op.get("id"))

    def _apply(self, op: dict[str, Any]) -> None:
        if op["op"] == "insert":
            f = op["label"]
            cover = _coverage(self.cal, f["t_from_ms"], f["t_to_ms"], f["period"], f["rth"])
            self._insert_row(op["id"], f, cover)
        else:
            raise LabelError(f"unknown journal operation {op['op']!r}")

    def _insert_row(self, label_id: Optional[int], f: dict[str, Any],
                    cover: tuple[int, int, int]) -> int:
        """INSERT one row; `label_id` is None for a new label, the journalled id on replay."""
        try:
            cur = self.conn.execute(
                f"INSERT INTO labels (id, {', '.join(_FACTS)}, cover_from_ms, cover_to_ms, within_rth)"
                f" VALUES ({', '.join('?' * (len(_FACTS) + 4))})",
                (label_id, *(json.dumps(f[k]) if k == "payload" else f[k] for k in _FACTS), *cover),
            )
        except sqlite3.IntegrityError as e:
            raise LabelError(str(e)) from None
        return cur.lastrowid

    def _state(self) -> tuple[int, int]:
        return self.conn.execute("SELECT seq, offset FROM journal_state WHERE id = 1").fetchone()

    def _set_state(self, seq: int, offset: int) -> None:
        self.conn.execute("UPDATE journal_state SET seq = ?, offset = ? WHERE id = 1", (seq, offset))


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


def _to_dict(r: tuple) -> dict[str, Any]:
    d = dict(zip(_COLUMNS, r))
    d["rth"] = bool(d["rth"])
    d["within_rth"] = bool(d["within_rth"])
    d["payload"] = json.loads(d["payload"])
    return d
