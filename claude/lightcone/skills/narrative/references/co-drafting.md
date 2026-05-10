# Co-drafting mode (stub)

> **Status: under development.** Use paper reproduction (the default flow when a paper exists) when applicable. This file names what's distinct about co-drafting and the open questions; it isn't yet production guidance.

The narrative is being drafted in dialogue with the user, against an existing-shape `astra.yaml`. There's no paper to harvest from and no body of code or fibers to mine; the spec's structure is the only artifact, and the user is the source for everything the structure doesn't already carry.

This mode covers a spectrum:

- **Fresh scoping.** `astra.yaml` was scaffolded by `/lc-new` (or by hand); decisions and outputs are sketched but the analysis hasn't run. Narrative drafted against intent, not results.
- **Live in-flight research.** Work is happening; data is coming in, decisions are settling, results are landing. Spec moves between conversations, narrative moves with it.
- **Newly-stable analysis.** Work has finished or paused; the user wants to write a narrative for what they did. No paper, no fibers — they remember it, and that memory is the source.

Pure greenfield (no `astra.yaml` at all) isn't a coherent narrative-skill task — there's nothing to cite into. If a user is at that stage, route them to `/lc-new` to scaffold structure first.

## What's distinct from paper reproduction

- **Source is conversation, not prose.** The paper-reproduction harvest move (paraphrase from a written source) doesn't apply. Draft moves come from dialogue with the user — `AskUserQuestion` when several framing questions land together, prose follow-ups when one question opens the next.
- **Voice depends on stage.** Reproduction is always declarative ("The pipeline runs in…"). Co-drafting voice tracks where the work is: present-tense for live work, past tense for completed steps, provisional markers when content is volatile.
- **Spec and narrative move together.** In reproduction the spec is fixed (or close to it) and the narrative reconstructs the paper. In co-drafting the spec may shift between drafts; expect to revisit narrative when a decision lands or a sub-analysis splits.

## The ask-first discipline

Co-drafting is the one mode where authoring without asking produces fiction. The user is available; ask. Surface the load-bearing reads before drafting — `AskUserQuestion` when several land together, single questions or prose follow-ups when the conversation wants its own rhythm:

- **Research question.** What are you trying to learn? One sentence.
- **Current headline finding** (if any). What's been established so far? One sentence; a gesture is fine.
- **Movement so far.** What pivots, abandoned options, surprises belong in the record?
- **Implications.** What would you claim today about what this means? Premature strong claims aren't required; honest gestures are.

The user's framing is the substrate. Don't draft around a guess at it.

## Provisional voice

When content is moving, make incompleteness visible. Three moves:

**Phrasing carries confidence.** Not "we constrain X to 3%"; rather "our current best constraint on X is 3%, pending validation of the covariance in [reconstruction](#analyses.reconstruction)." Hedge what's uncertain; claim what's settled.

**Explicit markers.** At the top of `summary` (or any volatile key), an italic note:

```yaml
summary: |
  _(Provisional — revisit after bao_fitting. Last updated 2026-04-23.)_
  We are measuring the BAO scale...
```

The `_(Provisional)_` prefix is a convention, not a spec field. It reads as expected-to-change without breaking the narrative shape.

**Decision rationales can be open.** "We are currently running with option X, pending validation of Y. See [[fiber-or-sub-analysis]]." A `rationale:` doesn't have to be retrospective.

When work stabilizes (a paper draft lands, results publish), revise into reproduction voice — past tense, declarative, scope clear. Co-drafting was scaffolding; the final narrative reads as a stable artifact.

## Open questions before this is production-ready

- **Provisional markers — convention or schema?** Today they're prose conventions (`_(Provisional)_`); whether they belong as structured metadata is open.
- **What's a `tempered`-style flag for narrative?** `tempered: true` on fibers signals "solid enough to build on." A narrative-level analog could let renderers display freshness state.
- **Anchor coverage for elements that don't exist yet.** "Once [reconstruction](#analyses.reconstruction) is run, we expect X." The validator currently requires anchors to resolve — co-drafting may need a "planned" sub-analysis form, or the prose may need to avoid forward-anchoring entirely.
- **Boundary with `/lc-new`.** `/lc-new` does conversational scoping but defers narrative ("filled in later, once structural pieces have settled"). When does the user finish `/lc-new` and switch to `/narrative` for the prose pass? Unclear today.
- **Boundary with retrofit.** A user co-drafting a narrative for completed work is reaching for the same artifacts retrofit mines. The line between "harvest from your own memory" (co-drafting) and "harvest from artifacts you produced" (retrofit) is fuzzy when the user is the artifacts' author.

## Pointers when authoring today

The substrate from SKILL.md applies in full: five keys, length cap, anchor grammar, reserved IDs, data flow, validation, craft. What changes is the *source* of content (dialogue) and the *voice* (provisional where moving).

- Use first-person plural and present tense for live work; past tense for completed steps.
- Hedge when uncertain; claim when confident. Over-hedging is its own failure mode.
- Mark sub-analyses that don't exist yet with provisional language rather than fake anchors.
- Inverted draft order can help: write `summary` first as a stub (to fix intent), then draft the rest, then return to `summary` last to revise. This is the opposite of reproduction's compress-last because the substrate is moving.

## Anti-patterns (co-drafting-specific)

- **Solo drafting.** The user is available; ask before guessing motivation, headline finding, or implications.
- **False completeness.** Writing in reproduction voice ("we measure," "we constrain") when the measurement is in flight. Use "we are measuring" / "our current constraint is X, pending Y."
- **Provisional everywhere.** If every sentence is hedged, the narrative reads as afraid of itself. Hedge the genuinely uncertain claims; state the settled ones plainly.
- **Stale markers.** A "revisit after X" comment left in place after X has landed is worse than no marker at all. Revise on each touch.
- **Over-committing to implications.** Promising what results will mean before they land. A gesture is honest; a claim before evidence is not.

## Report friction

If you hit co-drafting cases this stub doesn't cover, file a fiber or GitHub issue against `lightcone-cli` with `narrative` in the title so the next pass can firm this up.
