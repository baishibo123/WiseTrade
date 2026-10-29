"""
Curve recording interface (ADR-008, ADR-016).

    Portfolio.update() ──┬──► _equity_history        (Portfolio still owns this)
                         └──► recorder.on_equity_point(point, revises_previous=…)

The point of this module is the *call site*, not the abstract class. An ABC can
be introduced at any time; a hook inside the event loop cannot, because adding
one later means changing `Portfolio` and `Engine` rather than writing a
subclass. Until now the only recorder entry point ran after the backtest
finished and received the completed list, so it could change the file format and
nothing else -- streaming was not slow, it was unreachable.

What this opens up, all of it "portfolio state over time":
  - live plotting while a slow run is in progress
  - streaming to disk so memory stays bounded
  - per-run progress or heartbeat
  - a drawdown circuit breaker that stops a run early
  - per-symbol curves rather than only the portfolio curve

What it does NOT reach, each needing its own hook elsewhere: fill-level events
(they happen in `Portfolio.process_signals`) and bar-level data (the event loop
in `Engine.run`). And it says nothing about *what to display* when a batch
produces two hundred curves -- that question is still open and is the harder
one.

Nothing streaming is implemented. The default is NullRecorder, so behaviour is
unchanged and `Analyzer` is untouched: `Portfolio` keeps owning
`_equity_history` and the recorder is a pure side channel. ADR-016 left the
ownership question open deliberately; side-channel is chosen for now because
bounded memory is not a real constraint at this scale (one year of single-symbol
minute bars is ~98k rows, about 4 MB). Moving ownership into a recorder is what
bounded memory would eventually require, and it can be done later without
disturbing this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence


# (timestamp_ms, equity, cash, positions_value, num_positions)
EquityPoint = tuple


class CurveRecorder(ABC):
    """
    Receives portfolio state as it is produced.

    Implementations must be cheap: on_equity_point runs once per tick inside the
    event loop, so a 5-month single-symbol backtest calls it ~40,000 times and a
    230-task batch calls it about nine million times in total.
    """

    @abstractmethod
    def on_equity_point(self, point: EquityPoint, *, revises_previous: bool) -> None:
        """
        One equity observation.

        revises_previous=True means this point supersedes the one already
        emitted for the same timestamp rather than following it. Portfolio
        updates twice at the same instant when Engine executes `on_end()`
        signals against the final bar, and the two are different events: a live
        chart should overwrite its last point, while an append-only sink should
        record a correction. Only the sink knows which is right, so the
        distinction is carried here rather than resolved upstream -- and it has
        to be in the signature from the start, since adding it later would
        change every implementation.
        """

    def close(self) -> None:
        """Called once when the run ends. Default does nothing."""


class NullRecorder(CurveRecorder):
    """
    Records nothing. The default, so installing the hook changes no behaviour.

    Deliberately not `None`: a null object keeps the call site unconditional, so
    there is one code path through the event loop rather than a branch that is
    only exercised when someone opts in.
    """

    __slots__ = ()

    def on_equity_point(self, point: EquityPoint, *, revises_previous: bool) -> None:
        pass


class InMemoryRecorder(CurveRecorder):
    """
    Keeps every point. Not used by the engine -- `Portfolio` already retains the
    history -- but it makes the interface testable without a file or a socket,
    and it is the reference for what a real sink receives.
    """

    def __init__(self) -> None:
        self.points: list[EquityPoint] = []
        self.revisions = 0

    def on_equity_point(self, point: EquityPoint, *, revises_previous: bool) -> None:
        if revises_previous and self.points:
            self.points[-1] = point
            self.revisions += 1
        else:
            self.points.append(point)

    def history(self) -> Sequence[EquityPoint]:
        return self.points
