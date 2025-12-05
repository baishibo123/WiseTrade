# core/portfolio.py
from typing import List, Dict, Any
from datetime import datetime
import pandas as pd

from database.schema import Bar


class Portfolio:
    """
    Simple but realistic single-asset portfolio
    - Tracks cash, position, equity over time
    - Records full trade log (for win rate, etc.)
    - Records equity curve every bar (for drawdown, Sharpe)
    No commission/slippage yet (we add later)
    """


    def __init__(self, initial_cash: float = 100_000.0, min_trade_size: float = 0.1):
        self.min_trade_size = float(min_trade_size)

        self.initial_cash = float(initial_cash)

        # Current state
        self.cash: float = initial_cash
        self.position: float = 0.0  # shares/contracts held
        self.entry_price: float = 0.0  # for unrealized P&L (we are only dealing with one asset)

        # History for analyzer
        self._history: List[tuple[int, float]] = []  # (datetime_ms, equity)
        self._trades: List[Dict[str, Any]] = []  # full trade log

    # ------------------------------------------------------------------
    # 1. Order execution
    # ------------------------------------------------------------------
    def buy(self, size: float, price: float) -> None:
        if size <= 0 or price <= 0:
            return

        max_affordable = self.cash / price if price > 0 else 0.0
        desired_size = min(size, max_affordable)

        if desired_size < self.min_trade_size:
            return  # Can't buy at least 1 share → skip

        actual_size = desired_size
        cost = actual_size * price

        self.cash -= cost
        self.position += actual_size
        self.entry_price = price

        self._trades.append({
            "datetime": None,
            "type": "BUY",
            "size": round(actual_size, 6),
            "price": price,
            "cost": cost
        })

    def sell(self, size: float, price: float) -> None:
        if size <= 0 or price <= 0 or self.position <= 0:
            return

        sellable = min(size, self.position)
        if sellable < self.min_trade_size:
            return

        proceeds = sellable * price
        self.cash += proceeds
        self.position -= sellable

        if self.position < self.min_trade_size:
            self.position = 0.0
            self.entry_price = 0.0

        self._trades.append({
            "datetime": None,
            "type": "SELL",
            "size": round(sellable, 6),
            "price": price,
            "proceeds": proceeds
        })

    def sell_all(self, price: float) -> None:
        """Close entire position at current price"""
        if self.position > 0 and price > 0:
            self.sell(self.position, price)

    # ------------------------------------------------------------------
    # 2. Mark-to-market — called every bar by Engine
    # ------------------------------------------------------------------
    def update(self, bar: Bar) -> float:
        """
        Update equity using current close price
        Record equity point in history
        Returns current total equity
        """
        if self.position > 0:
            position_value = self.position * bar.close
        else:
            position_value = 0.0

        current_equity = self.cash + position_value

        # Record equity curve point
        self._history.append((bar.datetime, current_equity))

        # Update last trade datetime (so Analyzer knows when trade happened)
        if self._trades and self._trades[-1]["datetime"] is None:
            self._trades[-1]["datetime"] = bar.datetime

        return current_equity

    # ------------------------------------------------------------------
    # 3. State queries — used by Strategy
    # ------------------------------------------------------------------
    @property
    def is_long(self) -> bool:
        return self.position > 0

    @property
    def cash_available(self) -> float:
        return self.cash

    @property
    def total_equity(self) -> float:
        # Only valid after last update()
        if not self._history:
            return self.initial_cash
        return self._history[-1][1]

    # ------------------------------------------------------------------
    # 4. Results for Analyzer
    # ------------------------------------------------------------------
    @property
    def trades(self) -> List[Dict[str, Any]]:
        """Immutable copy of trade log"""
        return self._trades.copy()

    @property
    def equity_curve(self) -> pd.DataFrame:
        """DataFrame: datetime (ms) → equity"""
        if not self._history:
            return pd.DataFrame(columns=["datetime", "equity"])

        df = pd.DataFrame(self._history, columns=["datetime", "equity"])
        df["datetime"] = pd.to_datetime(df["datetime"], unit="ms", utc=True)
        df = df.set_index("datetime")
        return df

    def reset(self) -> None:
        """Reset to initial state — useful for multiple runs"""
        self.__init__(initial_cash=self.initial_cash)