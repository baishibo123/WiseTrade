# Working Principles


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

## How I build

- **Design and debate in chat. Build and refactor in CC.** Conceptual questions and architectural decisions belong in the chat interface where I stay close to the reasoning. Multi-file changes and implementation belong in Claude Code.
- **Specify the interface or specify the uncertainty.** Before any implementation request: either "here's the interface I want, build to it" or "I don't have a clear interface yet, give me a skeleton to react to." Both are valid. Ambiguity without flagging is the failure mode.
- **Verify logic against requirements. Trust my own debug ability.** I don't need to understand every line of generated code before it touches the codebase. I need to verify the logic matches the requirement. These are different tasks — mixing them degrades both.
- **CLAUDE.md captures invariants, not current state.** Conventions, contracts, and things that must never change. Not "the portfolio currently does X." The code holds current state; CLAUDE.md holds the rules.
- **ADR for non-obvious decisions.** When a design choice is made that won't be obvious to a future reader (or a fresh session), write a one-paragraph record: what was decided, what alternatives were considered, why this one. File in `docs/decisions.md`.

---

## How I engage with AI tools

- **Don't mix task modes in a single session if the conceptual context is shifting.** Debug sessions stay debug sessions. Architectural questions get their own framing. The cost of a new conversation is lower than the cost of contaminated context.
- **Premature completion signals are false.** A satisfying LLM exchange is not progress. The unit of progress is a requirement met in the actual system.
- **Output-based progress metrics are unreliable in AI-assisted workflows.** Lines of code and visible artifacts don't map cleanly to advancement. Use requirement coverage and working test runs instead.
- **Ask for quality ratings on questions.** Not every question deserves deep investment. Explicitly asking "how important is this?" is a legitimate and efficient use of the tool.

---

*Last reviewed: May 5 2026*