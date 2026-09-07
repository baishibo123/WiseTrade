# Architectural Decisions

One paragraph per decision: **what** was decided, **alternatives** considered, **why** this one. Append-only; if a decision is overturned, add a new entry that supersedes it rather than editing the old one.

**Status flags.** An entry with no flag is a settled decision. Whether it has been implemented yet is a question about the code, not about the decision (see `collaboration-protocol.md` §4) — where the gap matters, the entry says so. Entries that are *not* settled are marked in the title and carry an explicit `**Status:**` line, so a reader can never mistake an intention for an established fact:

- `· OPEN` — a decision we know we have to make and have deliberately not made. A provisional default may be in place; the entry says what would trigger revisiting it.
- `· FUTURE` — recorded inspiration. Not scheduled, nothing depends on it, and no current code is shaped by it.

---

## ADR-001: Stay on SQLite (defer PostgreSQL migration)

**Decided:** Continue using SQLite for the bar database. Defer migration to PostgreSQL until there is a concrete trigger.

**Alternatives:** Migrate to PostgreSQL now to support concurrent writes and network access.

**Why:** SQLite supports multiple concurrent readers (WAL mode) at any data scale we will hit. Read-only batch backtests against a single file produce no write contention. Data volume (~3 GB now, projected tens of GB) is well within SQLite's comfortable range. The real triggers for PostgreSQL — concurrent writes from multiple processes, or moving the DB across machines — do not apply today. Migration is reversible and can be done later when an actual need surfaces (e.g., cloud deployment, multi-machine workers, ingesting data types beyond OHLCV bars).

---

## ADR-002: Multiprocessing over threading for batch parallelism

**Decided:** Use Python `multiprocessing` (separate processes) for parallel strategy execution. Threading is reserved for I/O-bound auxiliary work only.

**Alternatives:** Threading-based pool; sequential execution.

**Why:** Backtest event loops are CPU-bound pure Python. The Global Interpreter Lock prevents true parallelism across threads for CPU-bound code, so threads give no speedup for the dominant cost. Process-based parallelism sidesteps the GIL by giving each worker its own interpreter. Process isolation also satisfies the supervisor-pattern requirement: a worker crash (OOM, segfault, strategy bug) cannot corrupt the main process.

---

## ADR-003: ProcessPoolExecutor over multiprocessing.Pool

**Decided:** Use `concurrent.futures.ProcessPoolExecutor` as the worker pool primitive.

**Alternatives:** `multiprocessing.Pool` with `imap_unordered`.

**Why:** `ProcessPoolExecutor` provides cleaner crash semantics (a worker process death surfaces as `BrokenProcessPool` per-future rather than hanging the pool), first-class per-task error inspection via `Future.exception()`, and slightly better Ctrl+C handling. The one capability we lose — chunked task batching for high IPC efficiency on tiny tasks — is irrelevant to our workload, where each task is a multi-minute backtest. The futures API is also more composable if we ever mix with `ThreadPoolExecutor` for I/O work.

---

## ADR-004: Deterministic hash run IDs with VERSION discipline

**Decided:** Each batch task gets a `run_id` that is a deterministic hash of `(strategy_name, strategy.VERSION, sorted_params, universe, time_range)`. Strategies carry a `VERSION` class attribute that must be bumped when behavior changes.

**Alternatives:** Random/sequential IDs (no resume); deep source-code hashing of the strategy class (whitespace-sensitive, brittle).

**Why:** Deterministic IDs enable opt-in resumability: a re-run of the same batch can skip already-committed results. Idempotent re-runs are also free. The cost is that strategy code edits which change behavior without changing name/params would silently produce stale results on resume — addressed by the `VERSION` discipline. Source-hashing was rejected as too brittle (whitespace, transitive deps). Discipline-based versioning is documented in `strategies/base.py`.

---

## ADR-005: Resume is opt-in, not default

**Decided:** Batch runs default to full re-execution. Skipping committed runs requires an explicit `resume=True` (or `--resume`) flag.

**Alternatives:** Resume by default; never resume.

**Why:** Default re-execution is the safer behavior — users should opt into trusting that their strategy code hasn't changed in a way that invalidates prior results. Pairs with ADR-004's VERSION discipline: resume is the user explicitly asserting "I know nothing relevant has changed."

---

## ADR-006: Atomic file writes; result JSON written last as commit signal

**Decided:** All per-run output files are written via temp-file + atomic rename. Within a run, the order is: curve files first, then result JSON last. The existence of `runs/<run_id>.json` is the run-level commit signal.

**Alternatives:** Manifest-based completion tracking (main process is source of truth); direct writes without renames.

**Why:** POSIX `rename()` is atomic — the file either fully exists with new content or doesn't. Crashes mid-write cannot leave half-written final files; they leave temp files only. Writing the result JSON last means: if the JSON exists, all curves for that run must also be committed. This collapses the resumability check to one filesystem call (`os.path.exists`). Orphan curves without a matching JSON are harmless — overwritten on the next run. The manifest becomes a *derived* index built by scanning the directory at batch end, not the authoritative record.

---

## ADR-007: Curves stored as Parquet; footer is file-level commit signal

**Decided:** Per-run curve data (equity history, and eventually per-stock curves) is stored as Parquet via PyArrow's incremental writer. The Parquet footer's magic bytes serve as the file-level commit signal.

**Alternatives:** Custom binary format with hand-rolled finish-mark; CSV; HDF5.

**Why:** Parquet's footer is written only on `writer.close()`. A process killed mid-write produces an unreadable file (no footer) — naturally detectable. This is the user-suggested "finish mark" pattern, but battle-tested rather than hand-rolled. Parquet is also columnar (efficient for time series), well-supported, and avoids inventing a format. CSV was rejected as text-heavy and slow to read; HDF5 has heavier dependencies; custom binary creates maintenance burden.

---

## ADR-008: CurveRecorder abstraction decouples Engine from storage strategy

**Decided:** Introduce a `CurveRecorder` interface. The Engine (or the worker around the Engine) writes through this interface without knowing whether storage is in-memory, Parquet-at-end, or streaming-Parquet.

**Alternatives:** Embed file I/O directly in Engine/Portfolio; require user to extract curves manually after each run.

**Why:** Different scales need different storage strategies. Small runs (single equity curve, ~16 MB) can stay in-memory. Large runs (per-stock curves across a 100-symbol universe, GB-scale) need streaming-to-disk to avoid OOM. The recorder abstraction lets the Engine remain pure compute and lets the storage choice be a config flag, not an architectural fork.

---

## ADR-009: Two batch config types — split, not flagged

**Decided:** Provide `PortfolioBatchConfig` and `PerSymbolBatchConfig` as distinct types rather than a single `BatchConfig` with a `mode` flag.

**Alternatives:** One config class with `mode="portfolio"` / `mode="per_symbol"`.

**Why:** "Universe" means semantically different things in the two modes. In portfolio mode, universe = "the set of stocks the portfolio can hold simultaneously." In per-symbol mode, universe = "the set of stocks to iterate over, each evaluated independently." Trying to unify them with a flag forces every downstream parameter (`max_positions`, `max_position_pct`, etc.) to be defined in a way that makes sense under both interpretations, creating redundant cross-coupled validation. Splitting at the config layer keeps each type's parameters clean. Note: the **execution engine is unified** — both configs produce the same flat list of tasks for `BatchRunner`, which has no notion of mode.

---

## ADR-010: Per-strategy param specs (mixed shapes allowed in one batch)

**Decided:** Each strategy entry in a batch carries its own param spec. Strategy A can have a `param_grid` while Strategy B has a `param_list` in the same batch.

**Alternatives:** One global param spec per batch.

**Why:** Different strategies have different parameter spaces and different sweep patterns. Forcing a uniform shape across strategies would either constrain expressiveness or require multiple batches for what is conceptually one experiment. Per-strategy specs let one batch span heterogeneous strategy comparisons.

---

## ADR-011: Per-symbol mode uses $100k per independent run

**Decided:** In `PerSymbolBatchConfig`, each per-symbol run starts with its own full $100k of capital (or whatever `initial_cash` is set to).

**Alternatives:** Split $100k total evenly across symbols (e.g., $1k per symbol for 100 symbols).

**Why:** The purpose of per-symbol mode is to evaluate strategy quality on each symbol independently — apples-to-apples. Starting each run with the same capital makes per-symbol metrics directly comparable. Splitting capital simulates a different scenario (allocating one pot across many independent strategies), which is a valid use case but not the one this mode targets. A future "split capital" mode can be added if needed.

---

## ADR-012: Logging across processes via QueueHandler + QueueListener

**Decided:** Worker processes use `logging.handlers.QueueHandler` writing to a `multiprocessing.Queue`. The main process runs a `QueueListener` thread that drains the queue and writes serially to stdout + `errors.log`.

**Alternatives:** Per-worker stdout (interleaved garbage); per-worker log files (fragmented, hard to read).

**Why:** This is the canonical Python pattern for multi-process logging. Workers can't safely write to stdout directly without interleaving. A central queue plus a draining listener thread serializes output. Errors stream to the terminal in real time without stopping the batch (per requirement). The same channel persists structured errors to `errors.log` for post-run inspection.

---

## ADR-013: `run_id` includes the full portfolio config (supersedes ADR-004's field list)

**Decided:** `compute_run_id` hashes `portfolio_config` in full, alongside the ADR-004 fields.

**Alternatives:** Whitelist only the five keys `Engine` reads; leave portfolio config out of the hash entirely.

**Why:** `initial_cash`, `max_positions`, `max_position_pct`, and `min_trade_size` all change the resulting metrics, so two batches differing only in those fields were producing identical `run_id`s — meaning an opt-in resume could silently return results computed under different capital constraints. Hashing the whole dict rather than a whitelist accepts spurious cache misses (cost: one recomputation) to eliminate spurious hits (cost: silently wrong numbers). A whitelist would also need manual sync with `Engine`'s reads, which is exactly the drift ADR-004's VERSION discipline already asks a human to manage once.

---

## ADR-014 · FUTURE: Progressive disclosure in the batch-configuration UI

**Status:** FUTURE — recorded inspiration, not scheduled. No code depends on it, and nothing in the current architecture blocks it.

**Proposed:** When a user-facing front end is eventually built over the batch layer, configuration should be presented as a wizard — one decision per screen, each selection constraining and revealing the next — rather than as one flat form exposing every option simultaneously. Concretely: choose the universe; then choose strategies, now filtered to those valid for that universe; then supply parameters, now displayed with the chosen strategy's actual parameter names, defaults, and bounds.

**Alternatives:** A single flat configuration surface listing universe, strategy classes, and parameter fields at once — which is what a direct rendering of `PerSymbolBatchConfig`'s dataclass fields would produce.

**Why:** A flat surface offers no selection-triggered constraint. Nothing narrows the strategy list once a universe is chosen, and nothing tells the user which parameters the chosen strategy actually accepts — so a `param_grid` key the strategy ignores silently produces N distinct runs with identical results, which is a real property of `_expand_params` today. Staging the decisions makes each choice's valid domain a function of the previous choice, which is precisely where a constraint can be both enforced and displayed. The governing idea is attention: at any moment the user should be looking at one decision, not twenty.

**Note — this is a display concern, not an enumeration concern.** It was first suspected to be an architecture problem: that `build_tasks` should nest `universe → strategy → params` as dependent levels rather than taking a flat product over symbols. That was investigated and rejected. The flat product is correct for the ranking experiment `run_ranking.py` performs, and the uniformity it enforces is what makes per-symbol metrics comparable (ADR-011). `strategy → params` is already nested (ADR-010); only the symbol dimension is a flat product, and deliberately so. A future reader should not "fix" `build_tasks` on the strength of this entry. The wizard's step order happening to mirror the enumeration nesting is why the idea is recorded here rather than discarded.

---

## ADR-015 · OPEN: Process start method — fork vs spawn

**Status:** OPEN — provisional default is **spawn**, pinned explicitly and selectable. Not yet measured.

**Provisional:** `BatchRunner` resolves the start method to an explicit `multiprocessing` context and passes it to both `ProcessPoolExecutor(mp_context=...)` and `Manager()`, so macOS and Windows exercise the same code path. The method is selectable via `config.MP_START_METHOD` / `WISETRADE_MP_START_METHOD`, defaulting to `"spawn"`; an unavailable value falls back to spawn with a warning rather than raising.

*Mechanism refined during implementation (this entry is provisional by status, so amended in place rather than superseded): an explicit context is used instead of `set_start_method(force=True)`. Same decision, better mechanism — the choice stays local to `BatchRunner`, so no global interpreter state is mutated, nothing else in the process is affected, and no other caller's start method is silently overridden or able to silently override ours.*

**Selectable because the question cannot otherwise be settled.** ADR-015 stays OPEN on the strength of an unmeasured startup-cost claim; exposing the knob is what makes measuring it possible. `fork` is therefore available but warns on selection, and is not a neutral peer: `_start_logging()` starts the `QueueListener` thread *before* `_execute()` builds the pool, so the parent is provably multi-threaded at fork time — which Python 3.12 warns risks deadlock, and which motivated Python 3.14 moving Linux off fork-by-default. (One hazard is dodged by construction: feeds are created inside `run_one`, so no SQLite connection exists to be inherited.)

**A third candidate, noted during implementation: `forkserver`.** It may dominate both. A fork server process is created early, stays single-threaded, and all workers are forked from *it* — so it keeps fork's cheap startup while structurally avoiding the multi-threaded-parent hazard that makes plain `fork` unsafe here. POSIX-only, so it cannot be the cross-platform default, but it belongs in the measurement alongside the other two.

**Alternatives:** `fork` (POSIX only); leave it unset and inherit whatever each platform defaults to.

**Why:** The two differ in what a worker inherits. `fork` copies the parent's address space copy-on-write, so workers start fast and get already-imported modules, open file descriptors, and module-level state for free. `spawn` starts a fresh interpreter that re-imports everything and receives only what is explicitly pickled through `initargs`. That makes `fork` meaningfully cheaper to start — no per-worker re-import of pandas/numpy — but it also inherits locks and descriptors that were never designed to be duplicated: an inherited SQLite connection or a held logging lock is a classic source of deadlock and silent corruption, and CPython now warns that forking a multi-threaded process is unsafe — our `QueueListener` *is* a thread. `spawn`'s isolation is total, which is what ADR-002's supervisor rationale actually depends on. Windows has no `fork` at all, so `spawn` is the only shape that runs unchanged on both machines.

**Why this stays OPEN:** `fork`'s startup advantage is real and grows with import weight. Amortized across ~200 multi-minute backtests it is noise, but a large grid sweep of short runs would feel it, and a future read-only shared bar cache is something `fork` would give for free and `spawn` cannot. None of this has been measured. The default is `spawn` because it is the only choice correct on both platforms today, and it is pinned *explicitly* rather than left to the platform default specifically so that a fork-vs-spawn bug can never present as a silent difference between the Windows box and the Mac.

**Revisit when:** worker startup is measured as a significant fraction of batch wall time, or a shared-memory structure appears that `fork` would make free.

---

## ADR-016 · OPEN: Live and streaming curve recording

**Status:** OPEN — nothing streaming is implemented or scheduled. The interface gate described below is the agreed next implementation step but is **not yet in place**; today the worker still reads `analyzer.portfolio._equity_history` directly.

**Provisional:** Keep the full equity history in memory exactly as now, and keep the end-of-run Parquet write as the durable artifact. Add no streaming implementation. Preserve the option by making the recorder be invoked from *inside* the event loop rather than after it — that call site, not the abstract base class, is the part that cannot be retrofitted cheaply.

**Alternatives:** Implement streaming Parquet now for bounded memory; implement a live-tail sink now for real-time plotting; add nothing at all and accept that a future need forces a rewrite of `Portfolio` and `Engine`.

**Why:** Two different motivations have been travelling under the single word "streaming", and they have opposite requirements. Bounded memory wants large infrequent flushes and needs to *stop* retaining rows; live plotting wants small frequent flushes and must *keep* retaining them, because `Analyzer` reads the full series in six places to compute its metrics. Neither is needed now — this is backtesting, not live trading, and one year of single-symbol 1-minute bars is ~98k rows, roughly 4 MB, so memory is not a constraint at current scale. ADR-008's stated OOM motivation is therefore speculative; the live-plotting motivation is the real one, and it only becomes real for a strategy expensive enough that a single run takes minutes.

**Recorded so it is not rediscovered:** Parquet is the wrong format for a live-tail path. ADR-007 chose it precisely *because* the footer is written only on close, making a partial file detectably invalid — which is exactly backwards for tailing, since a reader can see nothing until the run has already finished. A live path therefore needs a second, different sink (JSONL tail, append-only ring buffer, socket) alongside the Parquet commit, not another subclass of the same recorder. It should also downsample on the way out: pushing all 98k points would exceed what any chart can render *and* add per-tick cost to the very run being watched.

**Deliberately deferred:** whether an in-memory recorder owns the equity list, or `Portfolio` keeps owning `_equity_history` with the recorder as a pure side-channel. Side-channel is the smaller change and leaves `Analyzer` untouched; ownership is the cleaner shape and is what would eventually make bounded memory reachable. Also deferred, and the harder question: *what to actually display*, given that a 200-run batch cannot plot every curve.

**Revisit when:** a single run is slow enough that watching it has value, or per-symbol curves make memory a real constraint.

---

## ADR-017 · FUTURE: Source fingerprint as a resume tripwire

**Status:** FUTURE — the idea is agreed; the scope question below cannot be answered while module boundaries are still moving.

**Proposed:** Stamp each committed run's JSON with a fingerprint of the code that produced it — `sha256` over the strategy class source plus the modules it calls. The fingerprint is *not* part of `run_id`. On resume, compare it against the committed run's recorded value and warn loudly (or refuse) on mismatch instead of silently skipping.

**Alternatives:** Fold a source hash into `run_id` itself (rejected by ADR-004, correctly); rely on VERSION discipline alone (status quo).

**Why:** ADR-004's VERSION discipline has two weaknesses. First, its failure is asymmetric and silent: forgetting to bump silently reuses results produced by code that no longer exists, while bumping unnecessarily costs one recomputation — so the only safe bias is over-bumping, which humans do not reliably do. Second, it has a transitive hole it does not mention: `calculate_sma` in `indicators.py` is shared by six strategies, so editing it changes every strategy's behavior while no strategy's `VERSION` changes. A fingerprint closes both. The insight that makes it viable where ADR-004's rejection still stands: source hashing is too brittle to serve as an *identity*, but brittleness is harmless in a *tripwire* — it does not need to be stable, only different. A false positive costs one recomputation; that is the same cheap direction ADR-013 chose.

**Why deferred:** The unanswered question is coverage. Strategy class only? Plus `indicators.py`? Plus `Portfolio` — whose fill and sizing logic also changes results? Plus `Engine`? Each addition catches more and produces more false positives, and the right boundary depends on a module structure that is still changing. Note also that ADR-005's fix (making resume genuinely opt-in) already shrinks the exposure window to the moment a user explicitly passes `resume_dir`, which is what makes deferring acceptable.

**Revisit when:** module boundaries have stabilised, or a stale-resume incident actually occurs.

---

## ADR-018 · FUTURE: Parameter validation and degenerate-run detection

**Status:** FUTURE — mechanism needs design. One interim measure taken instead: the history length is exposed as a single named constant in `config.py` so the number is visible rather than buried as a literal.

**Proposed:** Two layers. (1) Static — each strategy declares which of its parameters are measured in bars, and task enumeration rejects any combination exceeding the history length *before* launching, printing the conflict rather than producing a plausible-looking zero row. (2) Runtime — detect that an indicator was `None` for 100% of ticks and report the run as degenerate rather than `ok`.

**Alternatives:** No validation (status quo); runtime detection only; static rejection only.

**Why — the mechanism worth recording, because no component here is buggy:** with `max_lookback=300` and `slow_period=400`, four independent silencing points compose into a silent false result. (1) `self.history[symbol]` is a `deque(maxlen=300)`; at capacity `append` discards from the left with no return value, exception, or counter — it is *designed* to be quiet. (2) `get_closes()` has no `required` argument, so it cannot detect that 400 were wanted. (3) `calculate_sma` returns `None` when `len(prices) < period` — but that is the **same** `None` returned during legitimate warm-up, so "not warmed up yet" and "can never warm up" are indistinguishable by construction. This is the root cause. (4) `next()` does `continue`, and an empty signals dict is the normal state on most ticks, so `Portfolio` cannot complain either. The run reports `status="ok"`, `num_trades=0`, `total_return_pct=0.0`, and a *non-zero* `bar_count` — so it looks like it ran. The information "400 > 300" exists for one instant, at the `calculate_sma` call, where the only available vocabulary is a value already spoken for.

**Why deferred:** The static layer's placement is clear (task-build time is the earliest point where both numbers are known simultaneously), but the runtime layer is not: where the check lives, what status it reports, and how it avoids firing on strategies that are legitimately quiet all need design. Neither blocks the current architecture.

---

## ADR-019: `TradingCalendar` owns all time-derived quantities; sessions are derived from data and validated against a package

**Decided:** Introduce a `TradingCalendar` that is the single source for every time-derived number in the system. Its session table is **derived from the observed bar timestamps**, with an exchange-calendar package (`exchange_calendars` / `pandas_market_calendars`) used to *validate* that derivation rather than to produce it. It is injected into strategies as an attribute, exactly as `Portfolio` already is, so `Strategy.next()`'s signature does not change.

**Alternatives:** Calendar-as-truth, where the simulation clock iterates sessions the package emits (zipline's model); hardcoded holiday and session-hour constants; leave time arithmetic inside individual strategies (status quo).

**Why the dependency is inverted:** A package's trust decomposes into four layers of very different reliability — UTC/DST conversion (IANA tzdata, authoritative), holiday rules (stable convention), ad-hoc closures (knowable only retrospectively), and **regular session hours (constants in the package class, only as current as the last release)**. That last layer is the exposure: a schedule change such as an exchange extending its trading day requires a maintainer to notice and ship. But for backtesting the bars *are* ground truth about when the market was open — no bars on a date means it was closed; bars ending early mean a half day. Deriving from data therefore removes any dependence on the package being correct about the past, adapts automatically to schedule changes, and repurposes the package as a **data-quality checker** the project currently lacks: when the package says "trading day" and the data has no bars, that is a missing-download bug, not a holiday, and it is presently undetectable. Zipline is the cautionary case for the opposite choice. The backtest/live asymmetry is the underlying reason: backtesting looks backward, where data exists and can be truth; live trading looks forward, where tomorrow's bars do not exist yet and a calendar *must* be trusted. Same code, opposite trust direction.

**Scope:** Replaces `SMA_OS_Dynamic._minutes_to_close()` entirely — that method hardcodes `hour=21` (wrong for half the year), has no early-close awareness (on a 13:00 ET half day it believes three extra hours remain), and carries an `except ValueError: return 60` fallback that would silently report "60 minutes to close" on every bar forever if the hour parameter were ever swept out of range. Also supplies a measured `periods_per_year()`, replacing `analyzer.py`'s hardcoded `252 * 390`.

**Named `TradingCalendar`, not `SessionClock`:** once it owns session bounds, annualization factors, and the data cross-check, "clock" understates it; `TradingCalendar` also matches backtrader's vocabulary, so it reads as familiar rather than bespoke.

**Corollary — a classification rule that belongs in `CLAUDE.md`, not here:** bar counts are not uniformly wrong. A count used as a *proxy for a duration* is (`window_n = 390  # 1 trading day` breaks on half days, gaps, and extended hours). A count that genuinely *is* a count is not (`slow_period = 20` — an SMA is defined in observations, and "20 minutes" would be wrong). Conversion factors between the two (`252`, `390`, `365.25`) should be measured from the data, never assumed.

---

## ADR-020: Regular trading hours only, as a switch, defaulted on

**Decided:** Bar filtering to regular trading hours is a switch, defaulted **on**. Extended-hours and overnight bars are excluded from backtests unless explicitly enabled.

**Alternatives:** Always include extended hours (status quo — roughly half of raw DB bars); always exclude with no switch; make it a per-strategy setting.

**Why:** The two regimes have different data quality, not just different hours. Regular-session bars are dense and near-complete — approximately 390 one-minute bars per session, with gaps rare. Extended and overnight bars are sparse: liquidity collapses and there are long stretches represented by a single bar. `TimeAlignedIterator` forward-fills across gaps, but that fill logic has not been validated against real or mock trading, so including extended hours means compounding an unvalidated fill with the data most likely to stress it. Defaulting on restricts backtests to the only regime currently trusted. It is a switch rather than a deletion because extended-hours behavior is a legitimate future research target and the bars are already in the database.

**Why this is one decision with ADR-019 rather than a separate data-filtering choice:** because `periods_per_year()` is *measured* rather than assumed, flipping the switch keeps annualization self-consistent automatically. Under the old hardcoded `252 * 390`, enabling extended hours would have understated the annualization factor by roughly 1.6x and silently mis-scaled `volatility_annualized_pct` and `sharpe` — the latter being a column the ranking output is sorted on. (Mitigating detail, worth knowing: that error is a uniform multiplier across a batch, so ranking *order* survives and only absolute values are meaningless.)

**Consequence requiring action — this is ADR-013's problem, found again:** the RTH flag changes results but is not currently an input to `compute_run_id`. Two batches differing only in this switch would collide on `run_id`, and an opt-in resume could return extended-hours results for a regular-hours request. The flag must enter the hash when the switch is implemented.

---

## ADR-021: Overwriting is the exposed switch; reusing committed results is the default (supersedes ADR-005)

**Decided:** `BatchRunner.run()` takes `overwrite: bool = False`. By default only tasks with no committed result are submitted. `overwrite=True` discards and recomputes everything regardless of what is committed, and is logged at WARNING so the action leaves a durable trace in `errors.log`. The `resume` parameter is removed.

**Alternatives:** ADR-005's scheme (`resume: bool = False`, defaulting to full re-execution); keep `resume` and add `overwrite` beside it; expose neither and always reuse.

**Why — two mechanisms were sharing one flag.** Deciding that a committed result is no longer valid has two independent sources. *Automatic invalidation* is the program's job: comparing declared inputs it can hash and rejecting individual stale tasks (ADR-022). *Human override* is the operator's: they know things the hash cannot reach — that the bar database was re-adjusted, that a shared indicator function was edited — and issue a global order. The two differ in who decides, in granularity (per task versus whole batch), and in basis (hashable inputs versus outside knowledge). One boolean cannot express "reuse the 188 the program vouches for, re-run the 12 it flagged" while also carrying "ignore all of that, redo everything." Separating them keeps each mechanism's meaning intact as the automatic layer grows.

**Why the default inverts.** Under ADR-005 the destructive behavior *was* the default, wearing a name that did not sound destructive: `resume=False` meant "re-run and overwrite," so pointing `BatchRunner` at an existing batch directory with default arguments erased it. ADR-005's stated worry — silently reusing results produced by code that has since changed — is real, but it was aimed at the wrong control. That is properly the automatic layer's job (ADR-022), not something worth buying by making destruction the default. And the two risks only ever meet when a caller *deliberately names an existing directory*, at which point "continue this batch" is overwhelmingly the intended meaning; nobody names an existing batch directory intending to wipe it. Making reuse the default also stops "resume" being a mode at all: it becomes simply what running a batch means, which is the honest description — you never want to redo work you already have unless you have a reason, and having a reason is exactly what `overwrite=True` states.

**Corollary:** the config-layer asymmetry this replaces — `resume` derived from `resume_dir`, which left "existing directory, re-run everything" inexpressible — dissolves rather than needing a fix. That combination is now the default plus one explicit flag.

---

## ADR-022 · OPEN: Automatic staleness detection — result-affecting inputs outside the hash

**Status:** OPEN — the pattern is identified and three instances are known; no detection mechanism is implemented. ADR-021's human override is the interim mitigation.

**Problem:** `compute_run_id` hashes strategy identity, `VERSION`, params, universe, time range, and — since ADR-013 — portfolio config. But results also depend on inputs that are *not* hashed, so two runs can share a `run_id` while having been computed under materially different conditions, and reuse then returns the older one silently.

Three instances, all found by the same reasoning:

1. **Portfolio configuration** — capital and position constraints change the metrics. Resolved by ADR-013.
2. **The regular-trading-hours filter** — changes which bars exist at all. Identified in ADR-020; not yet in the hash.
3. **The bar database contents** — re-running `utils/adjust_database_sql.py`, or re-ingesting CSVs, invalidates every committed result while every hashed field stays identical. Unlike the other two, `VERSION` discipline offers no mitigation whatsoever here: the strategy code did not change, so there is nothing a human could correctly bump.

**The shape of the pattern, which is the point of recording it:** `run_id` covers *strategy* inputs well and *environment* inputs badly. Strategy identity, params, universe, and time range are hashed; capital constraints only after ADR-013; bar filtering not yet; data contents not at all. Any newly introduced result-affecting input should be checked against the hash by reflex rather than discovered later.

**Candidate mechanisms, none decided:** stamp an explicit ingest/adjustment version into the database when `adjust_database_sql.py` writes it, and hash that — robust, and survives moving the file between machines; hash the database file's `(size, mtime)` — cheap, but mtime changes on a no-op copy and does not survive transfer; or record fingerprints in the run JSON *without* hashing them and compare on reuse, warning rather than diverging the ID — the same tripwire-not-identity distinction ADR-017 draws for source code, and for the same reason: a false positive costs one recomputation.

**Related:** ADR-017 is the *code* identity version of this problem. This entry is the *environment and data* version, and unlike ADR-017 it has no discipline-based fallback.

**Revisit when:** the RTH switch is implemented — instance 2 must be resolved alongside it — or a stale-data reuse actually occurs.

---

## ADR-023: Session derivation splits data-truth from package-truth at the intraday boundary (refines ADR-019 and ADR-020)

**Decided:** Which dates are trading sessions is derived from the bar data. The *intraday* regular-hours boundary is taken from `exchange_calendars` when it is installed, with a bar-density fallback when it is not. `exchange_calendars` becomes a declared dependency. Neither ADR-019 nor ADR-020 is overturned; this records what implementation established about both.

**Alternatives:** Derive the intraday boundary from bar density alone, which is what ADR-019 assumed; take the entire session set from the package, which ADR-019 rejected and still rejects.

**Why the session set is still data-derived, but ADR-019's test was wrong.** ADR-019 justified itself with "no bars on a date means the market was closed." That does not hold for this vendor. Files are bucketed by *UTC* date, so a session's post-20:00-ET tail lands in the next day's folder — which is why the raw tree contains Saturday folders holding Friday evening. The correction is narrower than a reversal: converting to exchange-local time makes the artifact vanish completely. Measured on the real database, 453,567 distinct timestamps collapse to **479 ET sessions**, every one starting 04:01 ET, with no weekend dates at all and 2.0% of timestamps changing date under the conversion. So the session set *is* reliably data-derived — but only after that conversion, which is why it now happens exactly once in `derive_sessions` and nothing downstream re-derives a session from a raw timestamp.

**Why the intraday boundary is the exception.** It is the one thing the data genuinely cannot resolve: extended-hours trade still prints after an early close, so deriving the regular close from bar density placed the four half-days in the window (2024-07-03, 2024-11-29, 2024-12-24, 2025-07-03) at 13:01–13:02 instead of 13:00. The package places them at exactly 13:00 with exactly 210 bars, and agrees with the derived data on every other session. That is a narrow and well-defined role — resolving a boundary the data blurs — without becoming the source of the session set, so the reasoning in ADR-019 about package staleness still stands for everything else. The density fallback is retained so a machine without the package still builds a correct database, accurate to a minute or two on early closes.

**Boundaries are half-open because bars are stamped `eob`.** The bar labelled 09:31 covers 09:30–09:31, so regular hours are `(09:30, 16:00]` — exactly 390 bars, confirmed on 475 of 479 sessions. Treating the interval as closed at both ends picks up a pre-open bar and drops the closing one.

**Two measured corrections to ADR-020.** First, the filter removes **20% of bars, not the ~59% implied**: regular hours are 41% of the distinct *time axis* but 80% of the *rows*, because extended-hours coverage is sparse — only liquid names trade then, so those minutes contribute few bars. Second, the annualisation defect is confirmed and quantified: `252 * 390` is within 0.5% of the measured 97,798 bars/year for regular hours, but understates the extended-hours figure of 238,747 by **2.44x**, mis-scaling annualised volatility by sqrt(2.44) ≈ 1.56 and every Sharpe with it. Because that error only becomes visible once the filter is switched off, the switch and the measured `periods_per_year` had to ship in the same change — which is what ADR-020 meant by treating them as one decision.

**Consequence already recorded elsewhere:** the regular-hours flag is a result-affecting input and is now part of `run_id`. It is the third such input found outside the hash, after portfolio config (ADR-013) and the bar data itself (ADR-022, still open).

---

## ADR-024 · FUTURE: Ticker changes fragment a symbol's history

**Status:** FUTURE — the problem is characterised and reproducible; no mechanism is chosen, and the obvious one is blocked on a reference source we do not have.

**Problem:** A company's minute data appears under different tickers at different times, so a universe entry is implicitly *time-dependent* while the code treats it as a constant string. Observed in the current data for Fiserv:

```
FI      2024-01-02 → 2025-10-31   478 sessions
(none)  2025-11-03 → 2025-11-10     6 sessions, neither ticker
FISV    2025-11-11 → 2025-11-26    13 sessions
```

Neither ticker spans the window. Backtesting `FI` over 2025-07-02 → 2025-11-25 silently covers 86 of 102 sessions and, worse, **ends early** — so its return is measured over roughly four months while every other symbol gets five. That makes the row non-comparable in exactly the ranking ADR-011 exists to make comparable, and `TECH_100` currently carries a single ticker per company with no notion of validity dates.

**Why it was found at all:** only because per-run session coverage is reported in the metrics. Before that, `FI` sat in the ranking table indistinguishable from 114 complete symbols. The one-day `GOOG` gap was known in advance; this one was not, and it is seventeen times larger.

**Blocked on:** there is no available source listing US ticker changes with effective dates. That is the gating dependency — the storage design is straightforward, the reference data is not.

**Candidate mechanisms, none decided.**

*An alias table.* `symbol_aliases(canonical_id, ticker, valid_from, valid_to)`, with the feed unioning across a canonical id's aliases. Structurally identical to `adj_factors` — an interval table resolving a time-varying attribute — so it would compose with what exists. Needs the reference data above.

*Detect candidates from the data.* A ticker whose history ends as another's begins is a candidate pair, and price continuity across the seam confirms it: FI closed 66.24 on 2025-10-31, FISV opened 63.00 on 2025-11-11, a ratio of 0.95 across a six-session gap. That is the same continuity test `validate_adjustments` already uses for splits, pointed at a different discontinuity. It cannot be trusted to rewrite a universe unattended, but it can propose pairs for a human to confirm, which converts an unbounded research problem into a short review list.

*Do nothing and rely on coverage reporting.* Accept per-ticker fragmentation, and let `session_coverage_pct` flag the affected rows so they can be excluded or read with care. This is the current behaviour, and it is honest — it is only insufficient once such a symbol is one you actually care about.

**Note the failure shape**, because it recurs: nothing errors. The ingest, the feed, the calendar and the batch all handle a missing symbol-day correctly and the run completes with `status="ok"`. What was missing was not error handling but *visibility*, and the same is true of the split conventions (ADR-023) and the un-hashed inputs (ADR-022).

