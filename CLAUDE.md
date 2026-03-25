# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**WiseTrade** — event-driven backtesting framework for multi-symbol portfolio strategies on 1-minute US equity data. Scale: ~74M bars (10 years × ~100 stocks × 1-min). No external backtesting packages.

## Commands

```bash
# Validate data quality
python tests/diagnose_bar_gaps.py

# Run end-to-end integration test (requires database)
python tests/test_end_to_end.py

# Run single strategy on one symbol
python run.py

# Run all strategies across TECH_100 universe → results/ranking_*.csv
python run_ranking.py
```

No build, lint, or unit test tooling configured. Tests are scripts run directly.

---

## Architecture

```
DatabaseFeed → TimeAlignedIterator → Strategy → Portfolio → Analyzer
```

| Component | Responsibility | Does NOT Handle |
|-----------|---------------|-----------------|
| **DatabaseFeed** | Stream bars from SQLite/PostgreSQL | Time sync, indicators |
| **TimeAlignedIterator** | Synchronize multi-symbol bars | Signal generation, trading |
| **Strategy** | Generate signals from indicators | Trade execution, capital |
| **Portfolio** | Position & capital management | Signal generation |
| **Engine** | Orchestrate event loop | Business logic |
| **Analyzer** | Performance metrics | Storage, visualization |

### Event loop (inside `core/engine.py`)

Each tick yields `(timestamp, {symbol: Bar})` from `TimeAlignedIterator`:
1. `strategy.update_bar(symbol, bar)` — appends to `self.history[symbol]`, calls `_update_indicators(symbol)`
2. `strategy.next(bars)` → signals dict
3. `portfolio.process_signals(signals, bars)` — ranks by `score`, executes within constraints
4. `portfolio.update(bars, timestamp)` — marks equity curve point

### Strategy interface

Subclass `strategies/base.py:Strategy`. Must implement `next()`; optionally override `_update_indicators()`, `on_start()`, `on_end()`.

```python
def next(self, bars: Dict[str, Bar]) -> Dict[str, dict]:
    ...
```

Indicators go in `self._indicators[symbol]` (pure functions from `strategies/indicators.py`). History access: `get_closes()`, `get_opens()`, `get_highs_lows()`, `get_volumes()`. Position access: `has_position()`, `get_position_size()`.

### Signal format

```python
# BUY
{"action": "BUY", "score": 0.8, "quantity": 10.0}          # or "target_allocation": 0.15
# SELL
{"action": "SELL", "score": 0.5, "sell_pct": 0.5}          # omit sell_pct for full close
```

Priority chain for sizing: `quantity` > `target_allocation` > default (1 share + warning).

### Engine instantiation

```python
engine = Engine(
    universe=["AAPL", "MSFT"],
    strategy_class=MyStrategy,
    start_datetime=1751414400000,  # Unix millis UTC
    end_datetime=1764057600000,
    strategy_params={"fast": 10, "slow": 20},
    portfolio_config={"initial_cash": 100_000, "max_positions": 10, "max_position_pct": 0.3}
)
analyzer = engine.run()
```

---

## Key Design Decisions

**Single shared portfolio** (not N independent ones): $100k total capital shared across all symbols. Signals compete — Portfolio ranks by `score` and executes top-N within cash/position constraints. This reflects real diversification via capital allocation.

**Strategy suggests, Portfolio enforces**: Strategy expresses intent (quantity or target allocation); Portfolio applies hard limits (max_position_pct=30%, min_trade_size, max_positions, available cash).

**Incremental position building**: Repeated BUY signals on the same symbol accumulate shares; Portfolio updates weighted average cost automatically.

**Indicators as pure functions** (`strategies/indicators.py`): Decoupled from Strategy instances — independently testable, reusable across strategies, and easier to optimize.

---

## Critical Technical Details

- **Timestamps:** Unix milliseconds UTC throughout. All DB datetimes and signal timestamps use this format.
- **Trading hours:** Regular session 9:30–16:00 ET (390 bars/day). ~50% of raw DB bars are extended hours. Use `diagnose_bar_gaps.py` to filter/validate.
- **UTC offset:** DST → 13:30–20:00 UTC; standard → 14:30–21:00 UTC.
- **Corporate actions:** Splits pre-applied (4,708 splits). Prices forward-adjusted: `adjusted = original / factor`. Volumes inverse-adjusted. **Dividends not adjusted** (acceptable for low-yield tech stocks).
- **Bar gaps:** `TimeAlignedIterator` forward-fills missing bars across symbols. Bar counts legitimately differ per symbol due to extended hours and intraday gaps.

---

## Database

```sql
-- Primary table
CREATE TABLE bars (
    symbol TEXT NOT NULL,
    datetime INTEGER NOT NULL,  -- Unix millis UTC
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (symbol, datetime)
);
```

- **Dev:** SQLite at `db/us_market_1min_adjusted.sqlite` (~3 GB)
- **Prod:** PostgreSQL — already supported in `DatabaseFeed`; switch via `WISETRADE_DB_TYPE=postgresql`
- **Universe:** TECH_100 defined in `database/sqlite_db.py`
- **Raw source:** CSV files under `E:/stock` (Windows path)

---

## Current Limitations

- No dividend adjustment (only splits) — ~1–2% annual impact
- Single-threaded — no parallelization across symbols or strategies
- Asset allocation design incomplete — unclear how strategies should express sizing preferences beyond explicit quantity/allocation

## Development Priorities

**Immediate:**
1. Batch backtesting — run strategies across all symbols, rank results
2. Performance analysis — identify which stocks/conditions suit each strategy
3. Asset allocation design — cleaner API for strategies to express sizing intent

**Medium-term:** Pattern recognition, equity curve visualization, parameter grid search / walk-forward analysis

**Research phase:** Graphical strategy development, LLM-driven strategy synthesis, alpha/risk factor separation
