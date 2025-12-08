from strategies.base import Strategy


class OptimalStopping37(Strategy):
    """
    Implements the Secretary Problem (37% Rule) for SELLING.

    Logic:
    1. Define a window (e.g., 100 bars).
    2. Observation Phase (First 37%): Do nothing, just record the Max Price seen.
    3. Action Phase (Remaining 63%): Sell immediately if Price > Max Price.
    4. Forced Exit: If no trigger by end of window, sell at the last bar.
    """

    def __init__(self, params=None):
        super().__init__(params)
        # Params
        self.window_size = self.get_param('window', 100)
        self.stop_ratio = 0.37
        self.observation_cutoff = int(self.window_size * self.stop_ratio)

        # State
        self.bars_seen = 0
        self.max_price_observation = 0.0
        self.test_mode = self.get_param('test_mode', True)  # Force a buy to test the exit

    def next(self, bar):
        self.update_bar(bar)  # Maintain history if needed
        self.bars_seen += 1

        # ----------------------------------------
        # 0. Test Mode: Buy on Bar 1 to have inventory
        # ----------------------------------------
        if self.test_mode and self.bars_seen == 1:
            # Buy 10 shares to test the exit logic
            self.portfolio.buy("TECH_100", bar.close, 10.0)
            return

        # We only act if we have a position to sell
        current_position = self.portfolio.positions.get("TECH_100", 0)
        if current_position <= 0:
            return

        # ----------------------------------------
        # 1. Observation Phase (0 to 37%)
        # ----------------------------------------
        if self.bars_seen <= self.observation_cutoff:
            if bar.close > self.max_price_observation:
                self.max_price_observation = bar.close

        # ----------------------------------------
        # 2. Action Phase (37% to 100%)
        # ----------------------------------------
        elif self.bars_seen <= self.window_size:
            # The Magic Rule: Sell if we beat the benchmark
            if bar.close > self.max_price_observation:
                self.portfolio.sell("TECH_100", bar.close, current_position)
                # Reset for next window?
                # For this specific Algo, we usually stop after one success.
                # If you want it continuous, reset self.bars_seen = 0 here.

        # ----------------------------------------
        # 3. Forced Exit (End of Window)
        # ----------------------------------------
        if self.bars_seen >= self.window_size:
            if current_position > 0:
                self.portfolio.sell("TECH_100", bar.close, current_position)

            # Reset logic for the NEXT window (Continuous Loop)
            self.bars_seen = 0
            self.max_price_observation = 0.0