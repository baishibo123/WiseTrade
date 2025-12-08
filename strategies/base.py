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
        self.history: Deque[float] = deque(maxlen=self._lookback)
        self.timestamps: Deque[float] = deque(maxlen=self._lookback)


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
        self.history.append(bar.close)
        self.timestamps.append(bar.datetime)

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

    # ------------------------------------------------------------------
    # Optional: convenience properties
    # ------------------------------------------------------------------
    @property
    def name(self) -> str:
        """Human-readable name — override in subclass"""
        return self.__class__.__name__

    def __str__(self) -> str:
        return f"{self.name}({self.params})"

    # --- Lightweight Indicator Helpers ---

    def sma(self, period: int) -> Optional[float]:
        """Calculates SMA on the fly from self.history."""
        if len(self.history) < period:
            return None
        # Optimization: accessing the last 'period' items of a deque
        # is faster via list slicing if converted, but for small N, standard iteration is fine.
        # We slice the last N items.
        return statistics.mean(list(self.history)[-period:])