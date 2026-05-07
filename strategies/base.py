"""
Base class for multi-symbol strategies
Maintains per-symbol history and provides signal generation framework
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any, Deque
from collections import deque
import logging

from database.schema import Bar
from strategies.indicators import *  # Import all indicator functions


class Strategy(ABC):
    """
    Base class for multi-symbol strategies

    Responsibilities:
    - Maintain per-symbol bar history
    - Calculate indicators (override _update_indicators)
    - Generate signals based on current market state

    Lifecycle:
    1. __init__: Strategy configuration
    2. on_start: Called once before backtest
    3. update_bar: Called for each bar (updates history & indicators)
    4. next: Called after all symbols updated (generates signals)
    5. on_end: Called once after backtest (cleanup, final exits)

    VERSIONING (ADR-004):
        The batch backtester hashes (class name, VERSION, params, universe,
        time range) into a deterministic run_id used for resumability. If you
        change strategy behavior — even a one-line bug fix — bump VERSION on
        the subclass, otherwise opt-in resume will return stale results from
        a prior run that used the old logic. Renaming params or the class
        also invalidates the hash automatically; only behavior changes that
        leave the surface API alone need a manual bump.

    Usage:
        class MyStrategy(Strategy):
            VERSION = "1.0"   # bump on any behavior change

            def _update_indicators(self, symbol: str):
                # Calculate indicators for this symbol
                closes = self.get_closes(symbol)
                self._indicators[symbol]["sma_20"] = calculate_sma(closes, 20)

            def next(self, bars: Dict[str, Bar]) -> Dict[str, dict]:
                signals = {}
                for symbol, bar in bars.items():
                    sma = self._indicators[symbol].get("sma_20")
                    if sma and bar.close > sma:
                        signals[symbol] = {"action": "BUY", "score": 0.8, "quantity": 10.0}
                return signals
    """

    VERSION: str = "1.0"

    def __init__(
            self,
            universe: List[str],
            params: Optional[Dict[str, Any]] = None
    ):
        """
        Initialize strategy

        Args:
            universe: List of symbols to track
            params: Strategy parameters (e.g., lookback periods, thresholds)
        """
        self.universe = universe
        self.params = params or {}
        self.portfolio = None  # Will be injected by Engine

        # Per-symbol bar history
        self._lookback = self.params.get('max_lookback', 300)
        self.history: Dict[str, Deque[Bar]] = {
            symbol: deque(maxlen=self._lookback) for symbol in universe
        }

        # Per-symbol indicators (cached values)
        self._indicators: Dict[str, Dict[str, Any]] = {
            symbol: {} for symbol in universe
        }

        # Strategy metadata
        self.name = self.__class__.__name__

        logging.info(
            f"Strategy '{self.name}' initialized: "
            f"{len(universe)} symbols, lookback={self._lookback}"
        )

    # ========================================================================
    # Lifecycle Hooks
    # ========================================================================

    def on_start(self):
        """
        Called once before backtest starts

        Override to perform initialization (e.g., load reference data)
        """
        pass

    def on_end(self) -> Optional[Dict[str, dict]]:
        """
        Called once after backtest ends

        Override to perform cleanup or force-close positions

        Returns:
            Optional signals dict (e.g., to close all positions)
        """
        # Default: close all open positions
        if self.portfolio and self.portfolio.positions:
            logging.info(f"Strategy '{self.name}' ending: closing {len(self.portfolio.positions)} positions")
            return {
                symbol: {"action": "SELL", "score": 1.0}
                for symbol in self.portfolio.positions.keys()
            }
        return None

    # ========================================================================
    # Bar Processing
    # ========================================================================

    def update_bar(self, symbol: str, bar: Bar):
        """
        Update history and indicators for a single symbol

        Called by Engine for each bar before next()

        Args:
            symbol: Symbol ticker
            bar: Current bar data
        """
        # Add bar to history
        self.history[symbol].append(bar)

        # Recalculate indicators for this symbol
        self._update_indicators(symbol)

    def _update_indicators(self, symbol: str):
        """
        Calculate/update indicators for a single symbol

        Override in subclass to implement custom indicators
        Store results in self._indicators[symbol]

        Example:
            closes = self.get_closes(symbol)
            self._indicators[symbol]["sma_20"] = calculate_sma(closes, 20)
            self._indicators[symbol]["rsi_14"] = calculate_rsi(closes, 14)
        """
        pass  # Base class has no indicators

    @abstractmethod
    def next(self, bars: Dict[str, Bar]) -> Dict[str, dict]:
        """
        Generate signals for all symbols at current timestamp

        Called after all symbols' bars have been updated

        Args:
            bars: Current bars for all symbols at this timestamp
                  Format: {symbol: Bar}

        Returns:
            Signals dict: {symbol: signal}

            Signal format:
            {
                "action": "BUY" | "SELL" | "HOLD",
                "score": float (0.0-1.0, for ranking),

                # Optional quantity specification:
                "quantity": float (explicit shares),
                # OR
                "target_allocation": float (% of portfolio, e.g., 0.10 = 10%),

                # For SELL signals, optional:
                "sell_pct": float (% of position, e.g., 0.5 = 50%)
            }
        """
        pass

    # ========================================================================
    # Data Access Helpers
    # ========================================================================

    def get_closes(self, symbol: str, n: Optional[int] = None) -> List[float]:
        """
        Get recent close prices for symbol

        Args:
            symbol: Symbol ticker
            n: Number of recent bars (None = all available)

        Returns:
            List of close prices (most recent last)
        """
        closes = [bar.close for bar in self.history[symbol]]
        return closes[-n:] if n else closes

    def get_opens(self, symbol: str, n: Optional[int] = None) -> List[float]:
        """
        Get recent open prices for symbol

        Args:
            symbol: Symbol ticker
            n: Number of recent bars (None = all available)

        Returns:
            List of open prices (most recent last)
        """
        opens = [bar.open for bar in self.history[symbol]]
        return opens[-n:] if n else opens

    def get_highs_lows(self, symbol: str, n: Optional[int] = None) -> tuple[List[float], List[float]]:
        """
        Get recent high and low prices for symbol

        Args:
            symbol: Symbol ticker
            n: Number of recent bars (None = all available)

        Returns:
            (highs, lows) tuple of lists
        """
        bars = list(self.history[symbol])[-n:] if n else list(self.history[symbol])
        highs = [bar.high for bar in bars]
        lows = [bar.low for bar in bars]
        return highs, lows

    def get_volumes(self, symbol: str, n: Optional[int] = None) -> List[float]:
        """
        Get recent volumes for symbol

        Args:
            symbol: Symbol ticker
            n: Number of recent bars (None = all available)

        Returns:
            List of volumes (most recent last)
        """
        volumes = [bar.volume for bar in self.history[symbol]]
        return volumes[-n:] if n else volumes

    # ========================================================================
    # Position Helpers (Pragmatic for now, may refactor later)
    # ========================================================================

    def has_position(self, symbol: str) -> bool:
        """Check if currently holding position in symbol"""
        return symbol in self.portfolio.positions if self.portfolio else False

    def get_position_size(self, symbol: str) -> float:
        """Get current position size (shares) for symbol"""
        if self.portfolio and symbol in self.portfolio.positions:
            return self.portfolio.positions[symbol].shares
        return 0.0

    def get_position_pnl(self, symbol: str, current_price: float) -> Optional[float]:
        """
        Get unrealized P&L for position

        Args:
            symbol: Symbol ticker
            current_price: Current market price

        Returns:
            Unrealized P&L or None if no position
        """
        if self.portfolio and symbol in self.portfolio.positions:
            return self.portfolio.positions[symbol].unrealized_pnl(current_price)
        return None


# ============================================================================
# Testing utility
# ============================================================================

def test_strategy_base():
    """
    Test Strategy base class with mock data
    """
    from database.schema import Bar

    print("\n=== Strategy Base Class Test ===")

    # Create concrete strategy for testing
    class TestStrategy(Strategy):
        def _update_indicators(self, symbol: str):
            # Calculate SMA using imported indicator function
            closes = self.get_closes(symbol)
            self._indicators[symbol]["sma_20"] = calculate_sma(closes, 20)
            self._indicators[symbol]["rsi_14"] = calculate_rsi(closes, 14)

        def next(self, bars: Dict[str, Bar]) -> Dict[str, dict]:
            signals = {}
            for symbol, bar in bars.items():
                sma = self._indicators[symbol].get("sma_20")
                rsi = self._indicators[symbol].get("rsi_14")

                if sma and rsi:
                    if bar.close > sma and rsi < 70:
                        signals[symbol] = {
                            "action": "BUY",
                            "score": 0.8,
                            "quantity": 10.0
                        }
            return signals

    # Initialize strategy
    strategy = TestStrategy(
        universe=["AAPL", "MSFT"],
        params={"max_lookback": 50}
    )

    # Simulate bars
    for i in range(30):
        price_aapl = 150.0 + i * 0.5
        price_msft = 300.0 + i * 1.0

        bars = {
            "AAPL": Bar("AAPL", i * 1000, price_aapl, price_aapl, price_aapl + 1, price_aapl - 1, price_aapl, 1000),
            "MSFT": Bar("MSFT", i * 1000, price_msft, price_msft, price_msft + 1, price_msft - 1, price_msft, 2000)
        }

        # Update bars
        for symbol, bar in bars.items():
            strategy.update_bar(symbol, bar)

        # Generate signals
        signals = strategy.next(bars)

        if signals:
            print(f"\nTimestamp {i}:")
            for symbol, signal in signals.items():
                print(f"  {symbol}: {signal}")

    # Test data access helpers
    print(f"\nAAPL history length: {len(strategy.history['AAPL'])}")
    print(f"AAPL last 5 closes: {strategy.get_closes('AAPL', 5)}")
    print(f"AAPL last 5 opens: {strategy.get_opens('AAPL', 5)}")

    print("\n✓ Test complete")


if __name__ == "__main__":
    test_strategy_base()