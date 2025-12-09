# run.py example
import logging

from core.engine import Engine
from strategies.SMA_OS_Fixed import SMA_OS_Fixed
from strategies.SMA_OS_dynamic import SMA_OS_Dynamic
from strategies.sma_crossover import SMACrossover
from strategies.sma_atr_exit import SMA_ATR_Exit
import pandas as pd
from datafeed.db_feed import SQLiteFeed

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

if __name__ == '__main__':
    feed = SQLiteFeed("nvda", 1751414400000, 1764057600000)
    result = Engine(feed=feed, strategy_class=SMA_OS_Fixed, strategy_params=None).run()
    result.print_summary()

    feed = SQLiteFeed("nvda", 1751414400000, 1764057600000)
    result = Engine(feed=feed, strategy_class=SMA_OS_Dynamic, strategy_params=None).run()
    result.print_summary()

    feed = SQLiteFeed("nvda", 1751414400000, 1764057600000)
    result = Engine(feed=feed, strategy_class=SMACrossover, strategy_params=None).run()
    result.print_summary()

    feed = SQLiteFeed("nvda", 1751414400000, 1764057600000)
    result = Engine(feed=feed, strategy_class=SMA_ATR_Exit, strategy_params=None).run()
    result.print_summary()

    # results = Engine.run_multiple(
    #
    #     symbols=["AAPL", "NVDA", "TSLA", "MSFT", "AMD"],
    #     strategy_class=BuyAndHold,
    #     start_datetime=...,  # Unix ms
    #     end_datetime=...,
    #     show_progress=True
    # )
    # pd.concat([r.summary_table() for r in results.values()]).to_csv("results.csv", index=False)




