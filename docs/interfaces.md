# Interfaces

*Frozen payload contracts shared by the parallel feature branches. This file is
**read-only to every branch**. A branch that finds a contract wrong stops and
raises it rather than editing — another branch is already building against it.*

*Layer note (`collaboration-protocol.md` §4): this file holds **contracts**, not
decisions and not state. Rationale for a contract goes in `decisions.md`; what is
currently implemented lives in the code.*

---

## Ownership

| Branch | Owns | Consumes |
|---|---|---|
| `feat/bar-service` | `Bar`, period vocabulary | — |
| `feat/label-store` | `Label` | — |
| `feat/chart-ui` | nothing others need | `Bar`, `Label` |

`EpisodeWindow` is reserved here but implemented later, on a branch taken off
`feat/chart-ui` after it merges. Its shape is fixed now so that neither the bar
service nor the label store has to change to accommodate it.

Each producing branch ships `scripts/dump_fixtures.py` early, writing these
payloads as JSON. `feat/chart-ui` develops against those fixtures, not against a
running service — an agent building against an imagined API cannot detect that it
imagined it.

---

## Period vocabulary

```
"1m" "2m" "5m" "15m" "30m" "1h" "2h" "1d"
```

The period is an explicit user choice. **Zooming never changes it.** Auto-switching
resolution under a zoom would make a pattern change shape as the user inspects it,
which corrupts any judgment recorded against it — fatal for a labelling tool, and
the reason the resolution ladder is a *pagination* mechanism instead.

Buckets are **session-aligned, never epoch-aligned**. Epoch alignment happens to
work below 30m (09:30 ET is minute 570, divisible by 5, 15 and 30) and breaks
above it: a clock-aligned hour produces a 09:30–10:00 stub before settling onto
the hour, and a UTC calendar day splits an extended-hours session across midnight.
Alignment therefore reads `sessions.rth_open_ms`, and `"1d"` means exactly one
session, not a calendar day.

---

## `Bar`

Emitted by `database/resample.py:aggregate_bars`, one per bucket, ascending by
`t_ms`.

```jsonc
{
  "t_ms":       1751414400000,  // bucket END (eob), Unix ms UTC
  "open":       191.23,
  "high":       191.88,
  "low":        190.95,
  "close":      191.40,
  "volume":     1284300.0,      // float: adjusted volume is volume * cum_factor
  "n_src":      5,              // 1-minute bars actually present in this bucket
  "n_expected": 5               // 1-minute bars the bucket should contain, or null
}
```

**`t_ms` is milliseconds and is named so on purpose.** lightweight-charts takes a
`UTCTimestamp` in **seconds**; a field named `time` would be passed straight
through and every bar would land in 1970. The conversion must be visible at the
call site. (This is the same failure that put an entire ingest run in Jan 1970 —
see ADR-023.)

Bars are **split-adjusted**, aggregated from `bars_adjusted`. There is no
unadjusted path.

**`n_expected` is theoretical, not observed.** It is the bucket's span in minutes:
`(bucket_end - max(rth_open_ms, bucket_end - period)) / 60000` — 390 for a full
RTH day, 210 for a half day, truncated for the last bucket of a session. It must
not be read from `sessions.n_bars_rth`, which is an ingest-time count and already
reflects gaps; using it would make `n_src == n_expected` always and silently
disable the completeness check.

**`n_expected` is `null` when `regular_hours_only=false`.** Extended-hours coverage
is genuinely sparse and varies by orders of magnitude between names (797 bars vs 15
on the same day), so no theoretical count exists and flagging incompleteness there
would be noise.

`n_src < n_expected` means the bucket was built from incomplete data. **The service
never decides what to do about it.** The chart may render such a bar with a distinct
outline; a backtest consuming 1h bars must treat a bucket built from 12 of 60
minutes as a different object. Fixing a policy at the data layer would force one
answer on both.

---

## `Label`

Owned by `database/label_store.py`. The schema is deliberately open: a label is a row
with a type and a JSON payload, not a boolean column. New label types are added
without a migration.

```jsonc
{
  "id":            1,
  "symbol":        "AAPL",
  "anchor":        "point",      // "point" | "interval"
  "t_from_ms":     1751414400000,
  "t_to_ms":       null,         // null when anchor == "point"
  "period":        "5m",
  "rth":           true,         // the /bars `rth` the label was made on; required
  "label_type":    "setup",      // open vocabulary
  "payload":       {},           // open; type-specific
  "source":        "human",      // "human" | "model"
  "created_at_ms": 1757462400000,
  // Derived by the store; read-only, ignored if sent.
  "cover_from_ms": 1751414100000, // exclusive: clamped start of the first bucket
  "cover_to_ms":   1751414400000, // inclusive: end of the last bucket
  "within_rth":    true           // every covered minute is a regular-hours minute
}
```

**`t_from_ms` / `t_to_ms` must be the bar's own `datetime` value as read from the
database.** Never a timestamp reconstructed from a pixel x-coordinate. The click
path is: chart yields a logical index → look up that bar in the loaded window →
store *that bar's* timestamp. A coordinate-derived timestamp that is off by one bar
or one timezone renders correctly and silently attaches the label to the wrong bar,
poisoning the training set in a way that cannot be detected or reversed afterwards.

**`period` is part of the observation, not metadata.** A pattern judged on 5m bars
is not the same observation as one judged on 1m, and a model trained across both
without the distinction is learning noise.

**`rth` is part of the observation for the same reason.** Bucket alignment depends
on it: at `"1h"` the bar ending 10:00 covers (09:30, 10:00] on an RTH chart and
(09:00, 10:00] on an extended-hours chart, and `"1d"` is the regular session in one
case and the whole extended session in the other. Without `rth`, a stored
`(t_from_ms, period)` names two different bars and the one the user judged cannot
be recovered. It is required with no default: a default would silently stamp RTH
on a label made on an extended-hours chart. `GET /labels` accepts it as an
optional filter.

**Coverage is how a label crosses periods.** `t_from_ms` / `t_to_ms` are bucket
ends, so they do not say where a label begins: a `"1d"` point stamped 16:00 covers
the whole session. `(cover_from_ms, cover_to_ms]` is the 1-minute span the labelled
buckets occupy, computed with the same session-clamped `bucket_span` that `/bars`
uses. The single projection rule for drawing a label on a chart of any period: a bar
is marked when its own bucket intersects the coverage. `GET /labels` filters on
coverage, not on the stamps.

**`within_rth` decides visibility in a regular-hours view, and hides rather than
clips.** It is true for every `rth: true` label, and for an `rth: false` label only
when its whole coverage lies inside one session's regular hours. A label whose
coverage reaches into extended hours is not shown on an RTH chart at all: a pattern
judged with the pre-market in view is not the same observation once it is cut off.

A stamp that is not a bucket end on its session's grid for the label's `period` and
`rth` is rejected with 422, never snapped.

**In replay, the chart hides what has not happened yet.** `GET /labels` returns every
label whose coverage overlaps the window, so an interval that began before the replay
cursor comes back with its full `t_to_ms` and `cover_to_ms` -- where the judged
pattern ends. A replaying chart must draw such a label only up to the cursor and must
not reveal its end, its `payload` or its reviews until the cursor reaches `cover_to_ms`.
The store does not trim: it serves the record, and the cursor is a property of the view.

**`source` exists for the active-learning loop**: human labels are ground truth,
model labels are proposals awaiting review. They must never be pooled.

---

## `EpisodeWindow` *(reserved — not implemented yet)*

For the comparison view: many episodes aligned on entry, drawn on a
relative axis. Derived from `core/episodes.py:PositionEpisode`.

```jsonc
{
  "run_id":       "a1b2c3…",
  "symbol":       "AAPL",
  "opened_at_ms": 1751414400000,
  "closed_at_ms": 1751500800000,
  "realized_pnl": 412.55,
  "bars": [
    { "k": -60, "open": 190.1, "high": 190.4, "low": 189.9, "close": 190.2, "volume": 21400.0 }
    // k = bar index relative to entry; k <= -1 before, k = 0 the entry bar, k >= 1 after
  ]
}
```

**The x axis is `k`, not wall-clock time.** This is why the chart layer must accept
a logical index as its axis — the constraint that decided lightweight-charts over
KLineChart (ADR to be filed on `feat/chart-ui`).

**Price normalisation is not in the payload.** Rebasing to entry = 100 is a view
choice and belongs in the renderer; baking it in would make the raw levels
unrecoverable.

---

## Transport

One HTTP service, `api/`, run with `uvicorn api.app:app --port 8000`. Contracts
above are the response bodies; the route signatures in `api/routers/*.py` are part
of the same freeze.

```
api/app.py  ──┬──► routers/bars.py     GET /symbols /sessions /bars /bars/count
              └──► routers/labels.py   GET|POST /labels, PATCH|DELETE /labels/{id}
```

`app.py` and both router files are created on `main` **before** the branches fork,
as stubs returning 501. Each branch fills exactly one router, so no two branches
edit the same file and the merge has nothing textual to resolve. Adding a route
means adding it to your own router, never to `app.py`.

A static JSON dump was the alternative and cannot serve two core cases: browsing
needs a live query to page in history as the user pans left, and replay needs the
range itself to be truncated. Static dumps remain the fixture mechanism, which is
the role they already have above.

**The no-future guarantee for replay lives here, in the `t1_ms` bound** — not in
the client declining to draw bars it already holds. A client holding the future in
memory eventually leaks it, through a tooltip, a rescale, or an autocomplete, and
a leak of that kind is invisible in the resulting labels.

Routers are split **per domain, not per consumer**: a second consumer of `/bars`
must not require a second endpoint. This is the first service rather than a chart
backend, and the directory shape is chosen for that.

Loopback-only CORS, no authentication. The service reads a local database and must
not be reachable off-host.

---

## Rules for branches

1. **Do not edit this file.** Raise the problem instead.
2. **Do not edit `CLAUDE.md`.** It is reconciled after the merges.
3. **Append to `docs/decisions.md` only, using the pre-allocated numbers.** Never
   renumber an existing entry.
4. **Do not edit `scripts/build_database.py`.** `feat/label-store` creates its table
   lazily rather than touching the shared build pipeline.
5. **Add config constants at the end of `config.py`**, never in the middle.

### Pre-allocated ADR numbers

| Branch | Range |
|---|---|
| `feat/bar-service` | ADR-025, ADR-026 |
| `feat/label-store` | ADR-027, ADR-028 |
| `feat/chart-ui` | ADR-029 – ADR-031 |
