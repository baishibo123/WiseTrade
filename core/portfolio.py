# core/portfolio.py
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
import pandas as pd
import logging

from database.schema import Bar

from dataclasses import dataclass


@dataclass
class Position:
    """
    Represents a position in a single symbol

    Attributes:
        symbol: Ticker symbol
        shares: Number of shares held (float for fractional shares)
        avg_cost: Average cost basis per share
    """
    symbol: str
    shares: float
    avg_cost: float
    entry_time: Optional[int] = None  # Future: track holding period
    stop_loss: Optional[float] = None  # Future: risk management

    def market_value(self, current_price: float) -> float:
        """Calculate current market value of position"""
        return self.shares * current_price

    def unrealized_pnl(self, current_price: float) -> float:
        """Calculate unrealized profit/loss"""
        return (current_price - self.avg_cost) * self.shares

class Portfolio:
    """
    Multi-asset portfolio with capital allocation and risk management

    Signal-driven execution: Strategy specifies what and how much to trade
    """

    def __init__(
            self,
            initial_cash: float = 100_000.0,
            max_positions: int = 10,
            min_trade_size: float = 0.1,
            min_trade_size_per_symbol: Optional[Dict[str, float]] = None,
            max_position_pct: float = 0.3
    ):
        """
        Initialize portfolio

        Args:
            initial_cash: Starting capital
            max_positions: Maximum number of simultaneous positions
            min_trade_size: Minimum trade size (shares), default 0.1 for stocks
            min_trade_size_per_symbol: Asset-specific overrides
                e.g., {"BTC-USD": 0.0001} for crypto
            max_position_pct: Maximum position size as % of initial portfolio
                e.g., 0.3 = no position can exceed 30% of initial capital
        """
        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        self.max_positions = max_positions
        self.min_trade_size = float(min_trade_size)
        self.min_trade_size_per_symbol = min_trade_size_per_symbol or {}
        self.max_position_pct = float(max_position_pct)

        # Positions: {symbol: Position}
        self.positions: Dict[str, Position] = {}

        # History tracking
        self._equity_history: List[Tuple[int, float, float, float, int]] = []
        self._trades: List[Dict[str, Any]] = []

        logging.info(
            f"Portfolio initialized: ${initial_cash:,.0f}, "
            f"max_positions={max_positions}, max_position={max_position_pct:.0%}"
        )

    # ========================================================================
    # Signal Processing
    # ========================================================================

    def process_signals(
            self,
            signals: Dict[str, dict],
            bars: Dict[str, 'Bar']
    ):
        """
        Process strategy signals and execute trades

        BUY signal formats:
        - {"action": "BUY", "score": 0.8, "quantity": 5.0}
        - {"action": "BUY", "score": 0.8, "target_allocation": 0.10}
        - {"action": "BUY", "score": 0.8}  # Default: 1 share

        SELL signal formats:
        - {"action": "SELL", "score": 0.5, "quantity": 3.0}
        - {"action": "SELL", "score": 0.5, "sell_pct": 0.5}  # 50% of position
        - {"action": "SELL", "score": 0.5}  # Default: close entire position
        """
        if not signals:
            return

        # Phase 1: Process exits first (free up capital and slots)
        sell_signals = {
            sym: sig for sym, sig in signals.items()
            if sig.get("action") == "SELL"
        }

        for symbol, signal in sell_signals.items():
            if symbol in self.positions and symbol in bars:
                self._execute_sell(symbol, bars[symbol], signal)

        # Phase 2: Process entries (ranked by score)
        buy_signals = {
            sym: sig for sym, sig in signals.items()
            if sig.get("action") == "BUY"
        }

        if not buy_signals:
            return

        # Rank by score (highest first)
        ranked_buys = sorted(
            buy_signals.items(),
            key=lambda x: x[1].get("score", 0.0),
            reverse=True
        )

        # Phase 3: Execute buys
        for symbol, signal in ranked_buys:
            if symbol not in bars:
                continue

            # Check position limit for NEW positions only
            if symbol not in self.positions:
                if len(self.positions) >= self.max_positions:
                    logging.debug(
                        f"BUY {symbol} skipped: max_positions ({self.max_positions}) reached"
                    )
                    continue

            self._execute_buy(symbol, bars[symbol], signal)

    # ========================================================================
    # Trade Execution
    # ========================================================================

    def _execute_buy(
            self,
            symbol: str,
            bar: 'Bar',
            signal: dict
    ):
        """
        Execute buy order (add to position or create new)

        Quantity priority:
        1. signal.quantity (explicit shares)
        2. signal.target_allocation (% of portfolio)
        3. Default: 1.0 share (defensive fallback)
        """

        # Calculate shares to buy
        shares_to_buy = self._calculate_buy_quantity(symbol, bar, signal)

        min_size = self._get_min_trade_size(symbol)

        if shares_to_buy < min_size:
            logging.debug(
                f"BUY {symbol} skipped: quantity {shares_to_buy:.2f} below min_trade_size {min_size}"
            )
            return

        cost = shares_to_buy * bar.close

        # Check available cash
        if cost > self.cash:
            # Buy what we can afford
            affordable_shares = (self.cash / bar.close) // min_size * min_size

            if affordable_shares < min_size:
                logging.debug(
                    f"BUY {symbol} skipped: insufficient cash "
                    f"(need ${cost:,.0f}, have ${self.cash:,.0f})"
                )
                return

            logging.debug(
                f"BUY {symbol}: Insufficient cash. "
                f"Requested: {shares_to_buy:.2f} shares (${cost:,.0f}), "
                f"Buying: {affordable_shares:.2f} shares (${affordable_shares * bar.close:,.0f})"
            )
            shares_to_buy = affordable_shares
            cost = shares_to_buy * bar.close

        # Update position
        if symbol in self.positions:
            # Add to existing position (update average cost)
            position = self.positions[symbol]
            total_cost = (position.shares * position.avg_cost) + cost
            total_shares = position.shares + shares_to_buy
            position.shares = total_shares
            position.avg_cost = total_cost / total_shares

            logging.debug(
                f"BUY {symbol}: +{shares_to_buy:.2f} shares @ ${bar.close:.2f} "
                f"= ${cost:,.2f} | Total position: {position.shares:.2f} shares "
                f"(avg cost: ${position.avg_cost:.2f})"
            )
        else:
            # Create new position
            self.positions[symbol] = Position(
                symbol=symbol,
                shares=shares_to_buy,
                avg_cost=bar.close
            )

            logging.debug(
                f"BUY {symbol}: {shares_to_buy:.2f} shares @ ${bar.close:.2f} "
                f"= ${cost:,.2f} | NEW POSITION"
            )

        self.cash -= cost

        self._log_trade(
            datetime=bar.datetime,
            symbol=symbol,
            action="BUY",
            shares=shares_to_buy,
            price=bar.close,
            value=cost,
            signal_score=signal.get("score"),
            pnl=0.0
        )

    def _execute_sell(
            self,
            symbol: str,
            bar: 'Bar',
            signal: dict
    ):
        """
        Execute sell order

        Quantity priority:
        1. signal.quantity (explicit shares to sell)
        2. signal.sell_pct (% of current position to sell)
        3. Default: sell entire position
        """
        if symbol not in self.positions:
            logging.warning(f"SELL {symbol} ignored: no position held")
            return

        position = self.positions[symbol]

        # Calculate shares to sell
        shares_to_sell = self._calculate_sell_quantity(symbol, signal, position)

        min_size = self._get_min_trade_size(symbol)

        # Round to min_trade_size
        shares_to_sell = (shares_to_sell // min_size) * min_size

        if shares_to_sell < min_size:
            logging.debug(
                f"SELL {symbol} skipped: quantity {shares_to_sell:.2f} below min_trade_size {min_size}"
            )
            return

        # Cap at current position size
        shares_to_sell = min(shares_to_sell, position.shares)

        proceeds = shares_to_sell * bar.close
        pnl = (bar.close - position.avg_cost) * shares_to_sell

        self.cash += proceeds

        self._log_trade(
            datetime=bar.datetime,
            symbol=symbol,
            action="SELL",
            shares=shares_to_sell,
            price=bar.close,
            value=proceeds,
            signal_score=signal.get("score"),
            pnl=pnl
        )

        # Update or close position
        remaining_shares = position.shares - shares_to_sell

        if remaining_shares < min_size:
            # Close position entirely (remaining too small)
            logging.debug(
                f"SELL {symbol}: {shares_to_sell:.2f} shares @ ${bar.close:.2f} "
                f"= ${proceeds:,.2f} (PnL: ${pnl:+,.2f}) | Position CLOSED"
            )
            del self.positions[symbol]
        else:
            # Reduce position
            position.shares = remaining_shares
            logging.debug(
                f"SELL {symbol}: {shares_to_sell:.2f} shares @ ${bar.close:.2f} "
                f"= ${proceeds:,.2f} (PnL: ${pnl:+,.2f}) | "
                f"Remaining: {remaining_shares:.2f} shares"
            )

    # ========================================================================
    # Quantity Calculation
    # ========================================================================

    def _calculate_buy_quantity(
            self,
            symbol: str,
            bar: 'Bar',
            signal: dict
    ) -> float:
        """
        Calculate shares to buy with priority chain

        Priority:
        1. signal.quantity (explicit shares)
        2. signal.target_allocation (% of total portfolio)
        3. Default: 1.0 share (defensive fallback)

        All quantities subject to constraints (cash, max_position_pct, min_trade_size)
        """

        # Priority 1: Explicit quantity
        if "quantity" in signal:
            raw_shares = float(signal["quantity"])

        # Priority 2: Target allocation (% of total portfolio value)
        elif "target_allocation" in signal:
            target_value = self.initial_cash * signal["target_allocation"]
            raw_shares = target_value / bar.close

        # Priority 3: Default fallback (1 share for testing/defensive)
        else:
            logging.warning(
                f"BUY {symbol}: No quantity or target_allocation specified. "
                f"Using default: 1.0 share (defensive fallback). "
                f"Strategy should specify quantity!"
            )
            raw_shares = 1.0

        # Apply constraints
        return self._apply_buy_constraints(symbol, raw_shares, bar)

    def _calculate_sell_quantity(
            self,
            symbol: str,
            signal: dict,
            position: Position
    ) -> float:
        """
        Calculate shares to sell with priority chain

        Priority:
        1. signal.quantity (explicit shares)
        2. signal.sell_pct (% of current position)
        3. Default: sell entire position
        """

        # Priority 1: Explicit quantity
        if "quantity" in signal:
            return float(signal["quantity"])

        # Priority 2: Percentage of current position
        elif "sell_pct" in signal:
            sell_pct = float(signal["sell_pct"])
            if not (0.0 < sell_pct <= 1.0):
                logging.warning(
                    f"SELL {symbol}: Invalid sell_pct {sell_pct} (must be 0-1). "
                    f"Defaulting to full position close."
                )
                return position.shares
            return position.shares * sell_pct

        # Priority 3: Default - sell entire position
        else:
            return position.shares

    def _apply_buy_constraints(
            self,
            symbol: str,
            raw_shares: float,
            bar: 'Bar'
    ) -> float:
        """
        Apply portfolio constraints to buy quantity

        Constraints:
        1. Minimum trade size (round down)
        2. Available cash
        3. Maximum position size (max_position_pct of portfolio)
        """

        min_size = self._get_min_trade_size(symbol)

        # Constraint 1: Round to min_trade_size
        shares = (raw_shares // min_size) * min_size

        if shares < min_size:
            return 0.0

        # Constraint 2: Available cash
        cost = shares * bar.close
        if cost > self.cash:
            shares = (self.cash / bar.close) // min_size * min_size

        # Constraint 3: Max position size
        # Calculate total position value after this buy
        current_value = 0.0
        if symbol in self.positions:
            current_value = self.positions[symbol].shares * bar.close

        new_total_value = current_value + (shares * bar.close)
        max_position_value = self.initial_cash * self.max_position_pct

        if new_total_value > max_position_value:
            # Cap at maximum allowed
            allowed_additional_value = max_position_value - current_value

            if allowed_additional_value <= 0:
                logging.warning(
                    f"BUY {symbol}: Position already at max ({self.max_position_pct:.0%}). "
                    f"Signal ignored."
                )
                return 0.0

            capped_shares = (allowed_additional_value / bar.close) // min_size * min_size

            if capped_shares < shares:
                logging.warning(
                    f"BUY {symbol}: Capped by max_position_pct ({self.max_position_pct:.0%}). "
                    f"Requested: {shares:.2f} shares, Allowed: {capped_shares:.2f} shares"
                )
                shares = capped_shares

        return shares

    # ========================================================================
    # Utilities
    # ========================================================================

    def _get_min_trade_size(self, symbol: str) -> float:
        """Get minimum trade size for symbol (allows asset-specific overrides)"""
        return self.min_trade_size_per_symbol.get(symbol, self.min_trade_size)

    def _log_trade(
            self,
            datetime: int,
            symbol: str,
            action: str,
            shares: float,
            price: float,
            value: float,
            signal_score: Optional[float],
            pnl: float
    ):
        """Record trade in history"""
        self._trades.append({
            "datetime": datetime,
            "symbol": symbol,
            "action": action,
            "shares": shares,
            "price": price,
            "value": value,
            "signal_score": signal_score,
            "pnl": pnl
        })

    def update(self, bars: Dict[str, 'Bar'], current_timestamp: int):
        """Update equity curve with current market prices"""

        positions_value = 0.0
        for symbol, position in self.positions.items():
            if symbol in bars:
                positions_value += position.market_value(bars[symbol].close)
            else:
                # Conservative: use avg_cost if bar missing
                positions_value += position.shares * position.avg_cost

        total_equity = self.cash + positions_value

        self._equity_history.append((
            current_timestamp,
            total_equity,
            self.cash,
            positions_value,
            len(self.positions)
        ))

    # ========================================================================
    # Properties
    # ========================================================================

    @property
    def total_equity(self) -> float:
        """Current total portfolio value"""
        if not self._equity_history:
            return self.initial_cash
        return self._equity_history[-1][1]

    @property
    def num_trades(self) -> int:
        """Total number of completed trades (closed positions)"""
        return len([t for t in self._trades if t["action"] == "SELL"])

    def get_position_summary(self) -> Dict[str, Dict[str, float]]:
        """
        Get summary of current positions

        Returns:
            {symbol: {"shares": float, "avg_cost": float}}
        """
        return {
            symbol: {
                "shares": pos.shares,
                "avg_cost": pos.avg_cost
            }
            for symbol, pos in self.positions.items()
        }