# strategies/buy_and_hold.py
from .base import Strategy
from database.schema import Bar

class BuyAndHold(Strategy):
    def on_start(self):
        # Buy 100 shares at first bar
        pass

    def next(self, bar: Bar):
        # We only want to buy if we haven't bought yet
        if self.portfolio.position == 0:
            # Calculate how many shares we can buy (e.g., 95% of cash to be safe)
            # Or just buy a fixed amount as you had before

            # option A: Buy fixed amount
            self.portfolio.buy(100, bar.close)

            # option B (Better): Buy max possible
            # amount_to_invest = self.portfolio.cash
            # shares = amount_to_invest / bar.close
            # self.portfolio.buy(shares, bar.close)