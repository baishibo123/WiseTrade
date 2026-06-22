"""
Performance analytics for backtesting results
Calculates metrics, generates reports, and exports results
"""

from typing import Dict, List, Tuple, Optional, Any
import numpy as np
import logging
from datetime import datetime

from core.portfolio import Portfolio


class Analyzer:
    """
    Performance analyzer for backtest results

    Calculates:
    - Return metrics (total return, CAGR)
    - Risk metrics (Sharpe, volatility, max drawdown)
    - Trade statistics (win rate, profit factor)

    Usage:
        analyzer = Analyzer(
            portfolio=portfolio,
            universe=["AAPL", "MSFT"],
            strategy_name="SMA_Crossover",
            bar_count=1_000_000
        )

        print(analyzer.metrics)
        analyzer.print_summary()
        analyzer.export_trades("results/trades.csv")
    """

    def __init__(
            self,
            portfolio: Portfolio,
            universe: List[str],
            strategy_name: str = "Unknown",
            bar_count: int = 0
    ):
        """
        Initialize analyzer

        Args:
            portfolio: Portfolio instance with completed backtest
            universe: List of symbols traded
            strategy_name: Name of strategy
            bar_count: Total number of bars processed
        """
        self.portfolio = portfolio
        self.universe = universe
        self.strategy_name = strategy_name
        self.bar_count = bar_count

        # Calculate all metrics
        self._metrics = self._calculate_metrics()

        logging.info(f"Analyzer created for {strategy_name}: {len(universe)} symbols")

    # ========================================================================
    # Metrics Calculation
    # ========================================================================

    def _calculate_metrics(self) -> Dict[str, Any]:
        """
        Calculate all performance metrics

        Returns:
            Dictionary of metrics
        """
        equity_history = self.portfolio._equity_history
        trades = self.portfolio._trades

        if not equity_history:
            return self._empty_metrics()

        # Extract equity curve
        timestamps = np.array([t for t, _, _, _, _ in equity_history])
        equity_values = np.array([e for _, e, _, _, _ in equity_history])
        cash_values = np.array([c for _, _, c, _, _ in equity_history])
        position_values = np.array([p for _, _, _, p, _ in equity_history])
        num_positions = np.array([n for _, _, _, _, n in equity_history])

        initial = equity_values[0]
        final = equity_values[-1]

        # ================================================================
        # Return Metrics
        # ================================================================

        total_return_pct = ((final - initial) / initial) * 100

        # Time-based metrics
        duration_ms = timestamps[-1] - timestamps[0]
        duration_days = duration_ms / (1000 * 60 * 60 * 24)
        years = duration_days / 365.25

        # CAGR (Compound Annual Growth Rate)
        if years > 0 and final > 0 and initial > 0:
            cagr_pct = (((final / initial) ** (1 / years)) - 1) * 100
        else:
            cagr_pct = 0.0

        # ================================================================
        # Risk Metrics
        # ================================================================

        # Calculate returns (assuming 1-minute bars for now)
        returns = np.diff(equity_values) / equity_values[:-1]

        # Volatility (annualized)
        # For 1-minute bars: 252 trading days * 390 minutes per day
        periods_per_year = 252 * 390
        volatility_annual = np.std(returns) * np.sqrt(periods_per_year) if len(returns) > 0 else 0.0

        # Sharpe Ratio (assuming 0% risk-free rate)
        if volatility_annual > 0:
            sharpe = (cagr_pct / 100) / volatility_annual
        else:
            sharpe = 0.0

        # Maximum Drawdown
        peak = np.maximum.accumulate(equity_values)
        drawdown = (equity_values - peak) / peak
        max_drawdown_pct = abs(np.min(drawdown)) * 100 if len(drawdown) > 0 else 0.0

        # Calmar Ratio (CAGR / Max Drawdown)
        if max_drawdown_pct > 0:
            calmar = cagr_pct / max_drawdown_pct
        else:
            calmar = 0.0

        # ================================================================
        # Trade Statistics
        # ================================================================

        # Separate buy and sell trades
        sell_trades = [t for t in trades if t["action"] == "SELL"]

        num_trades = len(sell_trades)

        if num_trades > 0:
            # Win/Loss analysis
            winning_trades = [t for t in sell_trades if t.get("pnl", 0) > 0]
            losing_trades = [t for t in sell_trades if t.get("pnl", 0) < 0]

            num_wins = len(winning_trades)
            num_losses = len(losing_trades)

            win_rate_pct = (num_wins / num_trades) * 100

            # Profit/Loss statistics
            total_wins = sum(t["pnl"] for t in winning_trades)
            total_losses = abs(sum(t["pnl"] for t in losing_trades))

            profit_factor = total_wins / total_losses if total_losses > 0 else 0.0

            avg_win = total_wins / num_wins if num_wins > 0 else 0.0
            avg_loss = total_losses / num_losses if num_losses > 0 else 0.0

            largest_win = max([t["pnl"] for t in winning_trades]) if winning_trades else 0.0
            largest_loss = min([t["pnl"] for t in losing_trades]) if losing_trades else 0.0

            # Average holding period (in bars)
            # Note: This requires tracking entry times, placeholder for now
            avg_bars_per_trade = self.bar_count / num_trades if num_trades > 0 else 0

        else:
            # No trades completed
            num_wins = 0
            num_losses = 0
            win_rate_pct = 0.0
            profit_factor = 0.0
            avg_win = 0.0
            avg_loss = 0.0
            largest_win = 0.0
            largest_loss = 0.0
            avg_bars_per_trade = 0

        # ================================================================
        # Portfolio Statistics
        # ================================================================

        avg_positions = np.mean(num_positions)
        max_positions_held = np.max(num_positions)

        final_cash = cash_values[-1]
        final_positions_value = position_values[-1]
        cash_utilization_pct = (final_positions_value / final) * 100 if final > 0 else 0.0

        # ================================================================
        # Compile Metrics
        # ================================================================

        return {
            # Identification
            "strategy": self.strategy_name,
            "universe": self.universe,
            "universe_size": len(self.universe),

            # Return Metrics
            "total_return_pct": round(total_return_pct, 3),
            "cagr_pct": round(cagr_pct, 3),
            "total_equity": round(final, 2),
            "initial_equity": round(initial, 2),

            # Risk Metrics
            "sharpe": round(sharpe, 3),
            "volatility_annualized_pct": round(volatility_annual * 100, 3),
            "max_drawdown_pct": round(max_drawdown_pct, 3),
            "calmar": round(calmar, 3),

            # Trade Statistics
            "num_trades": num_trades,
            "num_wins": num_wins,
            "num_losses": num_losses,
            "win_rate_pct": round(win_rate_pct, 2),
            "profit_factor": round(profit_factor, 3),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "largest_win": round(largest_win, 2),
            "largest_loss": round(largest_loss, 2),

            # Portfolio Statistics
            "avg_positions": round(avg_positions, 2),
            "max_positions_held": int(max_positions_held),
            "final_cash": round(final_cash, 2),
            "cash_utilization_pct": round(cash_utilization_pct, 2),

            # Meta
            "years": round(years, 3),
            "bar_count": self.bar_count,
            "duration_days": round(duration_days, 1)
        }

    def _empty_metrics(self) -> Dict[str, Any]:
        """Return empty metrics for failed backtests"""
        return {
            "strategy": self.strategy_name,
            "universe": self.universe,
            "universe_size": len(self.universe),
            "total_return_pct": 0.0,
            "cagr_pct": 0.0,
            "total_equity": self.portfolio.initial_cash,
            "initial_equity": self.portfolio.initial_cash,
            "sharpe": 0.0,
            "volatility_annualized_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "calmar": 0.0,
            "num_trades": 0,
            "num_wins": 0,
            "num_losses": 0,
            "win_rate_pct": 0.0,
            "profit_factor": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "largest_win": 0.0,
            "largest_loss": 0.0,
            "avg_positions": 0.0,
            "max_positions_held": 0,
            "final_cash": self.portfolio.initial_cash,
            "cash_utilization_pct": 0.0,
            "years": 0.0,
            "bar_count": 0,
            "duration_days": 0.0
        }

    # ========================================================================
    # Properties
    # ========================================================================

    @property
    def metrics(self) -> Dict[str, Any]:
        """Get all calculated metrics"""
        return self._metrics

    @property
    def equity_curve(self) -> List[Tuple[int, float]]:
        """
        Get equity curve data

        Returns:
            List of (timestamp, equity) tuples
        """
        return [(t, e) for t, e, _, _, _ in self.portfolio._equity_history]

    @property
    def trades(self) -> List[Dict[str, Any]]:
        """Get all trades"""
        return self.portfolio._trades

    # ========================================================================
    # Display Methods
    # ========================================================================

    def print_summary(self):
        """Print formatted summary of backtest results"""
        m = self.metrics

        print("\n" + "=" * 70)
        print(f"BACKTEST SUMMARY: {m['strategy']}")
        print("=" * 70)

        print(f"\nUniverse: {m['universe_size']} symbols")
        print(f"Duration: {m['duration_days']:.1f} days ({m['years']:.2f} years)")
        print(f"Bars processed: {m['bar_count']:,}")

        print("\n--- RETURNS ---")
        print(f"Initial Equity:    ${m['initial_equity']:>12,.2f}")
        print(f"Final Equity:      ${m['total_equity']:>12,.2f}")
        print(f"Total Return:      {m['total_return_pct']:>12,.2f}%")
        print(f"CAGR:              {m['cagr_pct']:>12,.2f}%")

        print("\n--- RISK METRICS ---")
        print(f"Sharpe Ratio:      {m['sharpe']:>12,.2f}")
        print(f"Max Drawdown:      {m['max_drawdown_pct']:>12,.2f}%")
        print(f"Volatility (Ann):  {m['volatility_annualized_pct']:>12,.2f}%")
        print(f"Calmar Ratio:      {m['calmar']:>12,.2f}")

        print("\n--- TRADE STATISTICS ---")
        print(f"Total Trades:      {m['num_trades']:>12,}")
        print(f"Winning Trades:    {m['num_wins']:>12,}")
        print(f"Losing Trades:     {m['num_losses']:>12,}")
        print(f"Win Rate:          {m['win_rate_pct']:>12,.2f}%")
        print(f"Profit Factor:     {m['profit_factor']:>12,.2f}")
        print(f"Avg Win:           ${m['avg_win']:>12,.2f}")
        print(f"Avg Loss:          ${m['avg_loss']:>12,.2f}")
        print(f"Largest Win:       ${m['largest_win']:>12,.2f}")
        print(f"Largest Loss:      ${m['largest_loss']:>12,.2f}")

        print("\n--- PORTFOLIO ---")
        print(f"Avg Positions:     {m['avg_positions']:>12,.2f}")
        print(f"Max Positions:     {m['max_positions_held']:>12,}")
        print(f"Final Cash:        ${m['final_cash']:>12,.2f}")
        print(f"Cash Utilization:  {m['cash_utilization_pct']:>12,.2f}%")

        print("\n" + "=" * 70 + "\n")

    # ========================================================================
    # Export Methods
    # ========================================================================

    def export_trades(self, filepath: str):
        """
        Export trades to CSV

        Args:
            filepath: Output CSV file path
        """
        import csv

        with open(filepath, 'w', newline='') as f:
            if not self.trades:
                logging.warning("No trades to export")
                return

            writer = csv.DictWriter(f, fieldnames=self.trades[0].keys())
            writer.writeheader()
            writer.writerows(self.trades)

        logging.info(f"Exported {len(self.trades)} trades to {filepath}")

    def export_equity_curve(self, filepath: str):
        """
        Export equity curve to CSV

        Args:
            filepath: Output CSV file path
        """
        import csv

        with open(filepath, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "datetime", "equity", "cash", "positions_value", "num_positions"])

            for timestamp, equity, cash, pos_val, num_pos in self.portfolio._equity_history:
                dt_str = datetime.utcfromtimestamp(timestamp / 1000).strftime("%Y-%m-%d %H:%M:%S")
                writer.writerow([timestamp, dt_str, equity, cash, pos_val, num_pos])

        logging.info(f"Exported equity curve to {filepath}")

    def export_metrics(self, filepath: str):
        """
        Export metrics to JSON

        Args:
            filepath: Output JSON file path
        """
        import json

        with open(filepath, 'w') as f:
            json.dump(self.metrics, f, indent=2)

        logging.info(f"Exported metrics to {filepath}")

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert analyzer to dictionary (for database storage)

        Returns:
            Dictionary with metrics, trades, and equity curve
        """
        return {
            "metrics": self.metrics,
            "trades": self.trades,
            "equity_curve": [
                {
                    "timestamp": t,
                    "equity": e,
                    "cash": c,
                    "positions_value": p,
                    "num_positions": n
                }
                for t, e, c, p, n in self.portfolio._equity_history
            ]
        }


# ============================================================================
# Testing utility
# ============================================================================

def test_analyzer():
    """
    Test Analyzer with mock portfolio data
    """
    from core.portfolio import Portfolio
    from database.schema import Bar

    print("\n=== Analyzer Test ===")

    # Create mock portfolio with some trades
    portfolio = Portfolio(initial_cash=100_000, max_positions=10)

    # Simulate some trades
    bars = {
        "AAPL": Bar("AAPL", 1000, 150.0, 150.0, 151.0, 149.0, 150.0, 1000),
        "MSFT": Bar("MSFT", 1000, 300.0, 300.0, 301.0, 299.0, 300.0, 2000)
    }

    # Buy signals
    signals_buy = {
        "AAPL": {"action": "BUY", "score": 0.8, "quantity": 100.0},
        "MSFT": {"action": "BUY", "score": 0.9, "quantity": 50.0}
    }

    portfolio.process_signals(signals_buy, bars)
    portfolio.update(bars, 1000)

    # Simulate price increase
    bars2 = {
        "AAPL": Bar("AAPL", 2000, 155.0, 155.0, 156.0, 154.0, 155.0, 1100),
        "MSFT": Bar("MSFT", 2000, 310.0, 310.0, 311.0, 309.0, 310.0, 2100)
    }

    portfolio.update(bars2, 2000)

    # Sell signals
    signals_sell = {
        "AAPL": {"action": "SELL", "score": 0.7},
        "MSFT": {"action": "SELL", "score": 0.8}
    }

    portfolio.process_signals(signals_sell, bars2)
    portfolio.update(bars2, 2000)

    # Create analyzer
    analyzer = Analyzer(
        portfolio=portfolio,
        universe=["AAPL", "MSFT"],
        strategy_name="TestStrategy",
        bar_count=2
    )

    # Print summary
    analyzer.print_summary()

    # Test exports
    print("\nTesting exports...")
    analyzer.export_trades("test_trades.csv")
    analyzer.export_equity_curve("test_equity.csv")
    analyzer.export_metrics("test_metrics.json")

    print("\n✓ Analyzer test complete")


if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(levelname)s - %(message)s'
    )

    test_analyzer()