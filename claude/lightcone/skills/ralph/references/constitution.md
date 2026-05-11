# Constitution — depth reference

Drafting a constitution. The SKILL body's **Authoring** section covers the procedural backbone (Study → Draft → Refine → Launch). This reference goes deeper on voice, sections, and the discipline that keeps a constitution from sliding into a plan.

The constitution itself is just a markdown file with YAML frontmatter that a runner reads on each iteration. The bundled runner is `scripts/ralph` (next to this skill); other dispatchers can read the same markdown shape. The runner is interchangeable; the constitution is what matters.

---

## What a constitution is

A constitution is a design document with trust built in. Like a governmental constitution, it lays out principles and aspirations — not specific laws, not the current state of affairs. It is designed to outlast any single iteration and remain valid as the world changes around it.

**A good constitution never says "50 files remain"** — that is a snapshot that goes stale. It says `check "grep -r 'old_pattern'"` — that is a principle that stays true until the work is done.

Constitutions do not prescribe steps. They describe what the system looks like when it is right — the desired state, in both senses of the word. Nothing in the constitution should become confusing or unnecessary as the desired state is reached. Whoever works from it surveys reality, reasons about the gap, and decides what is highest value. Each iteration of the work does this with fresh context.

**Constitution, not plan.** Plans assume you know the path; constitutions trust the agent to find it — with taste, judgment, and fresh eyes each time. This matters most in science and exploratory work, where each decision is informed by the result just before it.

**Separation of context: if you craft, you never do the work yourself.** The constitution is designed by one role; iterations are run by another.

---

## When to write a constitution

- Work where adaptation matters more than a fixed plan: scientific investigation, exploratory refactoring, creative writing.
- The desired state is clear (or can be made clear) but the path is not.
- Iterations need to re-read with fresh context and make judgment calls.
- A checklist would either be wrong after one step or race through without judgment.

Don't write a constitution for: clearly-scoped atomic tasks, anything where a checklist or a plan is genuinely the right shape.

---

## Workflow (deeper)

### 1. Study

Read relevant files, understand existing patterns. This informs the **constitution**, not implementation — the goal is pointers that iterations will follow, not a head start on the work.

### 2. Draft

Create the constitution as a markdown file with `status: active` in YAML frontmatter (that's what the launcher checks). Some workflows expect a specific path so a runner picks it up — e.g. `/lc-from-paper` writes `constitution.md` at the reproduction workdir root. Otherwise put it wherever the work lives. The section block in the SKILL's "What goes in a constitution" is your starting shape; fill what fits, drop what doesn't.

Use the crafting process from [`crafting.md`](crafting.md):

- **Wonder → Ontology:** what IS the desired state? Name it precisely.
- **Design → Delivery:** what sections does this constitution need? Which are pointers vs snapshots?

Stances that help most during constitution drafting:

- **Ontologist** for naming the desired state ("what IS 'done' here?")
- **Simplifier** for fencing scope ("what are we explicitly leaving alone?")
- **Contrarian** for pressure-testing whether the whole framing is right
- **Architect** when the constitution is about refactoring structure

### 3. Refine

Show the draft, get feedback, revise. Use AskUserQuestion for structured choices. Apply the qualitative ambiguity self-check from `crafting.md` — goal, constraints, success — before launching.

Repeat until it feels solid. It does not have to be complete; open questions belong in the Open Questions section.

### 4. Launch

When approved, hand to a runner. Bundled option: `.claude/skills/ralph/scripts/ralph my-constitution.md`. The runner re-reads the constitution each iteration, so refinements between iterations are normal.

---

## Constitutional sections

A constitution needs enough structure that an iteration landing cold can orient itself, and enough freedom that it can adapt. Common sections — use what fits, skip what does not, add what is missing:

```markdown
## Desired State
What the system looks like when it is done. Invariants, quality bar,
done-conditions. Fence the scope — what to aim for AND what to leave alone.

## Context
File paths, existing patterns, architectural constraints. Things iterations
need to *find* but not *achieve*.

## Skills
Which skills to activate before working.

## Evidence
How to check progress — commands, test suites, grep patterns. Pointers to
ground truth that iterations measure themselves against.

## Open Questions
Uncertainties the user should weigh in on. Iterations add to this; the user
resolves between loops.
```

---

## Principles (deeper)

**Pointers, not snapshots.** `check "grep -r 'old_pattern'"` not "50 files remain." Snapshots go stale; pointers stay valid across iterations. This is the constitutional principle: write what remains true until the work is done.

**Reshape, don't accrete.** When the desired state evolves — testing surfaces a gap, a meeting changes the priority, a sibling decision lands — rewrite the affected sections so the body still reads as today's desired state. Don't tack on a "Round 2" section; don't add an "Amendments" appendix; don't keep the old framing alongside the new one as a sediment. A green-field constitution will change a lot as it matures, and a mature one will keep changing as reality does. The chronology lives in the runner's history surface (commits, sibling notes); the body lives in *now*.

**Prefer existing systems.** Before designing anything new: can what is there handle this?

**Constraints need reasons.** Bare constraints get creatively circumvented. Include enough *why* that an iteration knows when it applies.

**Scope is a gift.** A clear fence — "only rename, don't refactor" — saves iterations from well-intentioned drift. Explicit scope frees the agent to work confidently within it.

---

## Constitutions that shape artifacts

Some constitutions do not build code — they shape artifacts like documentation or research narratives. These have different rhythms:

- **The desired state is comprehension, not correctness.** "A reviewer can follow the narrative cold" is harder to test than "all tests pass" — but it is the right bar. Evidence for progress: fewer redundant plots, clearer prose, more natural flow.
- **The artifact continues to grow.** Unlike a refactoring (which finishes), a research narrative keeps acquiring nodes. The constitution shapes how growth presents itself, not when growth stops.

---

## Anti-patterns

- **Checklists.** "1. Add X, 2. Add Y" — iterations race through without judgment.
- **Vague done.** "Make it better" — when does iteration stop? What would a reader see?
- **Over-specification.** Prescribing *how* instead of *what*. Trust the agent's taste.
- **Snapshot language.** "Currently 50 files" — will be wrong after one iteration.
- **Immutable seed.** Not our shape. The constitution is meant to be edited between iterations; do not treat it as frozen.
- **Numerical convergence.** "Iteration stops when similarity ≥ 0.95" — wrong shape for science. Stop when the Evidence section says the desired state has been reached.
- **Decision logs in the body.** "Resolved choices" / "Decisions made" / "Process notes" sections turn the constitution into a process journal. When a question gets answered (in conversation, via `AskUserQuestion`, in a review), fold the answer into the narrative where it is contextually relevant — into Invariants, Desired State, Context — and let the runner's chronological surface (commits, sibling notes) carry the chronology. The constitution describes *what is*, not *how we got here*; an "Open Questions" section that has been fully resolved should be deleted, not left as a victory log.
- **Amendment scaffolding.** "Round 2", "v2 deltas", "Updates 2026-05-04 →", "Second round amendments". The same failure as a decision log, played out across edits: the body becomes a sediment of layered framings instead of the current desired state. When the desired state shifts, *reshape* the affected sections — rewrite headings, update prose, drop what no longer applies — so the document still reads as one coherent description of now. The story of how it got here is what commits and the outcome blurb are for.

---

## When crafting lands here

The crafting rhythm in [`crafting.md`](crafting.md) applies to all careful interactive thinking; this reference kicks in when the target artifact is specifically a constitution. The diamonds do most of the work — the funnel mechanic used for open-ended exploration is not the primary move here, because there is already one specific artifact being produced. See the Workflow section above for which stances help most at each drafting phase.
