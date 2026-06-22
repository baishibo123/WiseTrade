# strategies/examples/simple_sma.py

"""
Simple SMA Crossover Strategy for testing
Buys when price crosses above SMA, sells when crosses below
"""

from typing import Dict
from database.schema import Bar
from strategies.base import Strategy
from strategies.indicators import calculate_sma


class SimpleSMA(Strategy):
    """
    Simple Moving Average Crossover

    Rules:
    - BUY: When close > SMA(20) and no position
    - SELL: When close < SMA(20) and have position
    - Position size: Fixed 10 shares
    """

    def _update_indicators(self, symbol: str):
        """Calculate SMA for each symbol"""
        closes = self.get_closes(symbol)

        # Calculate SMA(20)
        sma = calculate_sma(closes, 20)
        self._indicators[symbol]["sma_20"] = sma

    def next(self, bars: Dict[str, Bar]) -> Dict[str, dict]:
        """Generate signals based on SMA crossover"""
        signals = {}

        for symbol, bar in bars.items():
            sma = self._indicators[symbol].get("sma_20")

            # Skip if not enough data
            if sma is None:
                continue

            # BUY signal: price above SMA and no position
            if bar.close > sma and not self.has_position(symbol):
                signals[symbol] = {
                    "action": "BUY",
                    "score": 0.8,
                    "quantity": 10.0  # Buy 10 shares
                }

            # SELL signal: price below SMA and have position
            elif bar.close < sma and self.has_position(symbol):
                signals[symbol] = {
                    "action": "SELL",
                    "score": 0.8
                }

        return signals