# BUY signals:
# Option 1: Explicit quantity (shares to add)
{"action": "BUY", "score": 0.8, "quantity": 5.0}

# Option 2: Explicit allocation (% of total portfolio to add)
{"action": "BUY", "score": 0.8, "target_allocation": 0.10}

# Option 3: Default fallback (buy 1 share - for testing/defensive)
{"action": "BUY", "score": 0.8}  # → buys 1.0 share

# SELL signals:
# Option 1: Explicit quantity (shares to sell)
{"action": "SELL", "score": 0.5, "quantity": 3.0}

# Option 2: Percentage of position (sell X% of held shares)
{"action": "SELL", "score": 0.5, "sell_pct": 0.5}  # Sell 50% of position

# Option 3: Default (sell entire position)
{"action": "SELL", "score": 0.5}  # → sells all shares