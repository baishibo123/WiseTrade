# WiseTrade - Multi-Symbol Portfolio Backtesting Framework

**Status:** Core MVP Complete | **Date:** March 2026

## Overview

Event-driven backtesting framework for multi-symbol portfolio strategies using 1-minute US stock data. Processes synchronized bar data across multiple symbols with realistic capital constraints and comprehensive performance analytics.

**Scale:** 10 years × 1-minute bars × ~100 stocks (~74M bars total)

---

## Architecture Philosophy

### Core Principle: Separation of Concerns
```
DatabaseFeed → TimeAlignedIterator → Strategy → Portfolio → Analyzer
```

| Component | Responsibility | Does NOT Handle |
|-----------|---------------|-----------------|
| **DatabaseFeed** | Stream bars from DB | Time sync, indicators |
| **TimeAlignedIterator** | Synchronize multi-symbol bars | Signal generation, trading |
| **Strategy** | Generate signals from indicators | Trade execution, capital |
| **Portfolio** | Position & capital management | Signal generation |
| **Engine** | Orchestrate event loop | Business logic |
| **Analyzer** | Performance metrics | Storage, visualization |

---

## Key Design Decisions

### 1. Why Multi-Symbol Portfolio?

**Problem with separate portfolios:**
```
N separate portfolios = N × $100k capital (unrealistic)
No selection logic when signals compete
```

**Solution:**
```
1 shared portfolio = $100k total capital
Selection: Rank signals by score, execute top-N within constraints
```

**Critical Insight:** Real diversification comes from capital allocation decisions, not averaging independent backtests.

---

### 2. Signal-Driven Portfolio

**Who decides position size?**

**Answer: Both - Strategy suggests, Portfolio enforces**
```python
# Strategy specifies intent:
{
    "action": "BUY",
    "score": 0.8,
    "quantity": 10.0  # OR "target_allocation": 0.15
}

# Portfolio enforces constraints:
- Available cash
- max_position_pct (30%)
- min_trade_size
- max_positions limit
```

**Priority chain:** `quantity` > `target_allocation` > default (1 share with warning)

---

### 3. Incremental Position Building

Strategy controls via multiple signals:
```python
t=100: BUY 5 shares → position = 5
t=200: BUY 5 shares → position = 10 (avg cost updated)
t=300: BUY 5 shares → position = 15
```

Portfolio updates weighted average cost automatically.

---

### 4. Why Separate Indicators Module?

**Decision:** Pure functions in `indicators.py` vs methods in Strategy

**Rationale:**
- Unit testable without strategy scaffolding
- Reusable across all strategies
- Cleaner strategy code (logic-focused)
- Easier to optimize

---

## Critical Technical Details

### Timestamps
- **Format:** Unix milliseconds (UTC)
- **Source:** All current data is UTC
- **Future Risk:** RT data may use different timezone (needs flow adjustment)

### Trading Hours
- **Regular:** 9:30 AM - 4:00 PM ET (390 bars/day)
- **UTC (DST):** 13:30 - 20:00
- **Data Reality:** ~50% bars are pre/post-market (extended hours)
- **Validation:** `diagnose_bar_gaps.py` filters to regular hours

### Corporate Actions
- **Method:** Pre-process database (forward adjustment)
- **Formula:** `adjusted_price = original_price / (to_shares / from_shares)`
- **Volume:** Inverse adjustment (`volume × factor`)
- **Status:** 4,708 splits applied
- **Limitation:** Dividends not adjusted (acceptable for low-yield tech stocks)

---

## Database Schema

### Bars Table
```sql
CREATE TABLE bars (
    symbol TEXT NOT NULL,
    datetime INTEGER NOT NULL,  -- Unix millis UTC
    open REAL, high REAL, low REAL, close REAL,
    volume REAL,
    PRIMARY KEY (symbol, datetime)
);
```

### Splits Table
```sql
CREATE TABLE splits (
    symbol TEXT,
    effective_date INTEGER,  -- Unix millis
    from_shares REAL,
    to_shares REAL,
    factor REAL  -- to_shares / from_shares
);
```

**Current:** SQLite (dev)  
**Planned:** PostgreSQL (production) - already supported in DatabaseFeed

---

## Known Issues & Fixes Applied

### Fixed in This Version

1. **TimeAlignedIterator initialization**
   - Issue: Called `next()` before `iter()` on feeds
   - Fix: Create iterators first: `iter(feed)` then `next(iterator)`

2. **CSV BOM encoding**
   - Issue: Chinese data source headers showed as `锘縮ymbol`
   - Fix: Use `encoding='utf-8-sig'` when opening CSV

3. **Datetime deprecation**
   - Old: `datetime.utcfromtimestamp(ts)`
   - New: `datetime.fromtimestamp(ts, tz=timezone.utc)`

### Current Limitations

- **No dividend adjustments** (only splits) - impact ~1-2% annually
- **Single-threaded** - no parallelization yet
- **Asset allocation guidelines** - unclear how strategies should express preferences

---

## Data Quality Insights

**Bar count discrepancies across symbols** (even same date range):
- Caused by: Extended hours data, intraday gaps, holiday half-days
- Solution: TimeAlignedIterator forward-fills missing bars
- Validation: Regular hours filtering shows ~1,770 bars/symbol for 5-day test

**Standard test range:**
- 2025-07-02 09:30 to 2025-07-09 16:00
- Symbols: ["AAPL", "MSFT", "GOOGL"]
- Expected: ~1,770 bars/symbol

---

## Future Development

### Immediate Priorities
1. **Batch backtesting** - Run strategies across all symbols, rank results
2. **Performance analysis** - Identify why certain stocks work better with given strategies
3. **Asset allocation design** - How strategies express sizing preferences

### Medium-Term
- **Pattern recognition** - Abstract frequent patterns, classify setups
- **Visualization** - Equity curves, trade timelines, missed opportunities
- **Parameter optimization** - Grid search, walk-forward analysis

### Advanced (Research Phase)
- **Graphical strategy development** - Visual pattern → code generation
- **LLM-driven backtesting** - Automated strategy synthesis and testing
- **Alpha/risk separation** - Portfolio construction vs signal generation

---

## Development Context

**Developer:** Shibo (Bio/Neuro → CS, systems-oriented learner)

**Philosophy:**
- Understanding mechanisms > following patterns
- Evidence-based decisions
- Iterative: working code → refinement
- Production-quality, not academic exercise

**This system is:**
- Foundation for quantitative research
- Designed for real trading (eventual goal)
- Built with extensibility in mind

---

## Quick Start
```bash
# 1. Validate data
python tests/diagnose_bar_gaps.py

# 2. Run E2E test
python tests/test_end_to_end.py

# 3. Run custom backtest
python run.py  # Or create custom script using Engine API
```

---

## Signal Format Reference
```python
# BUY
{
    "action": "BUY",
    "score": 0.8,  # Required for ranking
    "quantity": 10.0  # OR "target_allocation": 0.15
}

# SELL
{
    "action": "SELL",
    "score": 0.5,
    "quantity": 5.0  # OR "sell_pct": 0.5 OR omit for full close
}
```

---

## Critical Files

**Core Logic:** `core/engine.py`, `core/portfolio.py`, `core/time_iterator.py`  
**Strategy Framework:** `strategies/base.py`, `strategies/indicators.py`  
**Data Access:** `datafeed/db_feed.py`  
**Utilities:** `utils/adjust_database_sql.py`, `utils/load_splits_to_db.py`  
**Testing:** `tests/test_end_to_end.py`, `tests/diagnose_bar_gaps.py`

---

**For detailed implementation, see source code and inline docstrings.**