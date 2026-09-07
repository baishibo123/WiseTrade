from strategies.base import Strategy


class SMACrossover(Strategy):
    def __init__(self, params=None):
        super().__init__(params)
        self.fast_period = self.get_param('fast', 10)
        self.slow_period = self.get_param('slow', 20)
        self.qty = 100  # Fixed trade size

        # State tracking for "Fresh Cross"
        # We initialize as None so we don't trigger a trade on the very first bar
        self.prev_bullish = None

    def next(self, bar):
        # 1. CRITICAL: Update history first
        self.update_bar(bar)

        # 2. Calculate Indicators
        sma_fast = self.sma(self.fast_period)
        sma_slow = self.sma(self.slow_period)

        # We need both to exist to proceed
        if sma_fast is None or sma_slow is None:
            return

        # 3. Determine Market State
        is_bullish = sma_fast > sma_slow

        # Initialize state on the very first valid bar without trading
        if self.prev_bullish is None:
            self.prev_bullish = is_bullish
            return

        # 4. Trading Logic

        # Get current position size (Float)
        current_pos = self.portfolio.position

        # --- BUY LOGIC ---
        # Condition 1: Must be a FRESH cross (Currently Bullish, Previously Not)
        if is_bullish and not self.prev_bullish:
            # Condition 2: If holding positions, do not trigger another buying
            if current_pos < 0.1:
                self.portfolio.buy(self.qty, bar.close)

        # --- SELL LOGIC ---
        # Condition: Fresh Death Cross (Currently Not Bullish, Previously Bullish)
        elif not is_bullish and self.prev_bullish:
            if current_pos > 0:
                self.portfolio.sell(current_pos, bar.close)

        # 5. Update State for next bar
        self.prev_bullish = is_bullish