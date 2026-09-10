"""
Label store routes. Owned by `feat/label-store`.

Payload shape is frozen in `docs/interfaces.md`. The schema is deliberately open:
`label_type` is an open vocabulary and `payload` is an arbitrary JSON object, so
a new kind of label needs no migration and no change to these routes.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/labels", tags=["labels"])

_TODO = "not implemented on main -- see branch feat/label-store"


@router.get("")
def list_labels(
    symbol: str | None = None,
    t0_ms: int | None = None,
    t1_ms: int | None = None,
    period: str | None = None,
    label_type: str | None = None,
    source: str | None = Query(None, description='"human" or "model"'),
) -> list[dict]:
    """
    Labels matching every supplied filter. All filters are optional and AND-ed.

    `source` is never defaulted: human labels are ground truth and model labels
    are proposals awaiting review, and a caller that pools them by forgetting the
    filter is training on its own output.
    """
    raise HTTPException(501, _TODO)


@router.post("", status_code=201)
def create_label(label: dict) -> dict:
    """
    Store one label; returns it with its assigned `id`.

    `t_from_ms` / `t_to_ms` must be a bar's own `datetime` as read from the
    database, never a timestamp reconstructed from a pixel coordinate. This route
    cannot detect the difference, which is exactly why it is stated here.
    """
    raise HTTPException(501, _TODO)


@router.patch("/{label_id}")
def update_label(label_id: int, patch: dict) -> dict:
    """Partial update of one label."""
    raise HTTPException(501, _TODO)


@router.delete("/{label_id}", status_code=204)
def delete_label(label_id: int) -> None:
    """Remove one label."""
    raise HTTPException(501, _TODO)
