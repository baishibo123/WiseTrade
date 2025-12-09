from strategies.base import Strategy


class SMA_OS_Fixed(Strategy):
    """
    Entry: SMA(10) > SMA(20) [Fresh Cross]
    Exit: Optimal Stopping with Fixed Window (N=390 bars).
    """

    def __init__(self, params=None):
        super().__init__(params)
        # Entry Params
        self.fast_period = 10
        self.slow_period = 20

        # Exit Params (Optimal Stopping)
        self.window_n = 390  # Full trading day (6.5 hours)
        self.observation_idx = int(self.window_n * 0.37)  # ~144 bars

        # State
        self.bars_held = 0
        self.max_price_obs = 0.0
        self.prev_bullish = False  # To track "Fresh" crossover

    def next(self, bar):
        self.update_bar(bar)

        # 1. Calc Indicators
        fast = self.sma(self.fast_period)
        slow = self.sma(self.slow_period)
        if fast is None or slow is None:
            return

        # 2. Check Current Position (Float in Portfolio)
        # We access self.portfolio.position directly (float)
        position_size = self.portfolio.position
        is_invested = position_size > 0.01  # account for float rounding

        # 3. EXIT LOGIC (If we hold a position)
        if is_invested:
            self.bars_held += 1

            # Phase A: Observation (First 37% of N)
            if self.bars_held <= self.observation_idx:
                if bar.close > self.max_price_obs:
                    self.max_price_obs = bar.close

            # Phase B: Selection (Remaining 63%)
            elif self.bars_held <= self.window_n:
                # Sell if price beats the benchmark established in Phase A
                if bar.close > self.max_price_obs:
                    self.portfolio.sell(position_size, bar.close)
                    self._reset_exit_state()
                    return

            # Phase C: Time Limit Reached (Hard Close)
            if self.bars_held >= self.window_n:
                self.portfolio.sell(position_size, bar.close)
                self._reset_exit_state()
                return

        # 4. ENTRY LOGIC (If flat)
        else:
            is_bullish = fast > slow

            # Check for FRESH crossover: Currently Bullish AND Previously NOT Bullish
            if is_bullish and not self.prev_bullish:
                # fixed 100 shares (portfolio will deal with insufficient fund situation
                qty = 100
                self.portfolio.buy(qty, bar.close)
                # Initialize Exit State
                self.bars_held = 0
                self.max_price_obs = -1.0

            # Update state for next bar
            self.prev_bullish = is_bullish

    def _reset_exit_state(self):
        self.bars_held = 0
        self.max_price_obs = 0.0