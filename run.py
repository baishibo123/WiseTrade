# run.py example
import logging

from core.engine import Engine
from strategies import BuyAndHold
import pandas as pd
from datafeed.db_feed import SQLiteFeed

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

if __name__ == '__main__':
    feed = SQLiteFeed("AAPL", 1751414400000, 1756771200000)
    result = Engine(feed=feed, strategy_class=BuyAndHold.BuyAndHold, strategy_params=None).run()
    print(result)
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




