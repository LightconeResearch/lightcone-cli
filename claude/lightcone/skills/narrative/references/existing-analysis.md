# Existing-analysis retrofit mode

> **Status: under development.** This mode is scaffolded but not yet
> production-ready. The workflow below is a working draft — treat it
> as a starting point, not a locked spec. For the production-ready
> path, use paper reproduction mode if applicable. Report friction
> back so this reference can firm up.

A project has been running — with code, results, a working directory,
possibly a partial spec — and is being imported into ASTRA. There is
no published paper; the narrative is being built from artifacts, not
reconstructed from prose.

Read the main SKILL.md first. This file adds what's specific to
retrofit.

Retrofit is distinct from paper reproduction (there is no source
narrative to reconstruct) and from interactive authoring (the work is
already done, or at least substantially done, rather than in flight).
The core move is **archaeology**: classifying what's live, harvesting
intent from whatever artifacts carry it, marking gaps where the record
is silent.

## Workflow

### 1 · Triage

Before writing a single sentence, classify the project's contents.

Go through `astra.yaml` and each sub-analysis and mark:

- **live** — current, active, still used downstream
- **superseded** — kept in the spec for record, but no longer what's
  actually run
- **abandoned** — tried and dropped; may or may not belong in the
  narrative as movement-of-learning
- **unclear** — decision or finding with no documentation; the
  original rationale is not recoverable from the spec alone

Produce this as a short summary and surface via `AskUserQuestion`.
Confirm with the user:

- What stays, what is explicitly deprecated, what is abandoned.
- Whether abandoned options should appear as movement-of-learning
  (sometimes yes: "we initially tried X, which gave Y; switched to Z"
  is honest). Sometimes no: trivial or confidential choices don't
  belong.
- Which `unclear` items the user can reconstruct, vs. which are
  genuinely lost.

The narrative only speaks for live content unless the user explicitly
wants a history section.

### 2 · Harvest

The project's substrate substitutes for a paper's narrative. Mine
these, in roughly decreasing order of value:

- **`README.md`, `CLAUDE.md`, `NOTES.md`, `TODO.md`** at project root.
  Often contain the clearest statement of intent.
- **`.felt/`** or a fibers directory. The author's active thinking,
  decisions with rationale, meeting notes, open questions.
- **Notebook markdown cells.** Often the narrative the author wrote
  for themselves.
- **Code comments** at function-level decision points. "We drop
  rows where X < 0.1 because …" is a rationale waiting to be lifted.
- **Commit messages** at milestone commits. `git log --grep` for
  keywords like "decided," "switched," "abandoned," "fix" can surface
  turning points.
- **Meeting notes, old proposals, grant text.** Grant paragraphs are
  often where motivation lives in its cleanest form.
- **Open issues and closed PRs.** Rejected options often have a PR
  describing what was tried.

Make a list of candidate motivation, methodology, and findings text
before starting to draft. Where possible, anchor each harvested piece
to its source so rationales can be traced.

### 3 · Fill the gaps

For each `unclear` decision, try in order:

1. **Ask the user.** `AskUserQuestion` with the decision and its
   options, asking for a one-sentence rationale.
2. **If the user doesn't know**, write a fair description of what was
   chosen and mark it as reconstructed. Example:
   ```yaml
   rationale: >-
     _(Reconstructed 2026-04: original rationale not recorded.  Current
     reading is that option X was chosen because Y, based on the
     downstream code's assumptions about Z.)_
     ...
   ```
3. **If the rationale is actually lost**, name that. A narrative that
   admits "the reasoning for this cut was not recorded and cannot be
   reconstructed" is honest; one that fabricates a plausible-sounding
   justification is not.

Do the same for findings without evidence, inputs without provenance,
and outputs without a clear source sub-analysis.

### 4 · Draft order

Same as reproduction: inputs → methods → findings → outputs →
summary. Retrofit is stable enough for compression-last to
work. Unlike interactive authoring, you're narrating after the fact.

### 5 · Voice

- **Past tense for what happened**; present tense only for the living
  structure ("the pipeline runs three stages").
- **Don't impose a narrative of inevitability.** If the project tried
  Option A for six months, abandoned it, and switched to B, say so.
  The iteration is the substance of movement-of-learning — retrofit is
  where that content has to come from the archaeology, not from a
  researcher narrating live.
- **Mark reconstructions.** `_(Reconstructed)_` or a brief prose note
  when the authoring draws on harvested material whose original author
  is absent.

### 6 · Critique

In addition to SKILL.md's three-phase and craft audits:

**Triage audit.**

- Does the narrative speak only for live content, unless a deliberate
  history section is included?
- Are deprecated / abandoned elements explicitly named as such, or do
  they appear as if current?

**Harvest audit.**

- Does every load-bearing claim in the narrative trace to a project
  artifact (commit, notebook cell, fiber, code comment, meeting note)
  — or to the user's confirmation?
- Are gaps named rather than fabricated?

## Anti-patterns (retrofit-specific)

- **Fabricated rationales.** Writing a plausible-sounding justification
  for a decision whose actual rationale was "someone chose this and
  nobody remembers." Mark the reconstruction, or say the reasoning is
  lost.
- **Smoothing over abandoned work.** If the project pivoted mid-way,
  retrofit is exactly the place where that iteration belongs. Don't
  write a narrative of smooth progress that contradicts the git log.
- **Narrating around gaps.** A sub-analysis with no findings doesn't
  need filler prose explaining what it didn't find; the narrative
  should say the finding work is not yet done (or was never done).
- **Missing the archaeology step.** Jumping straight to drafting
  without triage and harvest produces a narrative in the author's
  voice about work they didn't do. The result sounds invented because
  it is.
- **Treating CLAUDE.md like a paper.** Harvest from it; don't import
  its style. `CLAUDE.md` is agent-facing; the narrative is
  reader-facing.

## When retrofit becomes reproduction

If, during retrofit, it becomes clear that the project is actually
reproducing an unacknowledged paper (code based on a published
analysis, derived from another group's method), switch to paper
reproduction mode for the parts that map. Hybrid is fine: reproduce
what's published; retrofit what's novel or local.

## When retrofit becomes interactive

If the retrofit surfaces that core decisions are still open and the
user wants to revisit them now, the narrative isn't yet stable. Flag
to the user and switch to interactive mode for those sections —
provisional voice, revisit after decisions land.
