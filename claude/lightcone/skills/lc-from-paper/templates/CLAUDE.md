# <paper-slug>

Reproduction of **<paper title>** (<arXiv ID>). DOI: <doi>. One-line subject: <e.g. "BAO scale measurement from DESI DR1">.

The driving document for this reproduction is [`constitution.md`](constitution.md) — Goal, Fidelity intent, Scope, Quality bar, Evidence. Every ralph iteration reads it on entry. This file (`CLAUDE.md`) is the auto-loading walk-up: rules + running accumulators.

## Rules

- **Code-as-canonical when `work/reference/code/` exists.** Every iteration that touches a sub-analysis reads the relevant code first. Where paper and code disagree, code is canonical for numerics, plotting, and method. When `work/reference/code/` is absent, paper is the only anchor — implement fresh from the spec, expect slower convergence, surface gaps honestly to the user rather than dressing them up.
- **Never block on `AskUserQuestion` mid-iteration.** Each ralph iteration runs in a fresh detached session; the user isn't reachable interactively. Append questions to `open-questions.md` and continue with the best-judgment default. The user resolves accumulated questions at REVIEW close-out (which runs in the user's main session).
- **arxiv-LaTeX-first acquisition.** PDF + Docling is a fallback for non-arxiv only.
- **`astra validate --verify-evidence`** is the fidelity gate; evidence quotes must match source PDFs.
- **No synthetic data.** Unless the paper itself uses synthetic data as input, every input dataset must be downloaded or queried from its real source.
- **Commit as you go.** Small, descriptive commits per significant change. The git log is the chronological trail of the reproduction; the next iteration reads it to know what landed.
- **Updates go in code, files, and the accumulators below — not progress notes scattered in the body.** Discoverable updates; the next iteration finds what changed by inspecting the system.

## Rigor — current state

Per-output trajectory tracking, updated by iterations as they produce artifacts. Coarse adjectives per output or per phase: *sketch / baseline / tightened / canonical*. Read alongside [`constitution.md`](constitution.md)'s Fidelity intent to decide how much to push on the next iteration. Empty until the first iteration produces something:

- (none yet)

### Open opportunities

Gaps that could be tightened if the reproduction comes back. Each carries a sense of leverage and where it sits relative to the constitution's Fidelity intent. Format: `<area> — <what could be tightened> — <leverage> — <above|at|below intent>`. Empty until a COMPARE iteration surfaces one:

- (none yet)

## Paper-vs-code disagreements

Material disagreements between paper and code, logged here as iterations find them. Code is canonical for numerics, plotting, and method (per the rule above); both options are preserved in `astra.yaml` as decision alternatives. Each entry summarizes the disagreement and points to the corresponding decision so any iteration can see them at a glance. Surfaced to the user at REVIEW close-out (or earlier if they're around).

- (none yet)

## Pointers

- [`constitution.md`](constitution.md) — Goal, Fidelity intent, Scope, Quality bar, Evidence, Open dimensions. The ralph loop's driving document.
- `open-questions.md` — accumulated questions from iterations, resolved in REVIEW.
- `work/reference/index.json` — paper structural index (figures, tables, outline, citations with DOIs); the starting surface for any "where in the paper does X happen" lookup.
- `work/reference/code-index.md` — code inventory (when code present): module map, candidate decisions with file:line, entry-points, gotchas.
- `work/cited/<doi-slug>/` — per-cited-paper substrate produced by LITERATURE for `prior_insights:` resolution.
- <any paper-specific conventions or warnings the user surfaced during the interview>
