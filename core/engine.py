"""
Event-driven backtesting engine for multi-symbol portfolios
Orchestrates data flow, strategy execution, and portfolio management
"""

from typing import Dict, List, Any, Type, Optional
import logging
from datetime import datetime

from datafeed.db_feed import BaseFeed, DatabaseFeed
from database.schema import Bar
from strategies.base import Strategy
from core.portfolio import Portfolio
from core.analyzer import Analyzer
from core.time_iterator import TimeAlignedIterator


class Engine:
    """
    Event-driven backtesting engine for multi-symbol portfolios

    Responsibilities:
    - Create and synchronize data feeds
    - Coordinate strategy and portfolio
    - Execute event loop
    - Generate performance analytics

    Architecture:
        Feeds → TimeAlignedIterator → Strategy → Portfolio → Analyzer

    Usage:
        engine = Engine(
            universe=["AAPL", "MSFT", "GOOGL"],
            strategy_class=MyStrategy,
            start_datetime=start_ts,
            end_datetime=end_ts,
            strategy_params={"fast": 10, "slow": 20},
            portfolio_config={
                "initial_cash": 100_000,
                "max_positions": 10,
                "max_position_pct": 0.3
            }
        )

        analyzer = engine.run()
        print(analyzer.metrics)
    """

    def __init__(
            self,
            universe: List[str],
            strategy_class: Type[Strategy],
            start_datetime: int,
            end_datetime: int,
            strategy_params: Optional[Dict[str, Any]] = None,
            portfolio_config: Optional[Dict[str, Any]] = None,
            feed_class: Type[BaseFeed] = DatabaseFeed  # ← Changed default handling
    ):
        """
        Initialize backtesting engine

        Args:
            universe: List of symbols to backtest
            strategy_class: Strategy class (not instance)
            start_datetime: Start timestamp (Unix millis UTC)
            end_datetime: End timestamp (Unix millis UTC)
            strategy_params: Parameters passed to strategy __init__
            portfolio_config: Portfolio configuration dict
                - initial_cash: Starting capital (default 100_000)
                - max_positions: Max simultaneous positions (default 10)
                - max_position_pct: Max position size as % (default 0.3)
                - min_trade_size: Minimum trade size (default 0.1)
                - min_trade_size_per_symbol: Asset-specific overrides
            feed_class: Feed class to use (default SQLiteFeed)
        """
        self.universe = universe
        self.strategy_class = strategy_class
        self.start_datetime = start_datetime
        self.end_datetime = end_datetime
        self.strategy_params = strategy_params or {}
        if feed_class is None:
            feed_class = DatabaseFeed

        self.feed_class = feed_class

        # Parse portfolio config
        portfolio_config = portfolio_config or {}
        self.initial_cash = portfolio_config.get("initial_cash", 100_000.0)
        self.max_positions = portfolio_config.get("max_positions", 10)
        self.max_position_pct = portfolio_config.get("max_position_pct", 0.3)
        self.min_trade_size = portfolio_config.get("min_trade_size", 0.1)
        self.min_trade_size_per_symbol = portfolio_config.get("min_trade_size_per_symbol", {})

        # Create feeds (deferred until run())
        self.feeds: Dict[str, BaseFeed] = {}

        logging.info(
            f"Engine initialized: {len(universe)} symbols, "
            f"strategy={strategy_class.__name__}, "
            f"period={self._format_timestamp(start_datetime)} to {self._format_timestamp(end_datetime)}"
        )

    # ========================================================================
    # Main Execution
    # ========================================================================

    def run(self) -> Analyzer:
        """
        Execute backtest and return performance analytics

        Returns:
            Analyzer object with metrics, trades, and equity curve
        """
        logging.info(f"Starting backtest: {self.strategy_class.__name__} on {len(self.universe)} symbols")

        # 1. Create feeds
        self._create_feeds()

        # 2. Initialize components
        strategy = self._create_strategy()
        portfolio = self._create_portfolio()

        # 3. Wire components together
        strategy.portfolio = portfolio

        # 4. Lifecycle: start
        strategy.on_start()

        # 5. Create time-aligned iterator
        iterator = TimeAlignedIterator(self.feeds)

        # 6. Main event loop
        bar_count = 0
        last_timestamp = None
        last_bars = {}

        for timestamp, bars in iterator:
            bar_count += 1
            last_timestamp = timestamp
            last_bars = bars

            # Update strategy history for each symbol
            for symbol, bar in bars.items():
                strategy.update_bar(symbol, bar)

            # Strategy generates signals
            signals = strategy.next(bars)

            # Portfolio processes signals and executes trades
            if signals:
                portfolio.process_signals(signals, bars)

            # Update equity curve
            portfolio.update(bars, timestamp)

            # Progress logging
            if bar_count % 100_000 == 0:
                dt_str = self._format_timestamp(timestamp)
                logging.info(
                    f"   → {bar_count:,} bars | {dt_str} | "
                    f"Equity: ${portfolio.total_equity:,.0f} | "
                    f"Positions: {len(portfolio.positions)}/{self.max_positions} | "
                    f"Cash: ${portfolio.cash:,.0f}"
                )

        # 7. Lifecycle: end
        final_signals = strategy.on_end()
        if final_signals and last_timestamp and last_bars:
            # Execute final exit signals
            portfolio.process_signals(final_signals, last_bars)
            portfolio.update(last_bars, last_timestamp)

        # 8. Create analyzer
        analyzer = Analyzer(
            portfolio=portfolio,
            universe=self.universe,
            strategy_name=strategy.name,
            bar_count=bar_count
        )

        logging.info(
            f"Backtest complete | "
            f"Final equity: ${analyzer.metrics['total_equity']:,.0f} | "
            f"Return: {analyzer.metrics['total_return_pct']:.2f}% | "
            f"Sharpe: {analyzer.metrics['sharpe']:.2f} | "
            f"Total bars: {bar_count:,}"
        )

        return analyzer

    # ========================================================================
    # Component Creation
    # ========================================================================

    def _create_feeds(self):
        """Create data feeds for all symbols"""
        for symbol in self.universe:
            try:
                feed = self.feed_class(
                    symbol=symbol,
                    start_datetime=self.start_datetime,
                    end_datetime=self.end_datetime
                )
                self.feeds[symbol] = feed
            except Exception as e:
                logging.error(f"Failed to create feed for {symbol}: {e}")
                raise

    def _create_strategy(self) -> Strategy:
        """Create and configure strategy instance"""
        return self.strategy_class(
            universe=self.universe,
            params=self.strategy_params
        )

    def _create_portfolio(self) -> Portfolio:
        """Create and configure portfolio instance"""
        return Portfolio(
            initial_cash=self.initial_cash,
            max_positions=self.max_positions,
            min_trade_size=self.min_trade_size,
            min_trade_size_per_symbol=self.min_trade_size_per_symbol,
            max_position_pct=self.max_position_pct
        )

    # ========================================================================
    # Utilities
    # ========================================================================

    def _format_timestamp(self, timestamp: int) -> str:
        """Format Unix millis timestamp as readable string"""
        return datetime.utcfromtimestamp(timestamp / 1000).strftime("%Y-%m-%d %H:%M")

    # ========================================================================
    # Class Methods - Batch Execution
    # ========================================================================

    @classmethod
    def run_multiple(
            cls,
            universe: List[str],
            strategy_classes: List[Type[Strategy]],
            start_datetime: int,
            end_datetime: int,
            strategy_params: Optional[Dict[str, Any]] = None,
            portfolio_config: Optional[Dict[str, Any]] = None,
            show_progress: bool = True
    ) -> Dict[str, Analyzer]:
        """
        Run multiple strategies on same universe (for comparison)

        Args:
            universe: List of symbols
            strategy_classes: List of strategy classes to test
            start_datetime: Start timestamp
            end_datetime: End timestamp
            strategy_params: Parameters passed to all strategies
            portfolio_config: Portfolio configuration
            show_progress: Show progress bar

        Returns:
            Dict mapping strategy name -> Analyzer
        """
        from tqdm import tqdm

        results = {}
        iterator = tqdm(strategy_classes, desc="Running strategies") if show_progress else strategy_classes

        for strategy_class in iterator:
            try:
                engine = cls(
                    universe=universe,
                    strategy_class=strategy_class,
                    start_datetime=start_datetime,
                    end_datetime=end_datetime,
                    strategy_params=strategy_params,
                    portfolio_config=portfolio_config
                )

                analyzer = engine.run()
                results[strategy_class.__name__] = analyzer

                if show_progress and isinstance(iterator, tqdm):
                    iterator.set_postfix({
                        "strategy": strategy_class.__name__,
                        "return": f"{analyzer.metrics['total_return_pct']:+.1f}%",
                        "sharpe": f"{analyzer.metrics['sharpe']:.2f}"
                    })

            except Exception as e:
                logging.error(f"Failed on {strategy_class.__name__}: {e}")
                continue

        return results

    @classmethod
    def run_parameter_sweep(
            cls,
            universe: List[str],
            strategy_class: Type[Strategy],
            start_datetime: int,
            end_datetime: int,
            param_grid: Dict[str, List[Any]],
            portfolio_config: Optional[Dict[str, Any]] = None,
            show_progress: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Run strategy with multiple parameter combinations

        Args:
            universe: List of symbols
            strategy_class: Strategy class to test
            start_datetime: Start timestamp
            end_datetime: End timestamp
            param_grid: Parameter grid to sweep
                e.g., {"fast": [5, 10, 20], "slow": [20, 50, 100]}
            portfolio_config: Portfolio configuration
            show_progress: Show progress bar

        Returns:
            List of dicts with params and results
        """
        from itertools import product
        from tqdm import tqdm

        # Generate all parameter combinations
        param_names = list(param_grid.keys())
        param_values = list(param_grid.values())
        combinations = list(product(*param_values))

        results = []
        iterator = tqdm(combinations, desc="Parameter sweep") if show_progress else combinations

        for combo in iterator:
            params = dict(zip(param_names, combo))

            try:
                engine = cls(
                    universe=universe,
                    strategy_class=strategy_class,
                    start_datetime=start_datetime,
                    end_datetime=end_datetime,
                    strategy_params=params,
                    portfolio_config=portfolio_config
                )

                analyzer = engine.run()

                result = {
                    "params": params,
                    "metrics": analyzer.metrics
                }
                results.append(result)

                if show_progress and isinstance(iterator, tqdm):
                    iterator.set_postfix({
                        "params": str(params),
                        "return": f"{analyzer.metrics['total_return_pct']:+.1f}%"
                    })

            except Exception as e:
                logging.error(f"Failed on params {params}: {e}")
                continue

        return results


# ============================================================================
# Testing utility
# ============================================================================

def test_engine():
    """
    Test Engine with mock strategy
    """
    from database.schema import Bar
    from strategies.base import Strategy
    from strategies.indicators import calculate_sma

    print("\n=== Engine Test ===")

    # Create simple test strategy
    class SimpleStrategy(Strategy):
        def _update_indicators(self, symbol: str):
            closes = self.get_closes(symbol)
            self._indicators[symbol]["sma_20"] = calculate_sma(closes, 20)

        def next(self, bars: Dict[str, Bar]) -> Dict[str, dict]:
            signals = {}

            for symbol, bar in bars.items():
                sma = self._indicators[symbol].get("sma_20")

                if sma is None:
                    continue

                # Buy if above SMA and no position
                if bar.close > sma and not self.has_position(symbol):
                    signals[symbol] = {
                        "action": "BUY",
                        "score": 0.8,
                        "quantity": 10.0
                    }

                # Sell if below SMA and have position
                elif bar.close < sma and self.has_position(symbol):
                    signals[symbol] = {
                        "action": "SELL",
                        "score": 0.8
                    }

            return signals

    # Note: This test requires actual data in database
    # For now, just test initialization

    try:
        engine = Engine(
            universe=["AAPL", "MSFT"],
            strategy_class=SimpleStrategy,
            start_datetime=1609459200000,  # 2021-01-01
            end_datetime=1640995200000,  # 2022-01-01
            strategy_params={},
            portfolio_config={
                "initial_cash": 100_000,
                "max_positions": 5
            }
        )

        print("✓ Engine initialized successfully")
        print(f"  Universe: {engine.universe}")
        print(f"  Strategy: {engine.strategy_class.__name__}")
        print(f"  Capital: ${engine.initial_cash:,.0f}")

        # Uncomment to run full backtest (requires data):
        # analyzer = engine.run()
        # print(f"\nResults:")
        # print(f"  Final equity: ${analyzer.metrics['total_equity']:,.0f}")
        # print(f"  Return: {analyzer.metrics['total_return_pct']:.2f}%")

    except Exception as e:
        print(f"✗ Engine test failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    test_engine()