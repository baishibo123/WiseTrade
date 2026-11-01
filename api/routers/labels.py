"""
Label store routes. Owned by `feat/label-store`.

Payload shape is frozen in `docs/interfaces.md`. The schema is deliberately open:
`label_type` is an open vocabulary and `payload` is an arbitrary JSON object, so
a new kind of label needs no migration and no change to these routes.

    routers/labels.py ──► database/label_store.LabelStore ──► labels.jsonl + labels.sqlite
                      └─► routers/bars.py:_calendar           (WISETRADE_LABELS_DIR)
                          (session calendar, for coverage)

Storage rules live in `database/label_store.py`. This module is transport: it
owns the connection and maps store errors onto HTTP status codes.
"""

from __future__ import annotations

from contextlib import closing

from fastapi import APIRouter, HTTPException, Query

from database import label_store

# The bar router's calendar cache and read-only connection, reused rather than
# copied: one process-wide calendar, one place that validates it. Coverage has
# to be computed on exactly the grid /bars serves, so sharing it is the point.
from .bars import _calendar, _connect as _connect_bars

router = APIRouter(prefix="/labels", tags=["labels"])

_TODO = "not implemented on main -- see branch feat/label-store"


def _open() -> label_store.LabelStore:
    """
    One store per request: FastAPI runs sync endpoints in a threadpool and
    `sqlite3` connections are not shareable across threads. Opening replays any
    journal lines another process appended since, so every request sees them.

    An unset or missing WISETRADE_LABELS_DIR is a 503, not an empty result. The
    store has no default location by design (ADR-027), and answering `[]` would
    look exactly like "no labels yet". A journal that cannot be replayed is a
    503 for the same reason: serving the stale index would hide it (ADR-028).
    """
    with closing(_connect_bars()) as bars_conn:
        cal = _calendar(bars_conn)
    try:
        return label_store.LabelStore(cal)
    except (RuntimeError, FileNotFoundError) as e:   # JournalError is a RuntimeError
        raise HTTPException(503, str(e)) from None


@router.get("")
def list_labels(
    symbol: str | None = None,
    t0_ms: int | None = None,
    t1_ms: int | None = None,
    period: str | None = None,
    rth: bool | None = None,
    label_type: str | None = None,
    source: str | None = Query(None, description='"human" or "model"'),
) -> list[dict]:
    """
    Labels matching every supplied filter. All filters are optional and AND-ed.

    `source` is never defaulted: human labels are ground truth and model labels
    are proposals awaiting review, and a caller that pools them by forgetting the
    filter is training on its own output.

    So omitting `source` returns **both** kinds, and that is pinned by a test
    rather than left to this comment. A consumer that wants ground truth must
    ask for `source=human`.

    The time filter is overlap of the label's coverage `(cover_from_ms,
    cover_to_ms]` with the instants `[t0_ms, t1_ms]`, so a label made on any
    period is found from any other. A chart wanting every label touching its
    loaded bars should pass `t0_ms = first_bar.t_ms - period`: the first bucket
    covers minutes before its own `t_ms`, and over-fetching is harmless where
    under-fetching drops a label at the left edge.
    """
    with _open() as store:
        return store.query(
            source=source, symbol=symbol, t0_ms=t0_ms, t1_ms=t1_ms,
            period=period, rth=rth, label_type=label_type,
        )


@router.post("", status_code=201)
def create_label(label: dict) -> dict:
    """
    Store one label; returns it with its assigned `id`.

    `t_from_ms` / `t_to_ms` must be a bar's own `datetime` as read from the
    database, never a timestamp reconstructed from a pixel coordinate. This route
    cannot detect the difference, which is exactly why it is stated here.

    The click path that satisfies this: the chart yields a logical index, the UI
    looks that bar up in the window it loaded from `/bars`, and sends *that
    bar's* `t_ms`. A timestamp off by one bar or one timezone renders correctly
    and silently attaches the label to the wrong bar; nothing downstream can
    detect or undo it.

    `source` and `rth` are required and have no default. `id` and `created_at_ms` are
    assigned by the store and ignored if sent. A label the store refuses is a
    422 naming the rule it broke.
    """
    with _open() as store:
        try:
            return store.insert(label)
        except label_store.LabelError as e:
            raise HTTPException(422, str(e)) from None


@router.patch("/{label_id}")
def update_label(label_id: int, patch: dict) -> dict:
    """Partial update of one label."""
    raise HTTPException(501, _TODO)


@router.delete("/{label_id}", status_code=204)
def delete_label(label_id: int) -> None:
    """Remove one label."""
    raise HTTPException(501, _TODO)
