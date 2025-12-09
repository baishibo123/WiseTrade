"""
WiseTrade - Strategy Ranking Script
Runs multiple strategies across multiple symbols and exports results to CSV
"""

import logging
import csv
from datetime import datetime
from typing import List, Type, Dict, Any
from pathlib import Path

from core.engine import Engine
from strategies.base import Strategy
from strategies.sma_crossover import SMACrossover
from strategies.sma_atr_exit import SMA_ATR_Exit
from strategies.SMA_OS_dynamic import SMA_OS_Dynamic
from strategies.SMA_OS_Fixed import SMA_OS_Fixed
from database.sqlite_db import TECH_100
from tqdm import tqdm

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ============================================================================
# CONFIGURATION
# ============================================================================

# Date range (Unix milliseconds UTC)
START_DATETIME = 1751414400000
END_DATETIME = 1764057600000

# Engine settings (using defaults)
INITIAL_CASH = 100_000.0
MIN_TRADE_SIZE = 0.1

# Strategies to test (add more as needed)
STRATEGIES: List[Type[Strategy]] = [
    SMACrossover,
    SMA_ATR_Exit,
    SMA_OS_Dynamic,
    SMA_OS_Fixed,
]

# Symbols to test
SYMBOLS = list(TECH_100)

# Output directory
OUTPUT_DIR = Path("results")
OUTPUT_DIR.mkdir(exist_ok=True)


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def unix_millis_to_readable(timestamp_ms: int) -> str:
    """Convert Unix milliseconds to YYYYMMDD format"""
    dt = datetime.utcfromtimestamp(timestamp_ms / 1000)
    return dt.strftime("%Y%m%d")


def generate_output_filename(start_ms: int, end_ms: int) -> str:
    """Generate output filename with date range"""
    start_str = unix_millis_to_readable(start_ms)
    end_str = unix_millis_to_readable(end_ms)
    return f"ranking_{start_str}_{end_str}.csv"


# ============================================================================
# MAIN RANKING FUNCTION
# ============================================================================

def run_ranking():
    """
    Main function to run all strategy-symbol combinations and export results
    """
    # Calculate total combinations
    total_combinations = len(STRATEGIES) * len(SYMBOLS)
    logging.info(
        f"Starting ranking run: {len(STRATEGIES)} strategies × {len(SYMBOLS)} symbols = {total_combinations} combinations")
    logging.info(f"Date range: {unix_millis_to_readable(START_DATETIME)} to {unix_millis_to_readable(END_DATETIME)}")

    # Prepare CSV output
    output_file = OUTPUT_DIR / generate_output_filename(START_DATETIME, END_DATETIME)
    csv_headers = [
        "Strategy_Name",
        "Symbol",
        "Total_Return",
        "Sharpe",
        "Max_Drawdown",
        "Volatility",
        "Win_Rate",
        "Total_Trades"
    ]

    # Open CSV file for writing
    with open(output_file, 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=csv_headers)
        writer.writeheader()

        # Progress bar for overall completion
        with tqdm(total=total_combinations, desc="Overall Progress", position=0) as pbar:

            # Iterate through all strategies
            for strategy_class in STRATEGIES:
                strategy_name = strategy_class.__name__
                logging.info(f"\n{'=' * 60}")
                logging.info(f"Testing Strategy: {strategy_name}")
                logging.info(f"{'=' * 60}")

                # Iterate through all symbols
                for symbol in SYMBOLS:
                    try:
                        # Import feed here to avoid circular imports
                        from datafeed.db_feed import SQLiteFeed

                        # Create feed for this symbol
                        feed = SQLiteFeed(
                            symbol=symbol,
                            start_datetime=START_DATETIME,
                            end_datetime=END_DATETIME
                        )

                        # Create engine
                        engine = Engine(
                            feed=feed,
                            strategy_class=strategy_class,
                            strategy_params=None,  # Use default parameters
                            initial_cash=INITIAL_CASH,
                            min_trade_size=MIN_TRADE_SIZE
                        )

                        # Run backtest
                        analyzer = engine.run()

                        # Extract metrics
                        metrics = analyzer.metrics

                        # Prepare row for CSV
                        row = {
                            "Strategy_Name": strategy_name,
                            "Symbol": symbol,
                            "Total_Return": metrics["total_return_pct"],
                            "Sharpe": metrics["sharpe"],
                            "Max_Drawdown": metrics["max_drawdown_pct"],
                            "Volatility": metrics["volatility_annualized"],
                            "Win_Rate": metrics["win_rate_pct"],
                            "Total_Trades": metrics["num_trades"]
                        }

                        # Write to CSV
                        writer.writerow(row)
                        csvfile.flush()  # Ensure data is written immediately

                        # Log success
                        logging.info(
                            f"✓ {strategy_name} | {symbol} | "
                            f"Return: {metrics['total_return_pct']:+.2f}% | "
                            f"Sharpe: {metrics['sharpe']:.2f} | "
                            f"Trades: {metrics['num_trades']}"
                        )

                    except Exception as e:
                        # Log error and skip to next combination
                        logging.error(f"✗ {strategy_name} | {symbol} | Error: {str(e)}")
                        continue

                    finally:
                        # Update progress bar
                        pbar.update(1)

    # Final summary
    logging.info(f"\n{'=' * 60}")
    logging.info(f"Ranking Complete!")
    logging.info(f"Results saved to: {output_file}")
    logging.info(f"{'=' * 60}")


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    run_ranking()