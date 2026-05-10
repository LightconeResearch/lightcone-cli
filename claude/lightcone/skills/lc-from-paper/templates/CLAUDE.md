# <paper-slug>

Reproduction of <paper title> (<arXiv ID>). DOI: <doi>.

## Paper

- Authors: <list>
- One-line subject: <e.g. "BAO scale measurement from DESI DR1">
- Code repo: <url> (cloned to `work/reference/code/` during ACQUIRE)

## Goal

<What "done" looks like for this reproduction. Concrete: which targets, what verdict against them, what validation passes. E.g.: "A complete `astra.yaml` with recipes that produce reproduced versions of <list of targets>, validated by `astra validate astra.yaml --verify-evidence`, with `comparison-report.yaml` verdict `pass` against the targets in `targets/targets.md`.">

**In scope:** <targeted figures / tables / numbers, methodological span being reproduced.>

**Out of scope:** <explicit exclusions, fenced from drift.>

## Rigor

*Current state* — populated by sub-agents as they produce artifacts. Coarse adjectives per output or per phase: *sketch / baseline / tightened / canonical*. Empty until the first phase produces something:

- (none yet)

*Open opportunities* — what could benefit from more attention if the user comes back, with a sense of leverage. Format: `<area> — <what could be tightened> — <leverage>`. Empty until a sub-agent surfaces a gap:

- (none yet)

## Paper-vs-code disagreements

Material disagreements between paper and code, logged here as sub-agents find them. Code is canonical for numerics, plotting, and method (per the discipline below); both options are preserved in `astra.yaml` as decision alternatives. Each entry summarizes the disagreement and points to the corresponding decision so any sub-agent or future orchestrator session can see them at a glance. Surfaced to the user the next time they're around.

- (none yet)

## Rules

- **Code-as-canonical when `work/reference/code/` exists.** Every implementing sub-agent reads relevant code on entry. Where paper and code disagree, code is canonical for numerics, plotting, and method.
- **Never block on `AskUserQuestion` mid-sub-agent.** Sub-agents don't have `AskUserQuestion`. Ask in prose if the user is reachable; otherwise append the question to `open-questions.md` and continue with the best-judgment default. The user resolves accumulated questions in REVIEW.
- **arxiv-LaTeX-first acquisition.** PDF + Docling is a fallback for non-arxiv only.
- **`astra validate --verify-evidence`** is the fidelity gate; evidence quotes must match source PDFs.
- **Commit as you go.** Small, descriptive commits per significant change. The git log is the chronological trail of the reproduction.

## Pointers

- `open-questions.md` — accumulated questions from autonomous-mode runs, resolved in REVIEW.
- <any paper-specific conventions or warnings the user surfaced during the interview>
