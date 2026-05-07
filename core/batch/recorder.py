"""
CurveRecorder abstraction (ADR-008).

Phase 1: only an end-of-run Parquet writer is implemented (the in-memory
equity history fits comfortably). The streaming sibling will appear when
per-stock curves are added and memory pressure becomes real.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterable, Sequence


EQUITY_COLUMNS = ["timestamp", "equity", "cash", "positions_value", "num_positions"]


class CurveRecorder(ABC):
    """Stores time-series curve data for one run. Atomic on commit (ADR-006)."""

    @abstractmethod
    def write_equity_history(self, history: Sequence[tuple]) -> Path:
        """Persist the full equity history. Returns the final committed path."""
        ...


class ParquetEquityRecorder(CurveRecorder):
    """
    Writes the portfolio equity history to a single Parquet file via pandas.

    Atomic via temp + rename. Parquet's footer is the file-level commit
    signal — a partial write produces a footerless file that pyarrow rejects.
    """

    def __init__(self, output_path: Path):
        self.output_path = Path(output_path)

    def write_equity_history(self, history: Iterable[tuple]) -> Path:
        import pandas as pd  # local import: pandas is heavy, only needed in worker

        rows = list(history)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

        if not rows:
            df = pd.DataFrame(columns=EQUITY_COLUMNS)
        else:
            df = pd.DataFrame(rows, columns=EQUITY_COLUMNS)

        tmp = self.output_path.with_suffix(self.output_path.suffix + ".tmp")
        df.to_parquet(tmp, engine="pyarrow", index=False)
        os.replace(tmp, self.output_path)
        return self.output_path
