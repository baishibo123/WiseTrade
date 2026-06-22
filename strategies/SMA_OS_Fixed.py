# strategies/examples/sma_os_fixed.py

"""
SMA Optimal Stopping Strategy - Fixed Window

Entry: SMA(10) > SMA(20) [Fresh Cross]
Exit: Optimal Stopping with Fixed Window (N=390 bars)
"""

from typing import Dict, Optional
from database.schema import Bar
from strategies.base import Strategy
from strategies.indicators import calculate_sma


class SMA_OS_Fixed(Strategy):
    """
    Entry: SMA(10) > SMA(20) [Fresh Cross]
    Exit: Optimal Stopping with Fixed Window (N=390 bars = 1 trading day)

    Phases:
    - Observation (37% of window): Track max price
    - Selection (63% of window): Sell when price beats observation max
    - Time limit: Force exit after N bars
    """

    def __init__(self, universe, params=None):
        super().__init__(universe, params)

        # Entry parameters
        self.fast_period = self.params.get('fast_period', 10)
        self.slow_period = self.params.get('slow_period', 20)

        # Exit parameters (Optimal Stopping)
        self.window_n = self.params.get('window_n', 390)  # 1 trading day (6.5 hours)
        self.observation_idx = int(self.window_n * 0.37)  # ~144 bars

        # Per-symbol state tracking
        self._state = {
            symbol: {
                'bars_held': 0,
                'max_price_obs': 0.0,
                'prev_bullish': False,
                'entry_bar_idx': None
            }
            for symbol in universe
        }

    def _update_indicators(self, symbol: str):
        """Calculate SMAs for this symbol"""
        closes = self.get_closes(symbol)

        self._indicators[symbol]['sma_fast'] = calculate_sma(closes, self.fast_period)
        self._indicators[symbol]['sma_slow'] = calculate_sma(closes, self.slow_period)

    def next(self, bars: Dict[str, Bar]) -> Dict[str, dict]:
        """
        Generate signals for all symbols based on SMA crossover + Optimal Stopping
        """
        signals = {}

        for symbol, bar in bars.items():
            # Get indicators
            sma_fast = self._indicators[symbol].get('sma_fast')
            sma_slow = self._indicators[symbol].get('sma_slow')

            # Skip if not enough data
            if sma_fast is None or sma_slow is None:
                continue

            # Get symbol state
            state = self._state[symbol]

            # Check current position
            has_position = self.has_position(symbol)

            # ================================================================
            # EXIT LOGIC (if holding position)
            # ================================================================

            if has_position:
                state['bars_held'] += 1

                should_exit = False

                # Phase A: Observation (first 37% of window)
                if state['bars_held'] <= self.observation_idx:
                    if bar.close > state['max_price_obs']:
                        state['max_price_obs'] = bar.close

                # Phase B: Selection (remaining 63%)
                elif state['bars_held'] <= self.window_n:
                    # Sell if price beats observation benchmark
                    if bar.close > state['max_price_obs']:
                        should_exit = True

                # Phase C: Time limit reached (force exit)
                if state['bars_held'] >= self.window_n:
                    should_exit = True

                if should_exit:
                    signals[symbol] = {
                        "action": "SELL",
                        "score": 1.0  # High confidence exit
                    }
                    # Reset state will happen after exit
                    self._reset_state(symbol)

            # ================================================================
            # ENTRY LOGIC (if no position)
            # ================================================================

            else:
                is_bullish = sma_fast > sma_slow

                # Check for FRESH crossover
                if is_bullish and not state['prev_bullish']:
                    signals[symbol] = {
                        "action": "BUY",
                        "score": 0.8,
                        "quantity": 100.0  # Fixed 100 shares
                    }

                    # Initialize exit state
                    state['bars_held'] = 0
                    state['max_price_obs'] = bar.close

                # Update state for next iteration
                state['prev_bullish'] = is_bullish

        return signals

    def _reset_state(self, symbol: str):
        """Reset tracking state for a symbol"""
        self._state[symbol]['bars_held'] = 0
        self._state[symbol]['max_price_obs'] = 0.0
        self._state[symbol]['prev_bullish'] = False