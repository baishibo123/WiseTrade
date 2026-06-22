# Working Principles

*A living doc. Update when a hard-won lesson gets overwritten under pressure.*

*Companion file: `collaboration-protocol.md` — operational rules for working with Claude. This file is about how I think; that file is about how we work.*

---

## How I learn and think

- **Why before how.** I don't trust a technique I can't explain mechanistically. This is a feature, not a bug — but it has a cost: I sometimes over-drill into fundamentals when the leverage is actually upstream.
- **Mechanism over syntax.** I learn tools by understanding what they're doing, not by memorizing their API. The Operational Confidence Model: conceptual foundation → capability boundary → interface/contract. ~3-4 hours to functional competency.
- **One black box at a time.** If I'm using a library I don't fully understand, the surrounding logic must be clean and verified. Never two unknowns compounding.
- **Failure cost × detectability = how deep to go.** Not everything deserves full understanding. The criterion is: if this breaks, how bad and how invisible? That determines drill depth.
- **Opportunistic learning trap.** When an unfamiliar concept appears incidentally during a task, the situation creates false urgency to learn it in place. This is not a real learning opportunity — it's a context without schema to attach to. Before drilling into an incidental concept, apply the entry filter:
    - Does it directly block task completion? → engage
    - Does it activate existing schema I can attach it to? → engage
    - Neither? → log it, move on. The pool is not a graveyard; it's a queue.

---

## Cognitive bandwidth conservation

This is the meta-principle that underlies most of my failure modes in AI-assisted work.

When I delegate a high-load task (coding, research synthesis) to a model, the bandwidth that gets freed up is not automatically available for higher-leverage work. It gets eaten back — by oversized responses I struggle to process, by jumps across decision points without grounding, by outputs I can't visually review. The eating is invisible because it disguises itself as productivity: long high-quality exchanges, fast-arriving code, fluent-looking progress.

The conservation law: **bandwidth saved by delegation must be explicitly protected, or it will be consumed by the delegation interface itself.**

This principle generates concrete operational rules (single-step granularity in architectural discussion, navigation-layer reporting from CC, recognizing the "skipping ahead" signal as a stop condition). Those rules live in `collaboration-protocol.md`. This file just names the underlying invariant.

The same principle likely applies beyond coding — to the knowledge-restructuring project, to any work where I outsource cognitive load to a fast counterpart. Watch for the same pattern there.

---

## Under pressure (family conflict, external doubt, anxiety spikes)

- Hard-won rational conclusions get overwritten by conditioned reactions under pressure. The principle book exists because of this.
- The trigger chain: external conflict → anxiety → avoidance. Recognize the chain before the third step.
- Physical intervention first. Reasoning during a spike is unreliable.
- Daily minimum task: one concrete thing that accumulates evidence for the path I've chosen.
- Consistency (连续一致性) is the primary signal I'm tracking in myself. Not output quality, not speed — consistency.

---

*Last reviewed: May 12, 2026.*
