from strategies.base import Strategy
import datetime


class SMA_OS_Dynamic(Strategy):
    """
    Entry: SMA(10) > SMA(20) [Fresh Cross]
    Exit: Optimal Stopping with Dynamic Window.
          Calculates N based on (Market Close Time - Current Time).
    """

    def __init__(self, params=None):
        super().__init__(params)
        self.fast_period = 10
        self.slow_period = 20

        # PARAMETER: Market Close Hour in UTC
        # US Market Close (4:00 PM ET) is:
        # 20:00 UTC (during Daylight Savings / Summer)
        # 21:00 UTC (during Standard Time / Winter)
        self.market_close_hour_utc = self.get_param('market_close_hour_utc', 21)

        # State
        self.window_n = 0
        self.observation_idx = 0
        self.bars_held = 0
        self.max_price_obs = 0.0
        self.prev_bullish = False

    def next(self, bar):
        # 1. Update History
        self.update_bar(bar)

        # 2. Indicators
        fast = self.sma(self.fast_period)
        slow = self.sma(self.slow_period)
        if fast is None or slow is None:
            return

        # 3. Position Check (Float)
        position_size = self.portfolio.position
        is_invested = position_size > 0.001

        # --- EXIT LOGIC ---
        if is_invested:
            self.bars_held += 1

            force_sell = False

            # Phase A: Observation
            if self.bars_held <= self.observation_idx:
                if bar.close > self.max_price_obs:
                    self.max_price_obs = bar.close

            # Phase B: Action
            elif self.bars_held <= self.window_n:
                if bar.close > self.max_price_obs:
                    self.portfolio.sell(position_size, bar.close )
                    force_sell = False
                    return

            # Check Time Limit
            if self.bars_held >= self.window_n:
                force_sell = True

            if force_sell:
                self.portfolio.sell(position_size, bar.close )

        # --- ENTRY LOGIC ---
        else:
            is_bullish = fast > slow

            # Fresh CrossCheck
            if is_bullish and not self.prev_bullish:

                # 1. Calculate Dynamic Window using bar.datetime (Unix Millis)
                minutes_remaining = self._get_minutes_to_close(bar.datetime)

                # Filter: Only trade if we have at least 30 mins left in the day
                if minutes_remaining > 30:
                    # Sizing: 95% of Cash
                    qty = 100

                    if qty >= self.portfolio.min_trade_size:
                        self.portfolio.buy(qty, bar.close )

                        # Set Dynamic Optimal Stopping Params
                        self.window_n = minutes_remaining
                        self.observation_idx = int(self.window_n * 0.37)

                        # Reset Exit State
                        self.bars_held = 0
                        self.max_price_obs = -1.0

            self.prev_bullish = is_bullish

    def _get_minutes_to_close(self, timestamp_ms: int) -> int:
        """
        Converts Unix Millis (UTC) to minutes remaining until Market Close (UTC).
        """
        # 1. Convert ms to seconds
        timestamp_sec = timestamp_ms / 1000.0

        # 2. Create UTC Datetime object
        dt_current = datetime.datetime.utcfromtimestamp(timestamp_sec)

        # 3. Create Target Close Time for THIS specific day
        # We replace the hour with our configured close hour (e.g., 21 for 9 PM UTC)
        try:
            dt_close = dt_current.replace(
                hour=self.market_close_hour_utc,
                minute=0,
                second=0,
                microsecond=0
            )
        except ValueError:
            # Handle edge case: if current time is technically "tomorrow" in UTC
            # or invalid hour, fallback to 60 mins default
            return 60

        # 4. Calculate Delta
        delta = dt_close - dt_current
        minutes = int(delta.total_seconds() / 60)

        # Safety: If we are somehow PAST the close (negative), return 0
        return max(0, minutes)