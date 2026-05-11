# /lc-from-paper

Reproduce a published scientific paper as a complete ASTRA project. The
skill is **interview-first** and **ralph-driven**: INTERVIEW + ACQUIRE
run in the user's main session to set up the per-paper substrate, then
a ralph loop carries the long middle (ARCHITECT → SPECIFY → LITERATURE
→ IMPLEMENT → RUN → COMPARE) across many iterations against the same
constitution, with REVIEW returning to the user's main session after
the loop closes.

`/lc-from-paper` is the entry point of the paper-reproduction bundle.
The sibling skills ([`ralph`](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/ralph/SKILL.md)
for the loop, [`paper-extraction`](paper-extraction.md),
[`narrative`](narrative.md), [`figure-comparison`](figure-comparison.md),
[`check-sentence-by-sentence`](check-sentence-by-sentence.md)) are
co-located in the same plugin and invoked by role across the phases.

Source: [`claude/lightcone/skills/lc-from-paper/SKILL.md`](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/lc-from-paper/SKILL.md).

## Architecture

Two pieces:

1. **Interactive bookends in the user's main session.** INTERVIEW and
   REVIEW are conversations with the user. ACQUIRE is two parallel
   sub-skill invocations (`/paper-extraction` and `/lc-from-code` in
   scan-only mode) that produce the on-disk substrate everything
   downstream consults.

2. **A ralph loop for the long middle.** Once `constitution.md` is
   drafted (INTERVIEW) and the substrate is on disk (ACQUIRE),
   `/lc-from-paper` launches a ralph loop against the constitution.
   Each iteration starts a fresh tmux-detached Claude session with the
   constitution as system prompt, surveys the workdir, picks the next
   valuable move (typically one phase's worth of work), does it,
   commits, exits. The fresh-context property is automatic — iteration
   N+1 reads N's work without bias, which makes per-phase review
   collapse into "the next iteration is the review."

Parallel fan-out (LITERATURE Haiku quote-finders, SPECIFY per-sub-
analysis work, IMPLEMENT per-output work) happens *inside* an
iteration, one level deep from the iteration's main session.

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

Drafted during INTERVIEW. The reproduction workdir holds **two files**
that iterations walk up to automatically:

- **`constitution.md`** — the ralph loop's driving document. YAML
  frontmatter `status: active`; sections: Goal (with **fidelity
  intent** prose — the user's own answer to "when is this good
  enough"), Scope (in / out), Quality bar, Evidence (paper DOI, arXiv
  ID, code repo URL), Open dimensions. Sharpens slowly — only when
  something fundamental shifts.
- **`CLAUDE.md`** — auto-loading walk-up. Paper identity at the top,
  Rules (code-as-canonical, never-block-on-`AskUserQuestion`-
  mid-iteration, arxiv-LaTeX-first, `astra validate --verify-evidence`
  as the fidelity gate), Rigor accumulator (*Current state* per output
  + *Open opportunities*, updated by iterations), Disagreements log
  (running, updated by iterations), Pointers.

Pointers, not snapshots.

## Disciplines

- **Workdir is the state.** File existence + `git log` + `astra
  validate` answer "what phase am I on" deterministically. No separate
  state machine.
- **Code-as-canonical, with disagreements recorded.** Where paper and
  code disagree on something material, code wins for numerics but the
  disagreement is preserved as a decision option and noted in
  CLAUDE.md.
- **Rigor is a trajectory toward the user's intent.** Each iteration
  sizes its work from the gap between *Current state* and the Goal's
  fidelity intent — cheap (one clean review-iteration is enough) vs
  heavy (two consecutive clean review-iterations required). Default is
  sequential review via iteration boundaries; in-iteration fan-out is
  the orthogonal parallelism option where it actually pays.
- **arxiv-LaTeX-first acquisition.** PDF + Docling is the non-arxiv
  fallback only.
- **No synthetic data.** Unless the paper itself uses synthetic data,
  every input must be real.
- **Open-questions for autonomous iteration.** Iterations run detached
  in tmux; `AskUserQuestion` isn't available. Questions go to
  `open-questions.md` with the iteration's best-judgment default
  applied; the user resolves at REVIEW close-out.

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
