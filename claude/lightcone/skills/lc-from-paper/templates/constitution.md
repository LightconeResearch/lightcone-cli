---
status: active
---

# <paper-slug> — reproduction constitution

The driving document for the ralph loop reproducing <paper title> (<arXiv ID>, DOI <doi>). Every iteration reads this on entry to know what "done" looks like and how to size its next move. **Sharpened slowly** — only when something fundamental shifts (target moves, scope opens or fences, a material disagreement makes us re-think a sub-analysis). Running accumulators (per-output rigor state, the disagreements log, opportunities) live in `CLAUDE.md`, not here.

## Goal

<What "done" looks like for this reproduction. Concrete: which targets, what verdict against them, what validation passes. E.g.: "A complete `astra.yaml` with recipes that produce reproduced versions of <list of targets>, validated by `astra validate astra.yaml --verify-evidence`, with `comparison-report.yaml` verdict `pass` against the targets in `targets/targets.md`.">

**Fidelity intent.** <The user's prose answer from INTERVIEW to "when is this good enough" — captured verbatim or in close paraphrase. E.g. "just checking if the analysis is tractable — quick sanity on a headline number", "Figure 3 must be right; the rest can stay rough", "full fidelity on the BAO fit, baseline elsewhere", "every primary and secondary target lining up within stated tolerance". Each iteration reads this when deciding cheap vs heavy next moves; COMPARE grades opportunities against it. Static once approved at INTERVIEW; the user can sharpen at any REVIEW.>

## Scope

**In scope:** <targeted figures / tables / numbers, methodological span being reproduced.>

**Out of scope:** <explicit exclusions, fenced from drift.>

## Quality bar

What "canonical" rigor looks like for *this* paper. The bar that primary-target outputs aim for when the fidelity intent calls for it:

- <e.g. "BAO fit posteriors match the paper's Figure 4 within 1σ across the full damping prior range">
- <e.g. "magnitude cuts and selection match the code's defaults exactly; any deviation is recorded as a paper-vs-code disagreement with both options preserved">
- <e.g. "every prior insight cites a real verbatim quote from the cited paper">

This is the ceiling; the fidelity intent determines which outputs need to actually reach it. CLAUDE.md's *Rigor — current state* table tracks where each output currently sits relative to this bar.

## Evidence

The substrate this reproduction is built against — the canonical sources iterations consult:

- **Paper:** `work/reference/{paper.pdf, source/ or document.md, index.json, astra.yaml}` (from `/paper-extraction` during ACQUIRE). The `index.json#citations` block carries each cited paper's resolved DOI for LITERATURE.
- **Code:** `work/reference/code/` (cloned during ACQUIRE; scan inventory at `work/reference/code-index.md`).
- **Paper DOI:** <doi>
- **arXiv ID:** <id> (if applicable)
- **Code repo URL:** <url>

## Open dimensions

Decisions worth surfacing to the user — places the reproduction could go differently and the call benefits from human ratification. Iterations append here when something material comes up that isn't itself a paper-vs-code disagreement (those go to `CLAUDE.md`'s disagreements log instead). The user resolves these at REVIEW close-out, or earlier if they're around.

- (none yet)
