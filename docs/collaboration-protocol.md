# Collaboration Protocol

*Operational. About how Claude and I collaborate. Read by Claude (chat and CC) and by me. First-person voice: "I" is Shibo; "the model" is Claude in either chat or CC.*

*Companion file to `principles.md`. The principles describe how I think; this protocol describes how we work together.*

---

## 1. Purpose and grounding

This protocol operationalizes one meta-principle from `principles.md`: **cognitive bandwidth conservation**.

The premise: delegating coding to CC was supposed to free up my bandwidth for architecture and verification. In practice, that bandwidth gets eaten back — by oversized architectural responses, by jumps across decision points that haven't been grounded yet, and by code reports I can't visually review. The cost is invisible because it masquerades as "high-quality discussion" and "high-velocity output," both of which `principles.md` already flags as false signals.

Every rule below exists to make that invisible cost visible and bounded.

---

## 2. Mode separation

- **Design and debate → chat. Build and refactor → CC.** Conceptual questions and architectural decisions belong where I stay close to the reasoning. Multi-file changes and implementation belong where the tooling matches the task.
- **Debug sessions stay debug sessions.** If the conceptual context shifts mid-session (from debugging a specific bug to rethinking the architecture, say), open a new conversation. The cost of a new conversation is lower than the cost of contaminated context — once a session's framing is set, later turns inherit assumptions from earlier ones, and those assumptions are often the thing that needs to change.

---

## 3. Architectural discussion protocol

This is the section that addresses the root failure mode: architectural discussions whose single-step granularity is too coarse for my bandwidth, leaving the "foundation" of each subsequent step structurally unverified.

### 3.1 The minimal unit per step

Each step in an architectural discussion produces, at minimum:

- **Signatures** of the modules/classes/functions involved. Hypothetical is fine — they do not need to be runnable. Pseudocode types are fine.
- **IO**: one line per function describing input → output.
- **Annotation**: one sentence per function/module saying what it does.
- **Flow diagram**: a simple ASCII diagram (using `|`, `-`, `→`, `└─`, etc.) whose nodes are the function/module names just introduced.

The flow diagram is **incremental**: each step adds the new nodes to the previous step's diagram rather than redrawing the whole thing. This keeps context cost linear instead of quadratic.

### 3.2 The decomposition test (soft rule)

If the model cannot sketch signature + flow for the current step, that is a signal the step isn't decomposed enough. Stop and split before proceeding.

This is a **soft rule**, not a hard gate. Some genuinely simple decisions require several small helper functions in coordination, and forcing each helper through the full format produces formalism without value. When the model judges that the format is becoming over-engineering, it should flag this explicitly ("this step is small enough that the full format adds noise — proposing to merge it with the next step") rather than silently skipping or silently complying.

### 3.3 One decision point per step

A single step advances exactly one architectural decision. If explaining the step requires introducing two parallel concepts that don't reduce to one, that is itself the signal to split — the same signal as 3.2, surfacing earlier.

### 3.4 No real code in this phase

Implementation does not begin until the architectural discussion has produced a complete, incremental flow diagram covering the scope of the change. Hypothetical signatures are the deliverable of this phase; runnable code is the deliverable of the next.

---

## 4. Implementation handoff

Once the architectural discussion has produced an agreed-upon flow diagram, implementation moves to CC. The following rules govern the handoff.

- **Specify the interface or specify the uncertainty.** Before any implementation request: either "here is the interface, build to it" — which the architectural phase usually produces directly — or "I don't have a clear interface yet, give me a skeleton to react to." Both are valid. Ambiguity without flagging is the failure mode.
- **Walking skeleton first, then layer by layer.** Build the thinnest end-to-end path that actually runs on real data — one input, one output, no parallelism, no logging, no atomic writes. Verify it works on real data, then add layers one at a time. Real data plus real components surface mismatches against existing code that fake inputs cannot. Each subsequent layer adds exactly one failure mode, so when something breaks I know where it lives.
- **Verify logic against requirements, not line by line.** I do not need to understand every generated line before it touches the codebase. I need to verify the logic matches the requirement. These are different tasks; mixing them degrades both. This rule is what Section 5 is designed to make actually achievable.

---

## 5. CC reporting contract

This section addresses the second-order problem: even if the architectural phase produced a clean foundation, my ability to verify implementation depends on what CC reports back. The default CC behavior — show a rough summary, or dump a diff too long to read — fails Section 4's "verify logic against requirements" rule.

After any meaningful change, CC reports in **two layers**:

### 5.1 Navigation layer (always shown, comes first)

For each touched function or class:

- **Signature + return type**
- **One-line annotation** of what it does

Plus a **flow diagram** showing how the touched units connect — same ASCII conventions as Section 3.1, so the implementation flow can be compared directly against the architectural flow that authorized it.

This layer is the verification layer. I read this first and use it to confirm the implementation matches the architectural intent.

### 5.2 Detail layer (CC's natural diff, comes after)

The full diff as CC would normally show it. CC should **not** suppress this — I fold it in my mind based on what the navigation layer told me. If the navigation layer reads correctly, the diff is reference material; if something in the navigation layer is surprising, the diff is where I drill in.

The point is to give me the choice of whether to read the diff, not to remove the option. Suppressing the diff would re-create the original problem (rough information, can't drill) in a new form.

### 5.3 Touches block (only for core interface changes)

When the change modifies a core interface — something other code already depends on — insert a **Touches block** between the navigation and detail layers:

```
Touches:
  reads:    <existing state/contracts this change reads from>
  writes:   <existing state/contracts this change writes to>
  assumes:  <preconditions inherited from existing code>
```

This is the lightweight version of blast-radius reporting. It is **not required** for routine changes — adding Touches blocks to every small edit produces noise. The model should add it when the change is to a core boundary; I can request it explicitly otherwise.

Heavier tooling for accurate blast radius (LSP-based MCP, ast-grep) is tracked in `tooling-backlog.md` and not part of this protocol yet.

---

## 6. Failure signals

These are the patterns to watch for that indicate the protocol is breaking down. Recognizing them is the precondition for using them.

- **Premature completion signals are false.** A satisfying exchange is not progress. The unit of progress is a requirement met in the actual system.
- **Output-based progress metrics are unreliable in AI-assisted workflows.** Lines of code and visible artifacts don't map cleanly to advancement. Use requirement coverage and working test runs instead.
- **Bandwidth-overflow signal: skipping ahead.** When I notice I am reading a response and skipping over sections rather than processing them — that is the signal. Stop the response, ask to split, do not push through. Pushing through is what produces the "responding correctly while not actually understanding" failure mode, which then propagates into implementation as a virtual foundation.
- **Unfamiliar concept appearing incidentally is not a real learning moment.** If a new library/package/concept shows up in a response and I don't already have schema for it, the correct move is to log it and open a separate session to learn it. Continuing the current session while half-understanding produces nodding-along behavior.

---

## 7. Conversational hygiene

- **Ask for quality ratings on questions.** Not every question deserves deep investment. Explicitly asking "how important is this?" is a legitimate and efficient use of the tool — both for me asking the model, and for the model asking me before producing a heavy response.

---

## 8. Documentation layering

Three layers, three homes. Never let layers leak into each other — especially never let state drift into invariants.

- **Decisions** (non-obvious choices that won't be obvious to a future reader) → `docs/decisions.md` as ADR entries. One paragraph each: what was decided, what alternatives were considered, why this one.
- **Invariants and conventions** (rules that hold across the project's life — naming, contracts, things that must never change) → `CLAUDE.md`.
- **Current state** (what the system actually does right now) → the code itself and git history. Never duplicate state into either of the above; it goes stale and misleads.

This protocol file itself, and `principles.md`, also live in `docs/`. They are higher-order than ADR: they govern *how* decisions get made and recorded, not which specific decisions were made.

---

*Last reviewed: May 12, 2026.*
