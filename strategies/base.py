# strategies/base.py
from typing import Any, Dict, Optional, Deque
from abc import ABC, abstractmethod
from collections import deque
import statistics

from database.schema import Bar
from core.portfolio import Portfolio


class Strategy(ABC):
    """
    Abstract base class for all trading strategies, to be modified for complex strategies

    User must implement:
        - next(bar: Bar) → where all logic lives

    Optional overrides:
        - on_start()     → called once at beginning
        - on_end()       → called once at end

    Strategy has full access to:
        self.portfolio   → buy(), sell(), sell_all(), cash, position, etc.
        self.bar         → current bar (convenience)
    """

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        """
        params: dictionary of strategy parameters
        """
        self.params = params or {}
        self.portfolio: Optional[Portfolio] = None
        self.bar: Optional[Bar] = None  # current bar (set by Engine)

        # Event - Driven Data Series
        # To calculate whether the conditions are fulfilled or not, some history data
        # should be stored.
        # "maxlen" automatically drops old data when new data is appended.
        self._lookback = self.params.get('max_lookback', 300)
        self.history: Deque = deque(maxlen=self._lookback)


    # ------------------------------------------------------------------
    # Lifecycle methods — called by Engine
    # ------------------------------------------------------------------
    def on_start(self) -> None:
        """
        Called once before the first bar
        Use for: indicator initialization, warm-up, logging
        """
        pass

    def update_bar(self, bar) -> None:
        """
        Helper: Must be called at the start of next() to update history.
        """
        self.bar = bar
        self.history.append(bar)

    @abstractmethod
    def next(self, bar: Bar) -> None:
        """
        Called on EVERY bar
        Calculate indicators, make buy/sell decisions here
        """
        raise NotImplementedError("You must implement next() in your strategy")

    def on_end(self) -> None:
        """
        Called once after the last bar
        Use for: final cleanup, logging results, closing positions
        """
        # Optional: close position at last price
        if self.portfolio and self.bar and self.portfolio.is_long:
            self.portfolio.sell_all(self.bar.close)

    # ------------------------------------------------------------------
    # Helper methods — make strategy code cleaner
    # ------------------------------------------------------------------
    def get_param(self, name: str, default: Any = None) -> Any:
        """Safe access to self.params"""
        return self.params.get(name, default)

    # --- Helpers ---
    def sma(self, period: int) -> Optional[float]:
        """Calculates SMA using the .close attribute of stored bars."""
        if len(self.history) < period:
            return None
        # Extract closing prices from the last N bars
        closes = [b.close for b in list(self.history)[-period:]]
        return statistics.mean(closes)

    def atr(self, period: int) -> Optional[float]:
        """
        Calculates Average True Range (ATR).
        Logic: Average of the True Ranges over N periods.
        """
        if len(self.history) < period + 1:
            return None

        # We need the last N+1 bars to calculate N True Ranges
        # (because TR requires previous close)
        recent_bars = list(self.history)[-(period + 1):]
        true_ranges = []

        for i in range(1, len(recent_bars)):
            curr = recent_bars[i]
            prev = recent_bars[i - 1]

            # TR = Max(High-Low, |High-PrevClose|, |Low-PrevClose|)
            val1 = curr.high - curr.low
            val2 = abs(curr.high - prev.close)
            val3 = abs(curr.low - prev.close)

            true_ranges.append(max(val1, val2, val3))

        return statistics.mean(true_ranges)

    # ------------------------------------------------------------------
    # Optional: convenience properties
    # ------------------------------------------------------------------
    @property
    def name(self) -> str:
        """Human-readable name — override in subclass"""
        return self.__class__.__name__

    def __str__(self) -> str:
        return f"{self.name}({self.params})"
