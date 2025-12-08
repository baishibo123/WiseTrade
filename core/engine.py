# core/engine.py
from typing import Dict, List, Any, Type, Optional
import logging
from datetime import datetime

from datafeed.db_feed import BaseFeed
from database.schema import Bar
from strategies.base import Strategy
from core.portfolio import Portfolio
from core.analyzer import Analyzer


class Engine:
    """
    The pure, clean, event-driven backtesting engine
    One Engine = one symbol + one strategy + one backtest run
    """

    def __init__(
        self,
        feed: BaseFeed, # feed following BaseFeed interface
        strategy_class: Type[Strategy],
        strategy_params: Optional[Dict[str, Any]] = None,
        initial_cash: float = 100_000.0,
        min_trade_size: float = 0.1
    ):
        self.feed = feed
        self.strategy_class = strategy_class
        self.strategy_params = strategy_params or {}
        self.initial_cash = float(initial_cash)
        self.min_trade_size = float(min_trade_size)

    def run(self) -> Analyzer:
        """
        Execute full backtest → return complete results
        This is the method you call from run.py or notebooks
        """
        # Retrieve symbol name from feed for logging
        symbol = getattr(self.feed, "symbol", "UNKNOWN")
        logging.info(f"Starting backtest: {self.strategy_class.__name__} on {symbol}")

        # 1. Initialize strategy and portfolio
        strategy: Strategy = self.strategy_class(params=self.strategy_params)
        portfolio = Portfolio(
            initial_cash=self.initial_cash,
            min_trade_size=self.min_trade_size
        )

        # 2. Bind them together
        strategy.portfolio = portfolio

        # 3. Lifecycle: start
        strategy.on_start()
        last_bar: Optional[Bar] = None
        # 4. Main event-driven loop
        bar_count = 0
        for bar in self.feed:
            bar_count += 1
            last_bar = bar
            strategy.update_bar(bar)
            # Strategy decides what to do with this bar
            strategy.next(bar)

            # Update equity using latest close price
            portfolio.update(bar)

            # heads-up logging every 100k bars
            if bar_count % 100_000 == 0:
                dt_str = datetime.utcfromtimestamp(bar.datetime / 1000).strftime("%Y-%m-%d %H:%M")
                logging.info(
                    f"   → {bar_count:,} bars | {dt_str} | "
                    f"Equity: ${portfolio.total_equity:,.0f}"
                )

        #Safety: if feed was empty, set a dummy bar so on_end() can close position
        if bar_count == 0 and last_bar is None:
            logging.warning(f"No data for {symbol} in date range")
            # Optionally: create a dummy bar with price=0 or skip
        else:
            # on_end() may want to close position — it can use strategy.bar safely
            pass

        # 5. Lifecycle: end
        strategy.on_end()

        # 6. Analyze results
        analyzer = Analyzer(portfolio)
        analyzer.symbol = symbol
        analyzer.strategy_name = strategy.name
        analyzer.bar_count = bar_count

        logging.info(
            f"Backtest complete | "
            f"Final equity: ${analyzer.metrics['total_equity']:,.0f} | "
            f"Total return: {analyzer.metrics['total_return_pct']:.2f}% | "
            f"Bars: {bar_count:,}"
        )

        return analyzer

    # ------------------------------------------------------------------
    # Class method: run many symbols with same strategy
    # ------------------------------------------------------------------
    @classmethod
    def run_multiple(
        cls,
        symbols: List[str],
        strategy_class: Type[Strategy],
        start_datetime: int,    # designated to run different symbols on the same time frame
        end_datetime: int,
        strategy_params: Optional[Dict[str, Any]] = None,
        initial_cash: float = 100_000.0,
        min_trade_size: float = 0.1,
        show_progress: bool = True
    ) -> Dict[str, Analyzer]:
        """
        Run same strategy on multiple symbols → return dict of results
        Perfect for ranking and comparison
        """
        from tqdm import tqdm

        results = {}
        iterator = tqdm(symbols, desc=f"Running {strategy_class.__name__}", leave=True) if show_progress else symbols

        for symbol in iterator:
            try:
                from datafeed.db_feed import SQLiteFeed
                feed = SQLiteFeed(
                    symbol=symbol,
                    start_datetime=start_datetime,
                    end_datetime=end_datetime
                )

                engine = cls(
                    feed=feed,
                    strategy_class=strategy_class,
                    strategy_params=strategy_params,
                    initial_cash=initial_cash,
                    min_trade_size=min_trade_size
                )
                result = engine.run()
                results[symbol] = result

                if show_progress and isinstance(iterator, tqdm):
                    iterator.set_postfix({
                        "last": symbol,
                        "return": f"{result.metrics['total_return_pct']:+.1f}%"
                    })

            except Exception as e:
                logging.error(f"Failed on {symbol}: {e}")
                continue

        return results