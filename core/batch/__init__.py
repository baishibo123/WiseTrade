"""
Batch backtesting infrastructure.

Public surface:
    - PortfolioBatchConfig, PerSymbolBatchConfig: define a batch
    - BatchTask, RunResult: worker contract
    - compute_run_id: deterministic hash for resumability
"""

from core.batch.types import BatchTask, RunResult, compute_run_id
from core.batch.config import PortfolioBatchConfig, PerSymbolBatchConfig

__all__ = [
    "BatchTask",
    "RunResult",
    "compute_run_id",
    "PortfolioBatchConfig",
    "PerSymbolBatchConfig",
]
