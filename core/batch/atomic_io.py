"""
Atomic file writes via temp-file + os.replace().

See ADR-006: every per-run output file uses this pattern so a crash mid-write
cannot leave a half-written final file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def atomic_write_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_json(path: Path, data: Any, indent: int = 2) -> None:
    atomic_write_text(path, json.dumps(data, indent=indent, default=str))


def atomic_rename(tmp_path: Path, final_path: Path) -> None:
    """For files written via a streaming writer to <name>.tmp."""
    os.replace(tmp_path, final_path)
