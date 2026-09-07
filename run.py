"""
Single-strategy smoke run: one symbol, one strategy at a time, full summary.

Use this to look at one backtest in detail; use run_ranking.py to compare many.

This was broken from the moment the Engine moved to a multi-symbol API: it
imported SQLiteFeed, which does not exist (the class is DatabaseFeed), and
called Engine(feed=...), which is not the signature. It could not have run.
Neither could the two strategies it listed -- SMACrossover and SMA_ATR_Exit
still take __init__(self, params) and would raise on construction.
"""

import logging

from core.engine import Engine
from strategies.SMA_OS_Fixed import SMA_OS_Fixed
from strategies.SMA_OS_dynamic import SMA_OS_Dynamic

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

SYMBOL = "NVDA"
START_DATETIME = 1751414400000   # 2025-07-02
END_DATETIME = 1764057600000     # 2025-11-25

# One symbol holding the whole account, so the run is about the strategy rather
# than about position sizing -- the same shape PerSymbolBatchConfig uses.
PORTFOLIO = {"initial_cash": 100_000.0, "max_positions": 1, "max_position_pct": 1.0}

# SMACrossover and SMA_ATR_Exit are omitted: they are still on the old
# single-symbol Strategy API and need migrating before they can be run here.
STRATEGIES = [SMA_OS_Fixed, SMA_OS_Dynamic]


def main():
    for strategy_class in STRATEGIES:
        analyzer = Engine(
            universe=[SYMBOL],
            strategy_class=strategy_class,
            start_datetime=START_DATETIME,
            end_datetime=END_DATETIME,
            portfolio_config=PORTFOLIO,
        ).run()
        analyzer.print_summary()


if __name__ == "__main__":
    main()
