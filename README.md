# WiseTrade

A lightweight, from-scratch, event-driven backtesting framework.

## To-do list
- engine 49:50 strategy.portfolio = portfolio may split functionality depending on trade off 
- base.py 31, 44 Check if that is essential that ABC Strategy have the att self.bar while calling next() with bar parameter. 
- engine 76:81 should place the Feed empty detection before traversing the bars. preventing further anomaly
- Portfolio att position: further make it feasible for multiple symbols in the same portfolio for easier analysis
- Portfolio 47:49   adding broker feature which will including commission fee. 
- Portfolio: design to decide: update() should make sure excluding price changes within the bar but outside the action range (before buy, after sell)



## Elevator Pitch
A clean, readable, high-performance backtesting system built entirely with standard Python libraries — no external backtesting packages — designed for research, coursework, and real quantitative experiments.

## Key Features
- Pure event-driven architecture (`for bar in feed → strategy.next(bar)`)
- Streaming data feed from SQLite → processes years of 1-min data using < 50 MB RAM
- Realistic portfolio logic with minimum 1-share trading rule
- Industry-standard performance metrics (correct Max Drawdown via `cummax`, annualized Sharpe from minute returns, CAGR, etc.)
- One-line multi-symbol comparison with automatic ranking
- Clean separation of concerns: Feed ↔ Engine ↔ Strategy ↔ Portfolio ↔ Analyzer
- Database filtered to ~100 major tech stocks → final SQLite file ≈ 3 GB (perfectly submittable)
- Fully prepared for future MongoDB backend (interface already abstracted)
- No dependencies beyond pandas, numpy, tqdm, sqlite3

## Project Structure

WiseTrade/ \
├── config.py   # Paths, constants, TECH_100 list, constants\
├── database/\
│   ├── schema.py              # Bar namedtuple + SQL schema\
│   └── sqlite_db.py # Loads your E:\stock CSVs → filtered TECH_100 database\
├── datafeed/\
│   └── db_feed.py             # Memory-efficient streaming SQLiteFeed\
├── core/\
│   ├── portfolio.py           # Cash, positions, trade log, equity curve\
│   ├── engine.py              # Main backtesting loop + run_multiple()\
│   └── analyzer.py            # All performance metrics\
├── strategies/\
│   ├── base.py                # Abstract Strategy class (on_start/next/on_end)\
│   └── examples/              # BuyAndHold, future 37% rule, etc.\
├── load_data.py               # Run once → creates us_market_1min.sqlite\
└── run.py                     # Example runs and comparison table\


## Architecture Overview (simple ASCII)
Raw CSVs (E:\stock)\
↓\
load_data.py → SQLite DB (TECH_100 only)\
↓\
SQLiteFeed → streams Bar objects one-by-one\
↓\
Engine → for bar in feed:\
→ Strategy.next(bar)\
→ Portfolio.update(bar)\
↓\
Analyzer → metrics + equity curve + ranking table\




## Quick Start
```bash
# 1. Place your raw data under E:\stock\... (structure as discussed)
# 2. Build the filtered database (runs ~10–15 minutes)
python load_data.py

# 3. Run Buy & Hold on top 20 tech stocks
python run.py --strategy BuyAndHold --top 20

# 4. See ranking table in console (and optionally saved to results.xlsx)
```

## Sample Output
```
      symbol            strategy  total_return_pct  cagr_pct  sharpe  max_drawdown_pct  num_trades
0       NVDA        BuyAndHold           312.45    148.72   1.921           -34.21          20
1       SMCI        BuyAndHold           278.93    132.10   1.678           -41.05          18
2       TSLA        BuyAndHold           189.67     97.45   1.423           -38.90          22
3       META        BuyAndHold           156.21     82.33   1.589           -22.14          15
4       AMD         BuyAndHold           141.88     76.91   1.312           -29.67          19
```


