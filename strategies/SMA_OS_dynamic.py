# strategies/examples/sma_os_dynamic.py

"""
SMA Optimal Stopping Strategy - Dynamic Window

Entry: SMA(10) > SMA(20) [Fresh Cross]
Exit: Optimal Stopping with Dynamic Window based on time until market close
"""

from typing import Dict, Optional
from datetime import datetime
from database.schema import Bar
from strategies.base import Strategy
from strategies.indicators import calculate_sma


class SMA_OS_Dynamic(Strategy):
    """
    Entry: SMA(10) > SMA(20) [Fresh Cross]
    Exit: Optimal Stopping with Dynamic Window

    Window dynamically calculated as: minutes remaining until market close
    Only enters trades if >= 30 minutes remain in trading day
    """

    def __init__(self, universe, params=None):
        super().__init__(universe, params)

        # Entry parameters
        self.fast_period = self.params.get('fast_period', 10)
        self.slow_period = self.params.get('slow_period', 20)

        # Market close time (UTC)
        # US Market Close (4:00 PM ET):
        # - 20:00 UTC (Daylight Saving Time / Summer)
        # - 21:00 UTC (Standard Time / Winter)
        self.market_close_hour_utc = self.params.get('market_close_hour_utc', 21)

        # Minimum minutes required to enter trade
        self.min_minutes_to_trade = self.params.get('min_minutes_to_trade', 30)

        # Per-symbol state tracking
        self._state = {
            symbol: {
                'window_n': 0,
                'observation_idx': 0,
                'bars_held': 0,
                'max_price_obs': 0.0,
                'prev_bullish': False
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
        Generate signals based on SMA crossover + Dynamic Optimal Stopping
        """
        signals = {}

        for symbol, bar in bars.items():
            # Get indicators
            sma_fast = self._indicators[symbol].get('sma_fast')
            sma_slow = self._indicators[symbol].get('sma_slow')

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

                # Phase A: Observation
                if state['bars_held'] <= state['observation_idx']:
                    if bar.close > state['max_price_obs']:
                        state['max_price_obs'] = bar.close

                # Phase B: Selection
                elif state['bars_held'] <= state['window_n']:
                    # Sell if price beats benchmark
                    if bar.close > state['max_price_obs']:
                        should_exit = True

                # Phase C: Time limit
                if state['bars_held'] >= state['window_n']:
                    should_exit = True

                if should_exit:
                    signals[symbol] = {
                        "action": "SELL",
                        "score": 1.0
                    }
                    self._reset_state(symbol)

            # ================================================================
            # ENTRY LOGIC (if no position)
            # ================================================================

            else:
                is_bullish = sma_fast > sma_slow

                # Check for FRESH crossover
                if is_bullish and not state['prev_bullish']:
                    # Calculate dynamic window
                    minutes_remaining = self._get_minutes_to_close(bar.datetime)

                    # Only trade if enough time remains
                    if minutes_remaining >= self.min_minutes_to_trade:
                        signals[symbol] = {
                            "action": "BUY",
                            "score": 0.8,
                            "quantity": 100.0
                        }

                        # Set dynamic window parameters
                        state['window_n'] = minutes_remaining
                        state['observation_idx'] = int(state['window_n'] * 0.37)
                        state['bars_held'] = 0
                        state['max_price_obs'] = bar.close

                # Update state
                state['prev_bullish'] = is_bullish

        return signals

    def _get_minutes_to_close(self, timestamp_ms: int) -> int:
        """
        Calculate minutes remaining until market close

        Args:
            timestamp_ms: Current bar timestamp (Unix milliseconds UTC)

        Returns:
            Minutes until market close (0 if already past close)
        """
        # Convert to seconds and create datetime object
        timestamp_sec = timestamp_ms / 1000.0
        dt_current = datetime.utcfromtimestamp(timestamp_sec)

        # Create market close time for current day
        try:
            dt_close = dt_current.replace(
                hour=self.market_close_hour_utc,
                minute=0,
                second=0,
                microsecond=0
            )
        except ValueError:
            # Fallback if invalid hour
            return 60

        # Calculate time difference
        delta = dt_close - dt_current
        minutes = int(delta.total_seconds() / 60)

        # Return 0 if past close time
        return max(0, minutes)

    def _reset_state(self, symbol: str):
        """Reset tracking state for a symbol"""
        state = self._state[symbol]
        state['window_n'] = 0
        state['observation_idx'] = 0
        state['bars_held'] = 0
        state['max_price_obs'] = 0.0
        state['prev_bullish'] = False