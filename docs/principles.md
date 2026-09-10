# Working Principles

*A living doc. Update when a lesson has to be re-learned the hard way.*

*Companion files: `collaboration-protocol.md` — operational rules for working with Claude. `CLAUDE.md` — the subset of these principles that CC is expected to act on. This file is about how I think; those files are about how the work runs.*

---

## How I learn and think

- **Why before how.** I don't trust a technique I can't explain mechanistically, and I learn tools by understanding what they're doing rather than by memorizing an API. The path: conceptual foundation → capability boundary → interface/contract. The cost of this disposition: I sometimes over-drill into fundamentals when the leverage is actually upstream.

- **One black box at a time.** If I'm using something I don't fully understand, the surrounding logic must be clean and verified. Never two unknowns compounding. *(Operational form in `CLAUDE.md`.)*

- **Failure cost × detectability = how deep to go.** Not everything deserves full understanding. If this breaks, how bad and how invisible? That determines drill depth. *(Operational form in `CLAUDE.md`.)*

- **Opportunistic learning trap.** When an unfamiliar concept appears incidentally during a task, the situation creates false urgency to learn it in place. This is not a real learning opportunity — it's a context without schema to attach to. Before drilling into an incidental concept, apply the entry filter:
    - Does it directly block task completion? → engage
    - Does it activate existing schema I can attach it to? → engage
    - Neither? → log it, move on. The pool is not a graveyard; it's a queue.

---

## Cognitive bandwidth conservation

This is the meta-principle that underlies most of my failure modes in AI-assisted work.

When I delegate a high-load task (coding, research synthesis) to a model, the bandwidth that gets freed up is not automatically available for higher-leverage work. It gets eaten back — by oversized responses I struggle to process, by jumps across decision points without grounding, by outputs I can't visually review. The eating is invisible because it disguises itself as productivity: long high-quality exchanges, fast-arriving code, fluent-looking progress.

The conservation law: **bandwidth saved by delegation must be explicitly protected, or it will be consumed by the delegation interface itself.**

This principle generates the concrete operational rules in `collaboration-protocol.md`. This file just names the underlying invariant.

The same principle applies beyond coding — to the knowledge-restructuring project, to any work where I outsource cognitive load to a fast counterpart. Watch for the same pattern there.

---

## NOTE: cost lives in the plan, not in the text

*Added after a 40x speedup on a query whose text barely changed. It is about SQL
because that is where I met it, but the shape is not specific to SQL.*

Two statements returning byte-identical results ran 167 ms and 4.2 ms. One
scanned a 479-row table once per bar; the other scanned it once in total.
Nothing in the SQL said which — only `EXPLAIN QUERY PLAN` did. Then a second
pair, `SELECT DISTINCT symbol` against `SELECT symbol ... GROUP BY symbol`,
measured 0.3 ms against 503 ms over the same 25.5M rows with *identical plan
output*.

- **The plan is the artifact under review, not the query.** Any time a large
  relation joins a small one, read the plan before trusting the text.

- **The plan is necessary and not sufficient.** That second pair is the warning:
  when the plans match and the clock does not, the clock wins. Measure the thing
  being shipped, never a proxy for it.

- **Prefer the lever that pins the plan over the one that hopes for it.** A hint
  the engine is obliged to honour beats a rewrite that happens to produce a good
  plan on today's statistics. The good plan is not the property worth having —
  the guarantee is.

- **This is `failure cost × detectability` with the usual intuition inverted.**
  The catastrophic plan is loud: 42 minutes files its own bug report. The merely
  bad plan is silent — 167 ms feels fine, ships, and becomes the floor that
  everything above it is built on. The dangerous case is not the disaster, it is
  the one that is only slightly too slow.

---

*Last reviewed: August 9, 2026.*
