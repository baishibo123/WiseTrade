"""
Bar and calendar routes. Owned by `feat/bar-service`.

Payload shape and field semantics are frozen in `docs/interfaces.md`; the route
signatures below are part of that contract. Implement against them -- do not
change them without raising it, because `feat/chart-ui` is already building
against these query parameters.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(tags=["bars"])

_TODO = "not implemented on main -- see branch feat/bar-service"


@router.get("/symbols")
def list_symbols() -> list[str]:
    """Every symbol present in `bars`, ascending."""
    raise HTTPException(501, _TODO)


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
    raise HTTPException(501, _TODO)


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
    raise HTTPException(501, _TODO)


@router.get("/bars/count")
def count_bars(
    symbol: str,
    period: str,
    t0_ms: int,
    t1_ms: int,
    rth: bool = True,
) -> dict:
    """Bucket count for the range, `{"n": int}`, without transferring the bars."""
    raise HTTPException(501, _TODO)
