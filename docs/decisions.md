# Architectural Decisions

One paragraph per decision: **what** was decided, **alternatives** considered, **why** this one. Append-only; if a decision is overturned, add a new entry that supersedes it rather than editing the old one.

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
