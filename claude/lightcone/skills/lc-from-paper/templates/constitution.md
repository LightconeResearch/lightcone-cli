---
status: active
---

# <paper-slug> — reproduction constitution

The driving document for the ralph loop reproducing <paper title> (<arXiv ID>, DOI <doi>). Every iteration reads this on entry to know what "done" looks like. The body **sharpens slowly** — only when something fundamental shifts (target moves, scope opens or fences, a material disagreement makes us re-think a sub-analysis); Open dimensions is updated each iteration as decisions worth user ratification surface. Durable findings that stay useful past the reproduction — paper-vs-code disagreements, open opportunities for future tightening, pointers to substrate — live in `CLAUDE.md`.

## Goal

<What "done" looks like for this reproduction. Concrete: which targets, what verdict against them, what validation passes. E.g.: "A complete `astra.yaml` with recipes that produce reproduced versions of <list of targets>, validated by `astra validate astra.yaml --verify-evidence`, with `comparison-report.yaml` verdict `pass` against the targets in `targets/targets.md`.">

**Fidelity intent.** <The user's prose answer from ORIENT to "what do you want out of this stretch, given what you have to spend on it" — captured verbatim or in close paraphrase. Carries both the aesthetic dimension (what "good enough" looks like) and the pragmatic dimension (compute, tokens, wall-clock budget). E.g.: "just checking if the analysis is tractable — an afternoon of compute", "Figure 3 must be right; the rest can stay rough — overnight", "full fidelity on the BAO fit, baseline elsewhere — a few days", "every primary and secondary target lining up within stated tolerance, no hard deadline". Each iteration reads this when sizing its next move; COMPARE grades opportunities against it. Static once approved at ORIENT; the user can sharpen at any REVIEW.>

## Scope

**In scope:** <targeted figures / tables / numbers, methodological span being reproduced.>

**Out of scope:** <explicit exclusions, fenced from drift.>

## Quality bar

What the quality bar looks like for *this* paper. The level primary-target outputs aim for when the fidelity intent calls for it:

- <e.g. "BAO fit posteriors match the paper's Figure 4 within 1σ across the full damping prior range">
- <e.g. "magnitude cuts and selection match the code's defaults exactly; any deviation is recorded as a paper-vs-code disagreement with both options preserved">
- <e.g. "every prior insight cites a real verbatim quote from the cited paper">

This is the ceiling; the fidelity intent determines which outputs need to actually reach it.

## Evidence

The substrate this reproduction is built against — the canonical sources iterations consult:

- **Paper:** `work/reference/{paper.pdf, source/ or document.md, index.json, astra.yaml}` (from `/paper-extraction` during ORIENT). The `index.json#citations` block carries each cited paper's resolved DOI for LITERATURE.
- **Code:** `work/reference/code/` (cloned during ORIENT; scan inventory at `work/reference/code-index.md`).
- **Paper DOI:** <doi>
- **arXiv ID:** <id> (if applicable)
- **Code repo URL:** <url>

## Open dimensions

Decisions worth surfacing to the user — places the reproduction could go differently and the call benefits from human ratification. Iterations append here when something material comes up that isn't itself a paper-vs-code disagreement (those go to `CLAUDE.md`'s disagreements log instead). The user resolves these at REVIEW close-out, or earlier if they're around.

- (none yet)
