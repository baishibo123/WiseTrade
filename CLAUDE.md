# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

*Layer discipline (`docs/collaboration-protocol.md` §4): this file holds **invariants and
conventions** — rules that hold across the project's life. Decisions with alternatives and
rationale go in `docs/decisions.md`; current state lives in the code and git history and
must not be duplicated here, because it goes stale and then misleads.*

## Working Norms

Process patterns Claude should follow when collaborating in this repo. (Personal
engineering principles live in `docs/principles.md`; architectural decisions live
in `docs/decisions.md`; the design-conversation workflow lives in
`docs/collaboration-protocol.md`. This section is specifically about AI-side behavior.)

### Judgment

- **Drill depth = failure cost × detectability.** Scale scrutiny to how bad and how
  invisible a failure would be. Expensive-and-silent (fill/execution, PnL, data
  alignment) gets full verification; loud-when-wrong gets a glance. This governs what
  to raise with me, not just how carefully to write it.

- **One black box at a time.** Don't build unverified logic on top of a dependency
  whose behavior isn't understood. If a change would require both at once, say so and
  stop rather than proceeding on assumption — never two unknowns compounding.

- **Explanations land on mechanism.** Tell me what something is doing, not which API
  to call. An explanation I can't restate mechanistically hasn't landed.

### Process

- **Walking skeleton first, then layer by layer.** When starting implementation after
  a design phase, build the thinnest end-to-end that actually runs on real data — one
  input, one output, no parallelism, no logging, no atomic writes. Verify it works,
  then add layers one at a time. This is an *integration flow test*, not a module
  smoke test: real data + real components surface integration mismatches; fake inputs
  only validate the new code in isolation. Each subsequent layer adds exactly one
  failure mode.

- **Read the existing surface before building on it.** Before designing code that
  calls into existing modules, read the actual signatures (`grep -n "def __init__"`,
  function shapes, config keys). Skipping this step surfaces integration mismatches as
  runtime errors after substantial code has already been written. This is the
  implementation-side counterpart to the bounded flow diagram in
  `collaboration-protocol.md` §2.1 — the diagram names the seam, this reads it.

- **Notify at commit-worthy moments; never auto-commit.** Flag natural checkpoints
  (ADRs settled, a layer compiles cleanly, a bug is fixed, before a risky operation)
  so the user can decide whether to commit. Do not run `git commit` autonomously.

- **Run /review after substantial implementations.** New subsystems get a `/review`
  pass before being declared done — catches asymmetries, dead code, and small drift
  that incremental smoke tests don't.

## Project Overview

**WiseTrade** — event-driven backtesting framework for multi-symbol portfolio strategies
on 1-minute US equity data. No external backtesting packages.

## Environment

Machine-specific paths come from the environment; everything inside the repo is derived
from `PROJECT_ROOT`. Never add OS detection to a path expression — correct cross-platform
pathing here means deriving from `PROJECT_ROOT` or reading the environment.

| Variable | Purpose |
|---|---|
| `WISETRADE_RAW_DATA_ROOT` | Root of the raw 1-minute CSV tree (ingest only) |
| `WISETRADE_SPLITS_CSV` | Corporate-splits CSV (ingest only) |
| `WISETRADE_SQLITE_DB_PATH` | Override the bar database; defaults to `db/` under the repo |
| `WISETRADE_DB_TYPE` | `sqlite` (default) or `postgresql` |
| `WISETRADE_MP_START_METHOD` | `spawn` (default) or `fork` — see ADR-015 |

Dependencies are declared in `pyproject.toml`; Python ≥ 3.12.

## Commands

```bash
# Build the bar database: CSV -> bars, splits -> adj_factors -> validate
python utils/build_database.py            # TECH_100
python utils/build_database.py --all      # every symbol in the CSV tree
python utils/build_database.py --skip-bars  # rebuild only splits/factors

# Batch backtest across TECH_100, per-symbol mode
# Output: results/<batch_id>/ with runs/, curves/, manifest.json, errors.log
python run_ranking.py
```

No build, lint, or unit test tooling configured.

---

## Architecture

```
DatabaseFeed → TimeAlignedIterator → Strategy → Portfolio → Analyzer
                                                     │
                       core/batch/  BatchConfig → BatchRunner → workers → manifest
```

| Component | Responsibility | Does NOT Handle |
|-----------|---------------|-----------------|
| **DatabaseFeed** | Stream bars from `bars_adjusted` | Time sync, indicators |
| **TimeAlignedIterator** | Synchronize multi-symbol bars | Signal generation, trading |
| **Strategy** | Generate signals from indicators | Trade execution, capital |
| **Portfolio** | Position & capital management | Signal generation |
| **Engine** | Orchestrate event loop | Business logic |
| **Analyzer** | Performance metrics | Storage, visualization |
| **core/batch** | Parallel execution, resumability, manifest | Any per-run business logic |

### Event loop (inside `core/engine.py`)

Each tick yields `(timestamp, {symbol: Bar})` from `TimeAlignedIterator`:
1. `strategy.update_bar(symbol, bar)` — appends to `self.history[symbol]`, calls `_update_indicators(symbol)`
2. `strategy.next(bars)` → signals dict
3. `portfolio.process_signals(signals, bars)` — ranks by `score`, executes within constraints
4. `portfolio.update(bars, timestamp)` — marks equity curve point

### Strategy interface

Subclass `strategies/base.py:Strategy`. Must implement `next()`; optionally override
`_update_indicators()`, `on_start()`, `on_end()`.

```python
def next(self, bars: Dict[str, Bar]) -> Dict[str, dict]:
    ...
```

Indicators go in `self._indicators[symbol]` (pure functions from `strategies/indicators.py`).
History access: `get_closes()`, `get_opens()`, `get_highs_lows()`, `get_volumes()`.
Position access: `has_position()`, `get_position_size()`.

`on_end()` must **not** force-close positions. A forced exit at the backtest boundary is an
artifact of where the window ends, not a decision the strategy made, and scoring it as a
trade corrupts the trade statistics. Open positions are reported as open episodes and
excluded from win rates; the equity curve already marks them to market, so returns-based
metrics are unaffected either way.

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

Full rationale and alternatives in `docs/decisions.md`. Summarised here only where the rule
affects how you write code against these modules.

**Two batch modes** (ADR-009). `PortfolioBatchConfig`: one task per (strategy, params), the
whole universe sharing one pot of capital — signals compete and Portfolio ranks by `score`.
`PerSymbolBatchConfig`: one task per (strategy, params, symbol), each with its own full
capital, so per-symbol metrics are directly comparable (ADR-011). The execution engine is
unified; only task enumeration differs.

**Strategy suggests, Portfolio enforces.** Strategy expresses intent (quantity or target
allocation); Portfolio applies hard limits (`max_position_pct`, `min_trade_size`,
`max_positions`, available cash).

**Incremental position building.** Repeated BUY signals on the same symbol accumulate shares;
Portfolio updates weighted average cost automatically.

**Indicators as pure functions** (`strategies/indicators.py`): decoupled from Strategy
instances — independently testable, reusable, easier to optimize.

**Reuse is the default; overwriting is the exposed switch** (ADR-021). Running a batch
submits only tasks with no committed result. `overwrite=True` is a deliberate, destructive
human override for what the hash cannot see, and is logged at WARNING.

---

## Invariants

Rules that must hold. Violating one of these is a bug even if nothing raises.

**Timestamps are Unix milliseconds UTC throughout** — database, signals, config, task
definitions.

**Date boundaries are exchange-local, never UTC.** A trading day is a property of
`America/New_York`, so any conversion from a calendar date to an instant must go through
the exchange timezone. Midnight UTC is 19:00–20:00 ET on the *previous* day — inside the
prior session. This is why split boundaries use ET midnight (`database/adjustments.py`).

**Bar counts are not uniformly interchangeable with durations.** Three distinct cases:

| Kind | Example | Rule |
|---|---|---|
| Genuinely a count | `slow_period=20` — a 20-observation SMA | Keep as a count. "20 minutes" would be wrong. |
| A duration wearing a count | `window_n=390  # 1 trading day` | Convert to time. Breaks on half-days, gaps, extended hours. |
| A conversion factor | `252`, `390`, `365.25` | Measure from the data. Never assume. |

**The unit of trade accounting is the position episode, not the fill.** An episode is
`0 → nonzero → 0`, with every add and trim rolling up into one record (`core/episodes.py`).
Fill-based statistics are fragmentation-sensitive: slicing one exit into sixteen moved a
reported win rate from 50% to 94% with identical economics. Fill counts survive as
diagnostics (`avg_entry_fills`, `avg_exit_fills`) and must never be denominators.

**Returns-based metrics are ground truth; trade statistics are diagnostics.** Anything
derived from the mark-to-market equity curve (`sharpe`, `max_drawdown_pct`, `cagr_pct`,
`total_return_pct`) is immune to how fills are sliced. Everything below that line is not.

**`run_id` must cover every result-affecting input.** It hashes strategy identity, `VERSION`,
params, universe, time range and portfolio config (ADR-013). Any newly introduced input that
changes results — a bar filter, the data itself — must enter the hash or be recorded as a
known gap (ADR-022). The hash has historically covered *strategy* inputs well and
*environment* inputs badly; check new ones by reflex.

**Bump `VERSION` on a strategy whenever its behavior changes**, including bug fixes and
changed hardcoded constants (ADR-004). Forgetting silently reuses results produced by code
that no longer exists; bumping unnecessarily costs one recomputation. Err toward bumping.
Note the transitive hole: editing a shared function in `indicators.py` changes every
strategy's behavior while no strategy's `VERSION` changes.

---

## Data

### Source

Vendor CSVs laid out as `<WISETRADE_RAW_DATA_ROOT>/YYYYMM/YYYYMMDD/SYMBOL.csv`, columns
`exchange,symbol,open,high,low,close,amount,volume,bob,eob,type`, UTF-8 with BOM.

- **`eob`, not `bob`.** A bar timestamped at its close can be acted on at that instant;
  using `bob` would let a strategy see a close one minute before it happened.
- **Day folders are UTC dates, not trading dates.** A session's post-20:00-ET tail lands in
  the *next* day's folder — which is why there are Saturday folders holding Friday evening.
  Never infer a trading session from a folder name.
- **Extended hours are included**, roughly 04:00–20:00 ET. Regular session is 09:30–16:00 ET
  (390 bars). Coverage varies enormously by liquidity — on one sample day AAPL had 797 bars
  and a thin name had 15.
- `amount` is dollar notional and is currently dropped at ingest; `volume` is shares.

### Database

One SQLite file. Split adjustment is a **derived view over immutable raw bars**, so there is
no separate "adjusted" database to keep in sync.

```sql
bars          (symbol, datetime, open, high, low, close, volume)  PK (symbol, datetime)
splits        (symbol, ex_date, from_shares, to_shares)           PK includes the ratio
adj_factors   (symbol, valid_from, valid_to, cum_factor)          derived from splits
bars_adjusted VIEW: bars ⋈ adj_factors — price / factor, volume * factor
```

**Always read `bars_adjusted`, never `bars`.** Reading the raw table silently yields
unadjusted prices — NVDA drops 10× on 2024-06-10 and every metric spanning that date is wrong.

- **Prices in `bars` are unadjusted.** `factor = to_shares / from_shares`; bars *before* a
  split are divided by the cumulative product of every split after them, volume multiplied.
- The provider's `美股复权计算方法.md` states `前复权价格 = 原始价格 × 累计因子`, which is
  the wrong operator for a factor defined this way, and its example table inverts which side
  of the split needs adjusting. The data settles it — trust the data, not the doc.
- **Dividends: the provider states raw prices are already dividend-adjusted** ("原始价格已经
  对分红做了处理"). This is *unverified* — a spot check against recalled closes was
  inconclusive at ~1% resolution. Settling it requires checking price behavior across known
  ex-dividend dates.
- `validate_adjustments()` is the backstop: it compares adjusted closes across every split
  boundary. Ratio ≈ 1 is correct, ≈ 1/factor means unadjusted, ≈ factor means double-adjusted.
- **Prod:** PostgreSQL is supported in `DatabaseFeed` via `WISETRADE_DB_TYPE=postgresql`.
- **Universe:** `TECH_100` in `database/sqlite_db.py`.

### Known data caveats

- **`TimeAlignedIterator` forward-fills missing bars** across symbols. This path is not yet
  validated against real gappy data.
- Bar counts legitimately differ per symbol — extended hours, intraday gaps, listings and
  delistings.
- `splits.csv` contains announced but not-yet-effective splits; `build_adj_factors` filters
  by `as_of`. It also contains symbols with two rows on one ex-date, which are compounded
  with a warning — some are genuine same-day forward+reverse pairs, others are one ratio
  written two ways, and only a human can tell them apart.
