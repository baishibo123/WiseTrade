# core/analyzer.py
from typing import Dict, Any, Optional
import pandas as pd
import numpy as np

from core.portfolio import Portfolio
from database.schema import Bar


class Analyzer:
    """
    Post-backtest performance analyzer
    Calculates all industry-standard metrics:
    - Total return, CAGR, Sharpe, Max Drawdown, Win Rate, etc.
    - Provides clean equity curve DataFrame
    - Ready for comparison tables and ranking
    """

    def __init__(self, portfolio: Portfolio):
        self.portfolio = portfolio
        self.symbol: Optional[str] = None
        self.strategy_name: Optional[str] = None
        self.bar_count: int = 0

        # Self-triggered calculation
        self._metrics: Dict[str, Any] = {}
        self._calculate_metrics()

    def _calculate_metrics(self) -> None:
        """Main calculation engine — triggered on initialization"""
        equity_df = self.portfolio.equity_curve
        if equity_df.empty:
            self._metrics = {
                "total_return_pct": 0.0,
                "total_equity": self.portfolio.initial_cash,
                "cagr_pct": 0.0,
                "sharpe": 0.0,
                "volatility_annualized": 0.0,
                "max_drawdown_pct": 0.0,
                "num_trades": 0,
                "win_rate_pct": 0.0,
                "profit_factor": 0.0,
            }
            return

        # 1. Basic returns
        initial = self.portfolio.initial_cash
        final = equity_df["equity"].iloc[-1]
        total_return = (final / initial) - 1
        total_return_pct = total_return * 100

        # 2. Time period (in years)
        start_ms = equity_df.index[0].timestamp() * 1000
        end_ms = equity_df.index[-1].timestamp() * 1000
        years = (end_ms - start_ms) / (1000 * 60 * 60 * 24 * 365.25)
        years = max(years, 1e-6)  # avoid divide by zero

        cagr = (final / initial) ** (1 / years) - 1
        cagr_pct = cagr * 100

        # 3. Returns series (1-minute)
        returns = equity_df["equity"].pct_change().dropna()

        # 4. Risk metrics (annualized from 1-minute returns)
        if len(returns) > 1:
            minutes_per_year = 252 * 390  # 390 minutes per trading day
            mean_return = returns.mean()
            std_return = returns.std()

            volatility_annual = std_return * np.sqrt(minutes_per_year)
            sharpe = (mean_return / std_return) * np.sqrt(minutes_per_year) if std_return > 0 else 0.0
        else:
            volatility_annual = sharpe = 0.0

        # 5. Max Drawdown
        peak = equity_df["equity"].cummax()
        drawdown = (equity_df["equity"] - peak) / peak
        max_dd = drawdown.min() * 100  # in percent

        # 6. Trade statistics
        trades = self.portfolio.trades
        num_trades = len(trades)

        if num_trades > 0:
            # Calculate realized P&L per trade
            realized_pnl = []
            position = 0.0
            entry_price = 0.0

            for t in trades:
                if t["type"] == "BUY":
                    position += t["size"]
                    entry_price = t["price"]  # simplified
                elif t["type"] == "SELL":
                    if position > 0:
                        pnl = (t["price"] - entry_price) * t["size"]
                        realized_pnl.append(pnl)
                        position -= t["size"]

            wins = [p for p in realized_pnl if p > 0]
            losses = [abs(p) for p in realized_pnl if p <= 0]

            win_rate = len(wins) / len(realized_pnl) * 100 if realized_pnl else 0
            gross_profit = sum(wins) if wins else 0
            gross_loss = sum(losses) if losses else 0
            profit_factor = gross_profit / gross_loss if gross_loss > 0 else np.inf
        else:
            win_rate = profit_factor = 0.0

        # Store everything as dictionary
        self._metrics = {
            "symbol": self.symbol,
            "strategy": self.strategy_name,
            "total_return_pct": round(total_return_pct, 3),
            "total_equity": round(final, 2),
            "cagr_pct": round(cagr_pct, 3),
            "sharpe": round(sharpe, 3),
            "volatility_annualized": round(volatility_annual * 100, 3),
            "max_drawdown_pct": round(max_dd, 3),
            "num_trades": num_trades,
            "win_rate_pct": round(win_rate, 2),
            "profit_factor": round(profit_factor, 3) if np.isfinite(profit_factor) else 0.0,
            "bar_count": self.bar_count,
            "years": round(years, 3),
        }

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------
    @property
    def metrics(self) -> Dict[str, Any]:
        return self._metrics.copy()

    @property
    def equity_curve(self) -> pd.DataFrame:
        return self.portfolio.equity_curve.copy()

    # ------------------------------------------------------------------
    # Pretty output
    # ------------------------------------------------------------------
    def summary_table(self) -> pd.DataFrame:
        """One-row DataFrame — perfect for pd.concat() in run_multiple()"""
        data = self._metrics.copy()
        return pd.DataFrame([data])

    def print_summary(self) -> None:
        m = self._metrics
        print(f"\n{'=' * 60}")
        print(f" BACKTEST RESULT: {m.get('symbol', 'N/A')} | {m.get('strategy', 'Strategy')}")
        print(f"{'=' * 60}")
        print(f"   Final Equity     : ${m['total_equity']:,.2f}")
        print(f"   Total Return     : {m['total_return_pct']:+.2f}%")
        print(f"   CAGR             : {m['cagr_pct']:+.2f}%")
        print(f"   Sharpe Ratio     : {m['sharpe']:.3f}")
        print(f"   Max Drawdown     : {m['max_drawdown_pct']:.2f}%")
        print(f"   Volatility (ann) : {m['volatility_annualized']:.2f}%")
        print(f"   Win Rate         : {m['win_rate_pct']:.1f}%")
        print(f"   Profit Factor    : {m['profit_factor']:.2f}")
        print(f"   Total Trades     : {m['num_trades']:,}")
        print(f"   Period           : {m['years']:.2f} years ({m['bar_count']:,} bars)")
        print(f"{'=' * 60}\n")

    def __repr__(self) -> str:
        r = self._metrics.get("total_return_pct", 0.0)
        return f"<Analyzer {self.symbol} | {self.strategy_name} | {r:+.2f}%>"