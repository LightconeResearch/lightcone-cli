# Existing-analysis retrofit mode (stub)

> **Status: under development.** Use paper reproduction (the default flow when a paper exists) when applicable. This file names what's distinct about retrofit and the open questions; it isn't yet production guidance.

A project has been running — code, results, partial spec, no published paper — and is being imported into ASTRA. The `astra.yaml` has been built (or is being built); the narrative is reconstructed from the artifacts that produced the work, not from a written source. Retrofit is **harvest from artifacts**; co-drafting is **harvest from conversation**; reproduction is **harvest from a paper**.

## What's distinct from paper reproduction

- **No source narrative.** The five-key shape has to be assembled from
  what the artifacts carry: README, CLAUDE.md, fibers, notebook cells,
  code comments, commit messages, meeting notes, old proposals, issues,
  closed PRs.
- **Triage comes first.** Sub-analyses and decisions classify as live /
  superseded / abandoned / unclear. The narrative speaks for live content
  by default; abandoned and superseded only appear if the user wants
  history surfaced.
- **Gaps are explicit.** When a decision's original rationale isn't
  recoverable from artifacts and the user can't reconstruct it, the
  honest move is to say so — `_(Reconstructed YYYY-MM: original rationale
  not recorded.)_` — not to fabricate a plausible justification.
- **Past tense for what happened.** Present tense only for living
  structure ("the pipeline runs three stages").

## Open questions before this is production-ready

- **What's the canonical artifact harvest?** README, fibers, notebooks,
  commits, PR threads — order, depth, when to stop. Real retrofit cases
  will vary widely; the skill needs a default ordering and the criteria
  for going deeper.
- **How aggressive is `AskUserQuestion`?** A retrofit on a year-old
  project may have a researcher who remembers some decisions but not
  others. Where's the line between asking and reconstructing?
- **History sections.** When abandoned options are load-bearing
  ("we tried X for six months, switched to Y"), they belong in
  movement-of-learning. Routing: new sub-analysis with `excluded:` /
  `lifecycle: abandoned`? Inline marker in `methods`? No firm answer.
- **Voice for reconstructed content.** `_(Reconstructed)_` works
  inline. Whether reconstructed-vs-original needs structural distinction
  in the spec, or stays a prose convention, is open.

## When retrofit shifts modes

- **Becomes reproduction.** If the project is reproducing an unacknowledged paper, switch to the default flow for the parts that map. Hybrid is fine.
- **Becomes co-drafting.** If retrofit surfaces that core decisions are still open and the user wants to revisit them now, switch to co-drafting mode for those sections (provisional voice, revisit after decisions land).

## Report friction

If you hit retrofit cases this stub doesn't cover, file a fiber or
GitHub issue against `lightcone-cli` with `narrative` in the title so
the next pass can firm this up.
