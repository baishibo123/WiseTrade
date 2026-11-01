"""
The label journal: an append-only JSON-lines file of label operations (ADR-028).

    label_store.LabelStore ──► [ lock → read_from / append ] ──► labels.jsonl
                                                                 labels.lock

This module knows nothing about SQLite or about what a label means. It owns
three things only: the exclusive lock that serialises writers across processes,
durable appends, and reading lines back in `seq` order with damage detected
rather than guessed around.

Byte offsets are the cursor. The store remembers the offset just past the last
line it applied, so opening it costs a read of the lines appended since, not of
the whole history.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

JOURNAL_NAME = "labels.jsonl"
LOCK_NAME = "labels.lock"

log = logging.getLogger(__name__)


class JournalError(RuntimeError):
    """The journal is not in a state the store may open from. Never guessed around."""


@contextmanager
def locked(labels_dir: Path) -> Iterator[None]:
    """
    Exclusive, blocking, cross-process lock for the label directory.

    Held for a whole write -- SQLite apply, append, fsync, commit -- and for any
    replay, so two sessions or worktrees can never draw the same `seq`. Advisory:
    it binds only code that takes it, which is all code in this package.
    """
    with open(labels_dir / LOCK_NAME, "a+b") as f:
        _lock(f)
        try:
            yield
        finally:
            _unlock(f)


if os.name == "nt":  # pragma: no cover - exercised on Windows only
    import msvcrt

    _F_FULLFSYNC = None

    def _lock(f) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)

    def _unlock(f) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    _F_FULLFSYNC = getattr(fcntl, "F_FULLFSYNC", None)   # macOS only

    def _lock(f) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)

    def _unlock(f) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _fsync(fd: int) -> None:
    """
    Flush to stable storage. On macOS `fsync()` only hands the data to the
    drive, whose cache can still lose it on power loss; `F_FULLFSYNC` is the
    call that waits for the medium. Without it, the "durable before commit"
    ordering of ADR-028 does not hold on the machine this runs on.
    """
    if _F_FULLFSYNC is not None:
        fcntl.fcntl(fd, _F_FULLFSYNC)
    else:
        os.fsync(fd)


def size(labels_dir: Path) -> int:
    """Current journal length in bytes; 0 when it does not exist yet."""
    try:
        return (labels_dir / JOURNAL_NAME).stat().st_size
    except FileNotFoundError:
        return 0


def append(labels_dir: Path, op: dict[str, Any]) -> int:
    """
    Append one operation and fsync it. Returns the offset just past the line.

    Caller must hold `locked()`. One `write` of the whole line including its
    newline, so a crash leaves at most one torn final line -- which is exactly
    the case `read_from` repairs.
    """
    line = json.dumps(op, ensure_ascii=False, separators=(",", ":")) + "\n"
    with open(labels_dir / JOURNAL_NAME, "ab") as f:
        f.write(line.encode("utf-8"))
        f.flush()
        _fsync(f.fileno())
        return f.tell()


def read_from(labels_dir: Path, offset: int, last_seq: int) -> list[tuple[dict[str, Any], int]]:
    """
    Operations after `offset`, each paired with the offset just past its line.

    Caller must hold `locked()`. `last_seq` is the `seq` of the line ending at
    `offset` (0 at the start); every line read must continue it by exactly one,
    so a deleted or duplicated line is caught, not just a reordered one.

    A final fragment without a newline is a torn append whose transaction never
    committed -- `append` writes the newline in the same call, and the store
    commits only after the fsync returns. It is truncated and logged. Any other
    damage raises: it means the file was edited or replaced, and guessing would
    destroy the evidence.
    """
    path = labels_dir / JOURNAL_NAME
    end = size(labels_dir)
    if offset > end:
        raise JournalError(
            f"{path} is {end} bytes but the index has applied it up to byte {offset}: "
            f"the journal was truncated or replaced. Not opening."
        )
    if offset == end:
        return []
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read()

    torn = data.rpartition(b"\n")[2]
    if torn:
        data = data[:-len(torn)]
        cut = offset + len(data)
        log.warning("truncating torn final line of %s at byte %d (%d bytes): %r",
                    path, cut, len(torn), torn[:200])
        with open(path, "r+b") as f:
            f.truncate(cut)
            f.flush()
            _fsync(f.fileno())

    out = []
    pos = offset
    for raw in data.splitlines(keepends=True):
        pos += len(raw)
        try:
            op = json.loads(raw)
        except ValueError:
            raise JournalError(f"{path}: unparseable line ending at byte {pos}: {raw[:200]!r}") from None
        if not isinstance(op, dict) or op.get("seq") != last_seq + 1:
            raise JournalError(
                f"{path}: expected seq {last_seq + 1} in line ending at byte {pos}, "
                f"got {op.get('seq') if isinstance(op, dict) else op!r}"
            )
        last_seq = op["seq"]
        out.append((op, pos))
    return out
