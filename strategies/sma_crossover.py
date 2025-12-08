from strategies.base import Strategy


class SMACrossover(Strategy):
    def __init__(self, params=None):
        super().__init__(params)
        self.fast_period = self.get_param('fast', 10)
        self.slow_period = self.get_param('slow', 30)
        self.qty = 0.5  # Fixed trade size example (respecting min > 0.1)

    def next(self, bar):
        # 1. CRITICAL: Update history first
        self.update_bar(bar)

        # 2. Calculate Indicators
        sma_fast = self.sma(self.fast_period)
        sma_slow = self.sma(self.slow_period)

        # We need both to exist to proceed
        if sma_fast is None or sma_slow is None:
            return

        # 3. Trading Logic
        # We need the PREVIOUS values to detect a "Cross".
        # Since self.history is updated, self.sma() returns CURRENT SMA.
        # This is a simplified "State" check (Fast > Slow = Bullish).
        # For a true "Crossover" (change of state), we would store 'prev_state'.

        current_pos = self.portfolio.positions.get("TECH_100", 0)

        # Golden Cross (Fast is above Slow) -> Long
        if sma_fast > sma_slow:
            if current_pos == 0:
                self.portfolio.buy("TECH_100", bar.close, self.qty)

        # Death Cross (Fast is below Slow) -> Close Position
        elif sma_fast < sma_slow:
            if current_pos > 0:
                self.portfolio.sell("TECH_100", bar.close, current_pos)