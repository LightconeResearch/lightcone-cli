# /lc-from-paper

Reproduce a published scientific paper as a complete ASTRA project. The
skill is **interview-first** and **ralph-driven**. INTERVIEW and
ACQUIRE run in the user's main session to set up the per-paper
substrate. A ralph loop then carries the long middle —
ARCHITECT → SPECIFY → LITERATURE → IMPLEMENT → RUN → COMPARE —
across many iterations against the same constitution. REVIEW returns
to the user's main session once the loop closes.

`/lc-from-paper` is the entry point of the paper-reproduction bundle.
Sibling skills ([`ralph`](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/ralph/SKILL.md)
for the loop, [`paper-extraction`](paper-extraction.md),
[`narrative`](narrative.md), [`figure-comparison`](figure-comparison.md),
[`check-sentence-by-sentence`](check-sentence-by-sentence.md)) live in
the same plugin and are invoked by role across the phases.

Source: [`claude/lightcone/skills/lc-from-paper/SKILL.md`](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/lc-from-paper/SKILL.md).

## Architecture

Two pieces.

1. **Interactive bookends in the user's main session.** INTERVIEW and
   REVIEW are conversations with the user. ACQUIRE is two parallel
   sub-skill invocations (`/paper-extraction` and `/lc-from-code` in
   scan-only mode) that produce the on-disk substrate everything
   downstream consults.

2. **A ralph loop for the long middle.** Once `constitution.md` is
   drafted (INTERVIEW) and the substrate is on disk (ACQUIRE),
   `/lc-from-paper` launches a ralph loop against the constitution.
   Each iteration starts a fresh tmux-detached Claude session with
   the constitution as system prompt, surveys the workdir, picks the
   next valuable move (typically one phase's worth of work), does
   it, commits, and exits. The fresh-context property is automatic:
   iteration N+1 reads N's work without bias, so per-phase review
   collapses into "the next iteration is the review." Parallel
   fan-out (LITERATURE Haiku quote-finders, SPECIFY per-sub-analysis
   work, IMPLEMENT per-output work) happens *inside* an iteration,
   one level deep from the iteration's main session.

## Phases

Nine phases, zero-indexed. INTERVIEW + ACQUIRE + REVIEW run in the
user's main session; phases 2–7 run as ralph iterations.

| # | Phase | Where | Primary outputs |
|---|-------|-------|------------------|
| 0 | INTERVIEW | user's main session | per-paper `constitution.md` + `CLAUDE.md` |
| 1 | ACQUIRE | user's main session | `work/reference/{paper.pdf, source/ or document.md, figures/, tables/, index.json, astra.yaml, code/, code-status.yaml, code-index.md}` |
| 2 | ARCHITECT | ralph iteration | stub `astra.yaml` (sub-analyses, inputs, outputs, narrative) |
| 3 | SPECIFY | ralph iteration | filled `astra.yaml` (`decisions:`, `findings:`, `prior_insights:` placeholders, anchored narrative); `targets/targets.md`; `implementation-notes.md`; `universes/baseline.yaml` |
| 4 | LITERATURE | ralph iteration | `prior_insights:` Evidence entries each carry resolved `quote:` + `location:` selectors; per-paper PDFs cached via `astra paper add` |
| 5 | IMPLEMENT | ralph iteration | `scripts/`, `requirements.txt`, recipes in `astra.yaml` |
| 6 | RUN | ralph iteration | `results/<universe>/<output>/` |
| 7 | COMPARE | ralph iteration | `comparison-report.{yaml,md}` plus an opportunity assessment graded against the user's fidelity intent |
| 8 | REVIEW | user's main session | `REPRODUCTION-SUMMARY.md`, `/figure-comparison` HTML, resolved `open-questions.md`, finalized reproduction outcome |

## Per-paper substrate: constitution + CLAUDE.md

INTERVIEW drafts two files in the reproduction workdir; every
iteration walks up to them automatically.

- **`constitution.md`** — the ralph loop's driving document. YAML
  frontmatter declares `status: active`. Sections: Goal (carrying the
  **fidelity intent** — the user's own "when is this good enough"),
  Scope (in/out), Quality bar, Evidence (paper DOI, arXiv ID, code
  repo URL), Open dimensions. Sharpens slowly, only when something
  fundamental shifts.
- **`CLAUDE.md`** — the auto-loading walk-up. Paper identity at the
  top; Rules (code-as-canonical, no blocking on `AskUserQuestion`
  mid-iteration, arXiv-LaTeX-first, `astra validate
  --verify-evidence` as the fidelity gate); Rigor accumulator
  (*Current state* per output plus *Open opportunities*, updated each
  iteration); Disagreements log (running, also updated each
  iteration); Pointers.

Pointers, not snapshots.

## Disciplines

- **Workdir is the state.** File existence, `git log`, and `astra
  validate` answer "what phase am I on" deterministically — no
  separate state machine.
- **Code-as-canonical, with disagreements recorded.** Where paper
  and code disagree on something material, code wins for numerics,
  but the disagreement is preserved as a decision option and noted
  in CLAUDE.md.
- **Rigor is a trajectory toward the user's intent.** Each iteration
  calibrates its work from the gap between *Current state* and the
  Goal's fidelity intent: cheap (one clean review-iteration is
  enough) versus heavy (two consecutive clean review-iterations
  required). Review happens sequentially via iteration boundaries;
  the fresh-context property is automatic.
- **arXiv LaTeX first.** PDF + Docling is the non-arXiv fallback only.
- **No synthetic data.** Unless the paper itself uses synthetic data,
  every input must be real.
- **Open questions for autonomous iteration.** Iterations run detached
  in tmux, so `AskUserQuestion` isn't available. Questions go to
  `open-questions.md` with the iteration's best-judgment default
  applied; the user resolves them at REVIEW close-out.

## Anti-patterns

- Doing the long middle in the user's main session instead of launching
  the loop. INTERVIEW + ACQUIRE + REVIEW belong in the main session;
  ARCHITECT through COMPARE belong in iterations.
- Asking an iteration to use `AskUserQuestion` — iterations are
  detached.
- Re-implementing what `astra` already does (`astra validate`, `astra
  paper add`).
- Bundling phases into one iteration — defeats fresh-context review.
- Accreting amendment sections in `constitution.md` — reshape, don't
  append.

## Related

- [Bundle README](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/README.md)
  — why the bundle is co-located rather than a separate plugin install.
- [`/ralph`](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/ralph/SKILL.md)
  — the loop substrate (authoring + launching + iterating).
- [`/paper-extraction`](paper-extraction.md) — ACQUIRE's primary
  acquisition path; also invoked per cited paper by LITERATURE.
- [`/narrative`](narrative.md) — ARCHITECT's structural narrative and
  SPECIFY's anchored content narrative.
- [`/figure-comparison`](figure-comparison.md) — REVIEW (mandatory) and
  also user-invokable.
- [`/check-sentence-by-sentence`](check-sentence-by-sentence.md) —
  REVIEW (opt-in) and also user-invokable.
