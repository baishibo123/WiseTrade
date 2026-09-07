from strategies.base import Strategy


class SMA_ATR_Exit(Strategy):
    def __init__(self, params=None):
        super().__init__(params)

        # Entry Params
        self.fast_period = 10
        self.slow_period = 20
        self.qty = 100

        # Exit Params (ATR)
        self.atr_period = self.get_param('atr_period', 14)
        self.atr_multiplier = self.get_param('atr_mult', 3.0)

        # State
        self.prev_bullish = None
        self.trailing_stop_price = 0.0

    def next(self, bar):
        # 1. Update History (Stores full Bar object now)
        self.update_bar(bar)

        # 2. Calculate Indicators
        sma_fast = self.sma(self.fast_period)
        sma_slow = self.sma(self.slow_period)
        atr_value = self.atr(self.atr_period)

        # Ensure we have enough data
        if sma_fast is None or sma_slow is None or atr_value is None:
            return

        # 3. Market State
        is_bullish = sma_fast > sma_slow

        # Init prev state on first valid bar
        if self.prev_bullish is None:
            self.prev_bullish = is_bullish
            return

        current_pos = self.portfolio.position

        # --- EXIT LOGIC (Trailing Stop) ---
        if current_pos > 0.1:
            # 1. Calculate the potential new stop price based on current close
            # Logic: We want the stop to be below the current price by N * ATR
            potential_new_stop = bar.close - (atr_value * self.atr_multiplier)

            # 2. Ratchet Logic: Only move stop UP, never down.
            if potential_new_stop > self.trailing_stop_price:
                self.trailing_stop_price = potential_new_stop

            # 3. Check if HIT (If Low dipped below our stop)
            if bar.low <= self.trailing_stop_price:
                # Sell everything
                self.portfolio.sell(current_pos, bar.close )
                self.trailing_stop_price = 0.0  # Reset
                # Note: We do not return here, in case a buy signal happens same bar
                # (though unlikely with fresh cross logic)

        # --- ENTRY LOGIC ---
        # 1. Fresh Cross (Bullish now, wasn't before)
        # 2. Position Guard (Not already holding)
        if is_bullish and not self.prev_bullish:
            if current_pos < 0.1:
                self.portfolio.buy(self.qty, bar.close )

                # SET INITIAL STOP LOSS
                # We set it immediately upon entry
                self.trailing_stop_price = bar.close - (atr_value * self.atr_multiplier)

        # Update state
        self.prev_bullish = is_bullish