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

*Last reviewed: August 9, 2026.*
