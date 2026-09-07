"""
Performance analytics for backtesting results
Calculates metrics, generates reports, and exports results
"""

from typing import Dict, List, Tuple, Optional, Any
import math
import numpy as np
import logging
from datetime import datetime, timezone

from core.portfolio import Portfolio
from core.episodes import build_episodes, episode_stats


def _round_or_none(x, n):
    """round() that passes None through: an undefined metric stays undefined."""
    return None if x is None else round(x, n)


def _fmt(x, nd=2):
    """Display an optionally-undefined metric without crashing the formatter."""
    return "n/a" if x is None else f"{x:,.{nd}f}"


class Analyzer:
    # Below one calendar day, annualising extrapolates by more than 365x.
    MIN_YEARS_FOR_CAGR = 1.0 / 365.25

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
            bar_count: int = 0,
            calendar=None,
            regular_hours_only: bool = True,
            start_datetime: Optional[int] = None,
            end_datetime: Optional[int] = None
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
        self.calendar = calendar
        self.regular_hours_only = regular_hours_only
        self.start_datetime = start_datetime
        self.end_datetime = end_datetime

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

        # CAGR (Compound Annual Growth Rate), or None when the window is too
        # short to annualise.
        #
        # (final/initial) ** (1/years) overflowed to inf for very short runs:
        # a two-bar equity history gives years ~ 1.9e-6, so the exponent is
        # ~525,600 and the result is inf with only a RuntimeWarning. sharpe is
        # derived from cagr, so it became inf too -- and inf sorts to the top of
        # a ranking, putting a degenerate one-minute run above every real result.
        #
        # Two guards, because they fail for different reasons. The floor is a
        # judgement: annualising extrapolates by 1/years, and below one calendar
        # day that is an extrapolation of more than 365x, which is not a number
        # anyone should act on. The log-space check is arithmetic: math.exp
        # overflows above ~709.78 regardless of the floor.
        #
        # None, not 0.0. The old else-branch returned 0.0, which reads as "this
        # strategy did not grow" when the truth is "this window cannot answer
        # that" -- and 0.0 sorts to the middle of a ranking as though it were a
        # real measurement.
        cagr_pct = None
        if years >= self.MIN_YEARS_FOR_CAGR and final > 0 and initial > 0:
            log_growth = math.log(final / initial) / years
            if log_growth < 700.0:
                cagr_pct = (math.exp(log_growth) - 1.0) * 100.0

        # ================================================================
        # Risk Metrics
        # ================================================================

        # Calculate returns (assuming 1-minute bars for now)
        returns = np.diff(equity_values) / equity_values[:-1]

        # Volatility (annualized)
        # For 1-minute bars: 252 trading days * 390 minutes per day
        # Annualise from THIS run's own observations, not from a constant and
        # not from the calendar's universe-wide average.
        #
        # 252 * 390 assumed every bar is a regular-hours bar. The calendar's
        # average fixes that but is still universe-wide, and per-symbol bar
        # density varies enormously once extended hours are included -- measured
        # across TECH_100, BKNG trades 154 bars/session against NVDA's 918 while
        # the average is 948. Annualising BKNG with 948 overstates its
        # volatility by sqrt(948/154) ~ 2.5x and understates its Sharpe by the
        # same factor, and because the error differs per symbol it reorders the
        # very ranking a per-symbol batch exists to produce.
        #
        # len(returns)/years is exactly the observed frequency for this run, so
        # it is right for a thin symbol and a liquid one alike, with or without
        # the regular-hours filter. The calendar is kept only as a fallback for
        # a run too short to estimate from.
        if len(returns) > 1 and years > 0:
            periods_per_year = len(returns) / years
        elif self.calendar is not None:
            periods_per_year = self.calendar.periods_per_year(self.regular_hours_only)
        else:
            periods_per_year = 252 * 390
        volatility_annual = np.std(returns) * np.sqrt(periods_per_year) if len(returns) > 0 else 0.0

        # Sharpe Ratio (assuming 0% risk-free rate)
        # Undefined whenever CAGR is: sharpe is annualised return over
        # annualised volatility, so it inherits the numerator's status.
        if volatility_annual > 0 and cagr_pct is not None:
            sharpe = (cagr_pct / 100) / volatility_annual
        else:
            sharpe = 0.0 if cagr_pct is not None else None

        # Maximum Drawdown
        peak = np.maximum.accumulate(equity_values)
        drawdown = (equity_values - peak) / peak
        max_drawdown_pct = abs(np.min(drawdown)) * 100 if len(drawdown) > 0 else 0.0

        # Calmar Ratio (CAGR / Max Drawdown)
        if max_drawdown_pct > 0 and cagr_pct is not None:
            calmar = cagr_pct / max_drawdown_pct
        else:
            calmar = 0.0 if cagr_pct is not None else None

        # ================================================================
        # Trade Statistics — episode-based (see core/episodes.py)
        # ================================================================
        # The unit of account is the position episode (0 -> nonzero -> 0), not
        # the fill. Fill-level win rates can be moved from 50% to 94% purely by
        # slicing one exit into many, with identical economics; episodes are
        # invariant to that by construction. Fill counts survive as the
        # avg_entry_fills / avg_exit_fills diagnostics.
        episodes = build_episodes(trades)
        trade_stats = episode_stats(episodes, last_timestamp=int(timestamps[-1]))

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
            "cagr_pct": _round_or_none(cagr_pct, 3),
            "total_equity": round(final, 2),
            "initial_equity": round(initial, 2),

            # Risk Metrics
            "sharpe": _round_or_none(sharpe, 3),
            "volatility_annualized_pct": round(volatility_annual * 100, 3),
            "max_drawdown_pct": round(max_drawdown_pct, 3),
            "calmar": _round_or_none(calmar, 3),

            # Trade Statistics — DIAGNOSTICS, not ground truth.
            # Everything above this line derives from the mark-to-market equity
            # curve and is immune to how fills are sliced. Everything below is
            # trade accounting; prefer the returns metrics when ranking. Keys
            # are spread in from episode_stats() — see core/episodes.py for
            # what each of the three win rates means.
            **trade_stats,

            # Portfolio Statistics
            "avg_positions": round(avg_positions, 2),
            "max_positions_held": int(max_positions_held),
            "final_cash": round(final_cash, 2),
            "cash_utilization_pct": round(cash_utilization_pct, 2),

            # Data coverage — see _session_coverage()
            **self._session_coverage(timestamps),

            # Meta
            "years": round(years, 3),
            "bar_count": self.bar_count,
            "duration_days": round(duration_days, 1)
        }

    def _session_coverage(self, timestamps) -> dict:
        """
        How many trading sessions this run actually saw, against how many the
        calendar says were scheduled.

        A vendor can silently omit a symbol-day: GOOG 2025-09-24 arrived as a
        zero-byte CSV, the only one in 2.5M files. Nothing downstream breaks --
        the feed returns one session fewer and the backtest completes normally
        -- so without this the affected row in a ranking table is
        indistinguishable from every other row. GOOG placed third on return
        with 478 of 479 sessions and nothing said so.

        Cheap: the sessions table is a few hundred rows and the timestamps are
        already in hand, so this is a bisect per equity point and no SQL at all.
        Deliberately NOT a query against bars_rth -- scanning that view
        full-table is a range join over every bar and takes tens of minutes.
        """
        if self.calendar is None or len(timestamps) == 0:
            return {}

        lo = self.start_datetime if self.start_datetime is not None else int(timestamps[0])
        hi = self.end_datetime if self.end_datetime is not None else int(timestamps[-1])
        expected = len(self.calendar.sessions_in_range(lo, hi, self.regular_hours_only))
        if not expected:
            return {}

        seen = set()
        for t in timestamps:
            s = self.calendar.session_for(int(t))
            if s is not None:
                seen.add(s.et_date)

        return {
            "sessions_expected": expected,
            "sessions_present": len(seen),
            "session_coverage_pct": round(100.0 * len(seen) / expected, 3),
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
            **episode_stats([]),
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
        print(f"CAGR:              {_fmt(m['cagr_pct']):>13}%")

        print("\n--- RISK METRICS ---")
        print(f"Sharpe Ratio:      {_fmt(m['sharpe']):>13}")
        print(f"Max Drawdown:      {m['max_drawdown_pct']:>12,.2f}%")
        print(f"Volatility (Ann):  {m['volatility_annualized_pct']:>12,.2f}%")
        print(f"Calmar Ratio:      {_fmt(m['calmar']):>13}")

        print("\n--- TRADE DIAGNOSTICS (episode-based) ---")
        print(f"Episodes (closed): {m['num_episodes']:>12,}")
        print(f"  still open:      {m['num_open_episodes']:>12,}")
        print(f"Winning / Losing:  {m['num_wins']:>12,} / {m['num_losses']:,}")
        print(f"Win Rate (count):  {m['win_rate_count_pct']:>12,.2f}%   how often I was right")
        print(f"Win Rate (notion): {m['win_rate_notional_pct']:>12,.2f}%   share of committed capital that won")
        print(f"Win Rate (time):   {m['win_rate_time_pct']:>12,.2f}%   share of exposure time that won")
        print(f"  size skew:       {m['win_rate_size_skew_pct']:>+12,.2f}pp  count-notional; large = small wins, big losses")
        print(f"Fills per episode: {m['avg_entry_fills']:>12,.2f} in / {m['avg_exit_fills']:,.2f} out")
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
                dt_str = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
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