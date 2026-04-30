# Interactive mode — in-flight new research

> **Status: under development.** This mode is scaffolded but not yet
> production-ready. The workflow below is a working draft — treat it
> as a starting point, not a locked spec. For the production-ready
> path, use paper reproduction mode if applicable. Report friction
> back so this reference can firm up.

Research is being done now. A narrative is being drafted alongside the
work, not reconstructed from a paper or archaeological sources. The
narrative is expected to change as results land.

Read the main SKILL.md first. This file adds what's specific to
interactive.

Interactive differs from reproduction (no source paper to reconstruct
from — the narrative is the researcher's own) and from retrofit (the
work is still happening, not finished — you are authoring live, with
the researcher in the loop).

The core discipline is **provisional voice**: the narrative makes its
own incompleteness visible, so a reader can tell at a glance what's
settled and what's pending.

## Workflow

### 1 · Orient

1. `astra.yaml` and each sub-analysis — whole files. Note where
   `findings` are stub-level, where decisions are unresolved, where
   outputs don't exist yet.
2. Any project `CLAUDE.md` / working notes.
3. Active fibers at `.felt/` (if present). Fibers are the best
   substrate in interactive mode — they carry the researcher's live
   thinking, recent pivots, open questions. Read the relevant
   top-level fiber and anything it wikilinks.
4. Existing narrative, if any. Revision preserves what lands.

### 2 · Ask first, draft second

Interactive mode is not archaeology. The researcher is available.
Don't guess at motivation or the headline finding — ask. Use
`AskUserQuestion` to batch:

- **Research question.** What are we trying to learn? One sentence.
- **Current headline finding.** What, if anything, has been
  established so far? One sentence.
- **Movement so far.** What has already happened in the work that
  belongs in movement-of-learning? (Pivots, abandoned options, things
  that surprised the researcher.)
- **Implications the researcher would claim today.** What does the
  result — as far as it's gone — *mean*? A gesture is fine; a
  premature strong claim is not.

The researcher's framing is the substrate. Don't draft around a guess
at it.

### 3 · Draft order (inverted from reproduction)

In interactive mode, the executive summary is drafted *first* (as a
stub, to fix intent) and revised last. This is the opposite of
reproduction.

1. **`summary` — stub.** One paragraph, provisional. States
   the question and the current best-guess outcome. Explicitly marked
   provisional (see below). Useful because it forces a clear statement
   of intent the rest of the narrative can align with.
2. **`methods`** — the substance. The process is live; methods is
   where the live thinking goes. Name decisions in flight. Name
   pivots. Use first-person plural, with dates where iteration
   matters. Use `[<date>: <what changed>]` inline if it's load-bearing.
3. **`findings`** — what's been established so far, with anchors to
   `findings.<id>` that actually exist. Phrase claims to make
   dependency visible: "pending validation in
   [reconstruction](#analyses.reconstruction)."
4. **`inputs`** — what the work rests on.
5. **`outputs`** — thin; what's been promoted to the top level, if
   any.
6. **Return to `summary`** and revise it against the rest of
   the draft. Re-mark provisional.

For a decision in flight, `rationale:` can explicitly call out
open-ness: "We are currently running with option X, pending validation
of Y. See [[fiber or sub-analysis]]."

### 4 · Provisional voice

Make incompleteness visible in three ways:

**Phrasing.** Not "we constrain X to 3%"; rather "our current best
constraint on X is 3%, pending validation of the covariance in
[reconstruction](#analyses.reconstruction)." Not "we detect Y"; rather
"we detect Y at the 4σ level in the current fit, with the fit being
revisited after the prior rescope lands."

**Explicit markers.** At the top of `summary` (and optionally
on any key that's unusually volatile), an italic note:

```yaml
summary: >
  _(Provisional — revisit after bao_fitting.  Last updated 2026-04-23.)_
  We are measuring the BAO scale in the DESI DR1 LRG tracer as a
  warm-up before folding in ELGs and QSOs.  Current best result is
  [an 8σ detection of the acoustic peak at z = 0.7
  ](#findings.lrg_bao_detection), with the aggregate precision
  constraint pending completion of the covariance validation in
  [reconstruction](#analyses.reconstruction).
```

The `_(Provisional ...)_` prefix is a convention, not a spec field. It
reads as expected-to-change without breaking the narrative shape.

### 5 · Revision cadence

Interactive narratives accrete. File fibers for:

- The ceiling date for next revision.
- Open questions that will force rewrites when they close.
- Decisions in flight and what a different resolution would change in
  the narrative.

When a major result lands (headline finding solidified, pivotal
decision settled), a full revision pass — including re-drafting the
executive summary in reproduction-style (past tense, declarative) for
the now-settled content, while keeping provisional markers on what's
still open.

### 6 · Voice

- **First person plural** ("we are measuring," "we found"), present
  tense for live work, past tense for completed steps.
- **Hedge when uncertain; claim when confident.** Interactive mode has
  a sharper hedging signal than reproduction — the author's current
  confidence *is* what the reader needs to know. Don't over-hedge
  defensively and don't under-hedge performatively.
- **Name sub-analyses that don't exist yet.** If the plan is to run
  `reconstruction` next and the current narrative anticipates its
  output, say so: "Once [reconstruction](#analyses.reconstruction) is
  run, we expect X; if the expectation fails, Y follows." This is
  legitimate movement-of-learning: it captures what a result is being
  interpreted *against*.

### 7 · Critique (adds to SKILL.md base)

**Provisional audit.**

- Is every claim phrased consistently with the actual confidence level?
- Are provisional markers present where the content is volatile?
- Will a reader one week from now know which pieces need revisiting
  vs. which are settled?

**Freshness audit.**

- Any "last updated" or "revisit after" markers still current, or
  stale?
- Any referenced sub-analysis or finding that has since changed but
  the narrative still reflects the old state?

## Anti-patterns (interactive-specific)

- **False completeness.** Writing in reproduction voice ("we measure,"
  "we constrain") when the measurement is in flight. Use "we are
  measuring" / "our current constraint is X, pending Y."
- **Over-committing to implications.** Promising what results will
  mean before they land. A gesture is honest; a claim before evidence
  is not.
- **Skipping movement-of-learning because "it's still moving."** The
  live process *is* the movement. Capture it while it's cheap; it's
  the hardest content to reconstruct later.
- **Solo drafting.** Interactive is the one mode where authoring
  without asking produces fiction. The researcher is available; ask.
- **Provisional everywhere.** If every sentence is hedged, the
  narrative reads as afraid of itself. Hedge the genuinely uncertain
  claims; state the settled ones plainly.
- **Stale markers.** A "revisit after X" comment left in place after
  X has landed is worse than no marker at all. Revise on each touch.

## When interactive stabilizes

When the work is done (paper draft ready, results published, project
wrapping up), the narrative should be rewritten in reproduction voice.
Interactive was scaffolding; the final narrative reads as a stable
artifact. That rewrite is its own pass — switch modes and treat the
project's own prior drafts as a source, like a paper.
