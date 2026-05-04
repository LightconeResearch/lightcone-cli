---
name: constitution
description: >
  Draft a constitution — a markdown spec describing a desired state for
  autonomous iteration. Study the problem space, shape the spec
  interactively (two-diamonds rhythm; six stances on demand), then hand
  it to a runner — a ralph loop, a shuttle dispatch, or any other
  iteration-runner. Use for any work where adaptation matters more than
  a fixed plan: science, refactoring, exploration, creative work.
  Triggers: "constitution", "constitute", "ralph spec", "set up a ralph",
  "create a ralph", "write a spec".
---

# Constitution

A constitution is a design document with trust built in. Like a governmental constitution, it lays out principles and aspirations — not specific laws, not the current state of affairs. It's designed to outlast any single agent or iteration and remain valid as the world changes around it. A good constitution never says "50 files remain" because that's a snapshot that goes stale; it says "check `grep -r 'old_pattern'`" because that's a principle that stays true until the work is done.

Constitutions don't prescribe steps. They describe what the system looks like when it's right — the desired state, in both senses of the word. Nothing in the constitution should become confusing or unnecessary as the desired state is reached. Whoever works from it surveys reality, reasons about the gap, and decides what's highest value. In a ralph loop, each iteration does this with fresh context.

This matters most in science and exploratory work, where each decision is informed by the result just before it. A plan assumes you know the path; a constitution trusts the agent to find it — with taste, judgment, and fresh eyes each time.

**Separation of context: if you craft, you never do the work yourself.**

## Workflow

1. **Study** — Read relevant files, understand existing patterns. This informs the *spec*, not implementation. The goal is pointers that iterations will follow.

2. **Draft** — Create a markdown spec file. The bundled template lives in the sibling `ralph-loops` skill:
   ```bash
   cp ../ralph-loops/assets/spec.md my-spec.md
   ```
   (or copy directly from `claude/lightcone/skills/ralph-loops/assets/spec.md` if you're outside a skill).
   Fill in what you can — don't wait until it's perfect.

3. **Refine** — Show the draft, get feedback, revise. Use AskUserQuestion for structured choices. The two-diamonds rhythm and six stances in [`references/crafting.md`](references/crafting.md) help most when the user is deciding something non-trivial. Apply the qualitative ambiguity self-check before launching.

4. **Launch** — When approved, hand the spec to whichever runner is appropriate. Common options:

   - **`/ralph-loops`** — bundled, manual loop runner. Tmux session re-spawns iterations against the spec until status flips off open/active.
     ```bash
     ../ralph-loops/scripts/ralph my-spec.md [--backend claude|codex] [-- extra-flags...]
     ```
     Add `-- --chrome` for visual/frontend work. Session: `ralph-<spec-name>`. Attach: `tmux attach -t ralph-<spec-name>`.
   - **External dispatchers** (e.g. shuttle, when felt is installed) — watch a fiber tree for dispatch-eligible blocks and spawn single-shot workers. Their configuration is owned outside this skill.

   The constitution stays editable while iteration runs; successive iterations re-read it each cycle, so refinements between iterations are normal.

## What goes in a constitution

A constitution needs enough structure that an iteration landing cold can orient itself, and enough freedom that it can adapt. Common sections — use what fits, skip what doesn't, add what's missing:

```markdown
## Desired State
What the system looks like when it's done. Invariants, quality bar,
done-conditions. Fence the scope — what to aim for AND what to leave alone.

## Context
File paths, existing patterns, architectural constraints. Things iterations
need to *find* but not *achieve*.

## Skills
Which skills to activate before working (e.g., /snakemake, /narrative).

## Evidence
How to check progress — commands, test suites, grep patterns. Pointers to
the ground truth that iterations measure themselves against.

## Open Questions
Uncertainties the user should weigh in on. Iterations add to this; the user
resolves between loops.
```

For deeper reference on each section's voice and the discipline that keeps a constitution from drifting into a plan, see [`references/constitute.md`](references/constitute.md).

## Principles

**Constitution, not plan.** Say what the system looks like when it's right. Never describe the current state — anything that becomes false or irrelevant as work progresses doesn't belong. If a section would be outdated after one iteration, it's a snapshot — replace it with a pointer.

**Pointers, not snapshots.** "Check `grep -r 'old_pattern'`" not "50 files remain." Snapshots go stale; pointers stay valid across iterations. This is the constitutional principle: write what remains true until the work is done.

**Prefer existing systems.** Before designing anything new: can what's there handle this?

**Constraints need reasons.** Bare constraints get creatively circumvented. Include enough *why* that an iteration knows when it applies.

**Scope is a gift.** A clear fence — "only rename, don't refactor" — saves iterations from well-intentioned drift. Explicit scope frees the agent to work confidently within it.

## Constitutions that shape artifacts

Some constitutions don't build code — they shape artifacts like documentation, dashboards, or research narratives. These have different rhythms:

- **The desired state is comprehension, not correctness.** "A reviewer can follow the narrative cold" is harder to test than "all tests pass" — but it's the right bar. Evidence for progress: fewer redundant plots, clearer prose, more natural flow.
- **The artifact continues to grow.** Unlike a refactoring (which finishes), a research narrative keeps acquiring nodes. The constitution shapes how growth presents itself, not when growth stops.

## Anti-patterns

**Checklists.** "1. Add X, 2. Add Y" — iterations race through without judgment.

**Vague done.** "Make it better" — when does iteration stop?

**Over-specification.** Prescribing *how* instead of *what*. Trust the agent's taste.

**Snapshot language.** "Currently 50 files" — will be wrong after one iteration.

**Decision logs in the body.** "Resolved choices" / "Process notes" sections turn the constitution into a process journal. When a question gets answered, fold the answer into the narrative where it's contextually relevant — into Invariants, Desired State, Context — and let the runner's history surface (`felt history`, commits, etc.) carry the chronology.

---

## References

- [`references/constitute.md`](references/constitute.md) — depth on
  drafting voice, sections, and the felt-flavored crafting workflow.
  Felt-optional: read past the felt-specific commands if felt isn't installed.
- [`references/crafting.md`](references/crafting.md) — two-diamonds
  rhythm, six stances, the funnel ledger, and the qualitative ambiguity
  self-check. Use this when the conversation has careful-thinking
  character — not every constitution drafting needs it, but the ones that
  do are the ones that benefit most.

## Provenance

Merged from two sources:

- [`cailmdaley/skills/skills/constitution/`](https://github.com/cailmdaley/skills/tree/main/skills/constitution) (public, procedural, felt-agnostic) — provided the SKILL body backbone.
- `~/.claude/skills/felt/references/{constitute,crafting}.md` (personal felt skill) — provided the depth references; felt-specific commands have been softened to felt-optional framing so this skill stands alone in lightcone-cli.

Copied here for the paper-reproduction bundle so `/paper2astra` can invoke `/constitution` to draft the per-paper reproduction constitution during its interview phase. The merged shape may flow back upstream; re-sync as needed.
