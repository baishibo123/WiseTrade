"""
WiseTrade — strategy ranking across the TECH_100 universe.

Per-symbol mode (ADR-009): each (strategy, params, symbol) is one independent run
with its own $100k of capital. Results are written to results/<batch_id>/.

To rerun and resume an interrupted batch, pass the batch dir as resume_dir:
    config.run(resume_dir=Path("results/sma_ranking_20260506_153045"))
"""

from __future__ import annotations

from core.batch import PerSymbolBatchConfig
# SMACrossover and SMA_ATR_Exit still use the old single-symbol API and need
# migration before they can be batched. Re-add once they're on the new base.
from strategies.SMA_OS_dynamic import SMA_OS_Dynamic
from strategies.SMA_OS_Fixed import SMA_OS_Fixed


# Date range (Unix milliseconds UTC)
START_DATETIME = 1751414400000
END_DATETIME = 1764057600000


def main():
    config = PerSymbolBatchConfig(
        batch_name="sma_ranking",
        strategies=[
            (SMA_OS_Dynamic, {}),
            (SMA_OS_Fixed,   {}),
        ],
        universe="TECH_100",
        start_datetime=START_DATETIME,
        end_datetime=END_DATETIME,
        per_symbol_cash=100_000.0,
    )
    config.run()


if __name__ == "__main__":
    # Required for multiprocessing on Windows (spawn start method).
    main()
