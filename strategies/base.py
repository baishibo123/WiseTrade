# strategies/base.py
from typing import Any, Dict, Optional
from abc import ABC, abstractmethod

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

    # ------------------------------------------------------------------
    # Lifecycle methods — called by Engine
    # ------------------------------------------------------------------
    def on_start(self) -> None:
        """
        Called once before the first bar
        Use for: indicator initialization, warm-up, logging
        """
        pass

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