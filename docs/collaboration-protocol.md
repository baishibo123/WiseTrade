# Collaboration Protocol

*Operational. About how Claude and I collaborate. Read by Claude (chat and CC) and by me. First-person voice: "I" is Shibo; "the model" is Claude in either chat or CC.*

*Companion file to `principles.md`. The principles describe how I think; this protocol describes how we work together.*

---

## 1. Purpose and grounding

This protocol operationalizes one meta-principle from `principles.md`: **cognitive bandwidth conservation**.

The premise: delegating coding to CC was supposed to free up my bandwidth for architecture and verification. In practice, that bandwidth gets eaten back — by oversized architectural responses, by jumps across decision points that haven't been grounded yet, and by code reports I can't visually review. The cost is invisible because it masquerades as "high-quality discussion" and "high-velocity output," both of which `principles.md` already flags as false signals.

Every rule below exists to make that invisible cost visible and bounded.

---

## 2. Architectural discussion

### 2.1 The minimal unit per step

Each step in an architectural discussion produces, at minimum:

- **Signatures** of the modules/classes/functions involved. Hypothetical is fine — they do not need to be runnable. Pseudocode types are fine.
- **IO**: one line per function describing input → output.
- **Annotation**: one sentence per function/module saying what it does.
- **Flow diagram**: a simple ASCII diagram (using `|`, `-`, `→`, `└─`, etc.) whose nodes are the function/module names just introduced.

The flow diagram has two required properties.

**Incremental.** Each step adds its new nodes to the previous step's diagram rather than redrawing the whole thing. This keeps context cost linear instead of quadratic.

**Bounded.** The units being designed in this step are enclosed in brackets; their immediate upstream and downstream neighbors appear outside the brackets as named nodes. The diagram always shows where the piece sits in the system, not only what it contains:

```
strategy_trigger → [ portfolio.on_signal → portfolio.rebalance ] → metrics_collector
```

Everything inside `[ ]` is what this step designs. Everything outside is existing code it has to join. The outer nodes are what fixes the responsibility of the piece and makes its seams explicit — without them, an internally coherent design can still be attached to the wrong place.

### 2.2 No real code in this phase

Implementation does not begin until the architectural discussion has produced a complete, incremental flow diagram covering the scope of the change. Hypothetical signatures are the deliverable of this phase; runnable code is the deliverable of the next.

---



## 3. Interaction pacing

CC can produce a ten-part design in a single reply. It should not. The reply I want contains **one subpart**, framed as a proposal, ending in a real question.

The shape of a turn:

- **One subsection of the plan** — the next one, not all of them.
- **Stated as a proposal, not a settled decision.** "Here's what I'd do for this piece, and why."
- **A closing question that expects an answer.** "Does this work, or what would you change?"
- **Then stop.** Do not continue into the next subpart before I've answered.

Holding the rest of the plan in reserve is the point. CC having the full picture is useful; delivering it all at once is exactly what eats the bandwidth the delegation was supposed to free. Convergence happens one subpart at a time: propose → I react → adjust → move on.

---

## 4. Documentation layering

Three layers, three homes. Never let layers leak into each other — especially never let state drift into invariants.

- **Decisions** (non-obvious choices that won't be obvious to a future reader) → `docs/decisions.md` as ADR entries. One paragraph each: what was decided, what alternatives were considered, why this one.
- **Invariants and conventions** (rules that hold across the project's life — naming, contracts, things that must never change) → `CLAUDE.md`.
- **Current state** (what the system actually does right now) → the code itself and git history. Never duplicate state into either of the above; it goes stale and misleads.

This protocol file itself, and `principles.md`, also live in `docs/`. They are higher-order than ADR: they govern *how* decisions get made and recorded, not which specific decisions were made.

---

## 5. Retroactive review of unverified code

Sometimes code exists that never went through Section 2 — CC wrote it before I had confirmed the architecture. It runs and looks correct, but I have no schema attached to it, so it cannot enter `main` as-is.

Keep that work on its branch and use git as the source of truth for what actually differs:

```
git log  main..feature/<branch>            # what the branch claims to add
git diff --stat main..feature/<branch>     # which files, how much
git diff main..feature/<branch> -- <file>  # the actual change, per file
```

Then understand it the way §2.1 describes: for each block, reconstruct signature + IO + annotation and place it in a bounded flow diagram. A block counts as verified once that reconstruction holds up against the diff. Cherry-pick verified blocks into `main` in order rather than merging the branch wholesale, so `main`'s history records what was confirmed.

Any `decisions.md` or ADR entries the branch carries are claimed intent, not established fact — check them against the code before migrating them to `main`.

---

*Last reviewed: August 9, 2026.*
