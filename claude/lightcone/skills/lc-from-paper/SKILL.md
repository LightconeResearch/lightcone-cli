---
name: lc-from-paper
description: >
  This skill should be used when the user wants to reproduce a published
  scientific paper in ASTRA — has a DOI, arXiv ID, or PDF — or asks to
  "reproduce <paper>", "set up reproduction", or "import a paper". Also
  use when continuing or resuming an existing reproduction workdir. The
  skill instructs Claude to run INTERVIEW + ACQUIRE in the user's main
  session, then hand the reproduction off to a ralph loop whose
  iterations carry the remaining phases (ARCHITECT → SPECIFY → LITERATURE
  → IMPLEMENT → RUN → COMPARE) until the constitution closes, at which
  point REVIEW close-out runs back in the user's main session.
---

# lc-from-paper

You are helping the user reproduce a published scientific paper as a complete ASTRA project. This is a long, complex task that won't fit in a single context window — it spans discrete phases: acquire the paper and its code, architect the spec, specify decisions and findings, resolve cited literature, implement, run, compare, review.

The architecture is two-piece:

1. **Interactive bookends in the user's main session.** INTERVIEW and REVIEW are conversations with the user. ACQUIRE is two parallel sub-skill invocations (`/paper-extraction` and `/lc-from-code` in scan-only mode) that produce the on-disk substrate everything downstream consults.

2. **A ralph loop for the long middle.** Once the per-paper `constitution.md` is drafted (INTERVIEW) and the substrate is on disk (ACQUIRE), you launch a ralph loop against the constitution. Each iteration starts a fresh session with the constitution as system prompt, surveys the workdir, picks the next valuable move (typically one phase's worth of work), does it, commits, exits. The fresh-context property is automatic — iteration N+1 reads N's work without bias, which makes per-phase review collapse into "the next iteration is the review."

The whole thing is driven by **the per-paper `constitution.md`** at the reproduction workdir root, plus the auto-loading `CLAUDE.md` walk-up. The constitution describes the goal (what "done" looks like, the user's fidelity intent, scope, quality bar); CLAUDE.md carries the running accumulators (rigor state per output, paper-vs-code disagreements log, rules). Every iteration walks up to both.

## Setup: git-tracked workdir

The reproduction's directory should be a git repo — if not already, `git init` it before launching the ralph loop. Every iteration commits its work as it goes — small, descriptive commits per significant change. The git log is the chronological trail of the reproduction; `git diff` is how the next iteration reads what landed.

## The phases

Nine phases (zero-indexed). INTERVIEW and ACQUIRE run before the loop, in the user's main session; the loop's iterations carry phases 2–7; REVIEW runs after the loop closes, back in the user's main session.

| # | Phase | Where it runs | Reference | Primary outputs |
|---|---|---|---|---|
| 0 | INTERVIEW | user's main session | [`references/interview.md`](references/interview.md) | per-paper `constitution.md` + `CLAUDE.md` |
| 1 | ACQUIRE | user's main session | [`references/acquire.md`](references/acquire.md) | `work/reference/{paper.pdf, source/ or document.md, figures/, tables/, index.json, astra.yaml, code/, code-status.yaml, code-index.md}` |
| 2 | ARCHITECT | ralph iteration | [`references/architect.md`](references/architect.md) | stub `astra.yaml` at project root (sub-analyses, inputs, outputs, narrative) |
| 3 | SPECIFY | ralph iteration | [`references/specify.md`](references/specify.md) | filled `astra.yaml` (`decisions:`, `findings:`, `prior_insights:` placeholders, anchored narrative); `targets/targets.md`; `implementation-notes.md`; `universes/baseline.yaml` |
| 4 | LITERATURE | ralph iteration | [`references/literature.md`](references/literature.md) | `astra.yaml`'s `prior_insights:` resolved with `evidence:` selectors; per-paper PDFs cached via `astra paper add` |
| 5 | IMPLEMENT | ralph iteration | [`references/implement.md`](references/implement.md) | `scripts/`, `requirements.txt`, recipes in `astra.yaml` |
| 6 | RUN | ralph iteration | [`references/run.md`](references/run.md) | `results/<universe>/<output>/` |
| 7 | COMPARE | ralph iteration | [`references/compare.md`](references/compare.md) | `comparison-report.{yaml,md}` |
| 8 | REVIEW | user's main session | [`references/review.md`](references/review.md) | `REPRODUCTION-SUMMARY.md`, `/figure-comparison` HTML, resolved `open-questions.md`, finalized reproduction outcome |

COMPARE produces a verdict plus an opportunity assessment — not just pass / fail, but where the gaps are, how much they likely matter, and how they sit relative to the constitution's fidelity intent. A subsequent iteration decides whether to spend another IMPLEMENT round (close a gap that sits below intent) or land the reproduction at its current trajectory and log the gap as an open opportunity in CLAUDE.md's Rigor section. When the constitution's `status:` flips to `closed` (typically by an iteration after COMPARE returns `pass` or after the iteration logs accepted opportunities), the loop terminates and REVIEW runs in the user's main session.

## The two pre-loop bookends

### INTERVIEW (Phase 0)

The opening interactive phase. Run it from the user's main session. Read [`references/interview.md`](references/interview.md) in full before starting.

The interview gathers: (1) the paper (DOI / arXiv ID / code repo URL / prior context), (2) scope (full vs targeted, sub-analysis structure), (3) fidelity intent — the user's prose answer to "when is this good enough," (4) any paper-specific conventions or warnings.

These get drafted into **two files** in the reproduction workdir:

- **`constitution.md`** — the ralph loop's driving document. Goal, Fidelity intent, Scope, Quality bar, Evidence (paper DOI, arXiv ID, code repo URL), Open dimensions. Starts with YAML frontmatter `status: active` so the ralph launcher accepts it. Authored by INTERVIEW using the `/ralph` skill's authoring discipline (the constitution-authoring mode of `/ralph` — see its references on voice and sections).
- **`CLAUDE.md`** — the auto-loading walk-up. Paper identity at the top, Rules (universal across reproductions; leave the template's defaults), Rigor accumulator (starts empty), Disagreements log (starts empty), Pointers (to `constitution.md`, `work/reference/`, etc.).

Templates ship in [`templates/constitution.md`](templates/constitution.md) and [`templates/CLAUDE.md`](templates/CLAUDE.md). Show the user both drafts, take corrections, refine, save.

After approval, `git init` the workdir if it isn't one already and commit both files. Then run ACQUIRE in the same session.

### ACQUIRE (Phase 1)

Two parallel sub-skill invocations:

- **`/paper-extraction <doi-or-arxiv-id>`** — produces the paper substrate at `work/reference/{paper.pdf, source/ or document.md, index.json, astra.yaml, figures/, tables/, bibliography-source.{bib,bbl}}`.
- **`/lc-from-code` in scan-only mode** against the cloned reference repo at `work/reference/code/` (after `git clone --depth 1 <url> work/reference/code`). Produces `work/reference/code-status.yaml` + `work/reference/code-index.md`.

See [`references/acquire.md`](references/acquire.md) for the full step-by-step. Both happen in your main session — no orchestration overhead, just two skill invocations that produce on-disk artifacts.

When ACQUIRE returns, commit the new substrate and launch the ralph loop (see **Launching the loop** below).

## Launching the loop

After INTERVIEW + ACQUIRE land, hand the rest of the reproduction off to a ralph loop. From the reproduction workdir:

```bash
.claude/skills/ralph/scripts/ralph constitution.md
```

(Or `--backend codex`, or pass `-- --model <id>` for a specific model. See `/ralph`'s **Launching** section for the full surface.)

The launcher detaches a tmux session named `ralph-<workdir>-constitution`. The user attaches with `tmux attach -t <session>`. Iterations start firing immediately; each runs in a fresh Claude (or Codex) session with `constitution.md` injected as the system prompt and the workdir's `CLAUDE.md` auto-loading.

The loop runs until an iteration flips `constitution.md`'s frontmatter `status:` to `closed` — typically after COMPARE returns `pass` (or user-accepted `partial`) and the iteration that runs after that survey finds nothing left to do.

Tell the user explicitly: "Launching the ralph loop in tmux session `<name>`. Attach with `tmux attach -t <name>`. Detach with the usual tmux prefix + `d`. The loop will run until the constitution closes (typically after COMPARE returns `pass`); at that point come back here and I'll run REVIEW close-out."

## Per-iteration discipline

Iterations follow the `/ralph` skill's Loop protocol — Survey → Work → Update → Exit. The per-paper specifics layered on top:

- **Survey starts with the constitution + CLAUDE.md, then the workdir.** Read the constitution to remember the goal and the fidelity intent. Read CLAUDE.md's Rigor accumulator to know where each output currently sits relative to the quality bar. Then survey the workdir against the **Workdir-as-state** table below to identify the next phase that needs work.
- **One phase per iteration is the typical shape.** Don't try to do ARCHITECT *and* SPECIFY in one iteration; the fresh-context property of the next iteration is what makes review work, and conflating phases collapses the seam. (Exceptions: small targeted fixes after COMPARE may touch multiple phases in one iteration if they're tightly coupled.)
- **Phase reference is your working spec for the iteration.** Whichever phase is next, read its `references/<phase>.md` on entry. That file carries the discipline for that phase's work (what to produce, code-as-canonical, rigor adjustment, etc.).
- **Self-review is the next iteration.** Where ARCHITECT/SPECIFY/LITERATURE/IMPLEMENT used to spawn fresh-context reviewer sub-agents per round (broken — sub-agents can't spawn sub-agents), the discipline now collapses into iteration boundaries: iteration N writes the artifact, iteration N+1 reads it fresh and reviews, iteration N+2 applies fixes if needed, until two consecutive review iterations find no fixes or a 5-iteration cap. Each iteration is fresh by construction; the no-bias property is free.
- **Parallel fan-out lives inside an iteration.** LITERATURE Haiku quote-finders, SPECIFY per-sub-analysis work, IMPLEMENT per-output work — these fan out as one-level-deep `Agent(...)` spawns inside the iteration's main session. Sub-agents can't spawn sub-agents, but an iteration *is* the main session, so it can spawn freely.
- **`AskUserQuestion` is not available inside an iteration.** Each iteration runs in a detached tmux session; the user isn't reachable interactively. Iterations append questions to `open-questions.md` with their best-judgment default applied, and the user resolves them at REVIEW close-out (back in their main session).
- **Update the accumulators in CLAUDE.md** before exit: Rigor *Current state* per output that the iteration changed; *Paper-vs-code disagreements* for any material conflict the iteration surfaced; *Open opportunities* for COMPARE-surfaced gaps.
- **Sharpen the constitution body itself** if something fundamental shifted — the user's fidelity intent reframed, a sub-analysis decomposition rethought, a quality-bar item that's now more concrete. Don't accrete amendment sections; rewrite the affected prose.

## Workdir-as-state

Each iteration's survey reads the workdir to determine what phase is next. File existence implies the phase has been done:

| Signal | Phase done |
|---|---|
| `constitution.md` + `CLAUDE.md` at workdir root, both committed | INTERVIEW |
| `work/reference/source/` (arxiv tarball) **or** `work/reference/document.md` (Docling fallback) + `work/reference/index.json` + `work/reference/astra.yaml` | ACQUIRE paper substrate |
| `work/reference/code/` (or `code-status.yaml` with `found: false`) + `work/reference/code-index.md` | ACQUIRE code substrate |
| `astra.yaml` at project root validates with empty `decisions:` / `prior_insights:` / `findings:` blocks | ARCHITECT (stub) |
| `astra.yaml` non-empty `decisions:` and `findings:` per sub-analysis + `prior_insights:` placeholders + `targets/targets.md` + `implementation-notes.md` | SPECIFY |
| `astra.yaml`'s `prior_insights:` resolved with `evidence:` selectors; `work/cited/<doi-slug>/` populated per cited paper | LITERATURE |
| recipes present in `astra.yaml` + `scripts/` + `requirements.txt` | IMPLEMENT |
| `results/<universe>/<output>/` for every output | RUN |
| `comparison-report.yaml` | COMPARE |
| `REPRODUCTION-SUMMARY.md` + `.lightcone/comparison.html` + resolved `open-questions.md` | REVIEW |

`git log --oneline` complements this — phase commits are the chronological view of what landed when, and iteration boundaries are visible in the log.

## REVIEW close-out (after the loop)

When the loop closes (the user reports back that the tmux session has exited, or `constitution.md`'s `status:` is `closed`), run REVIEW from the user's main session. See [`references/review.md`](references/review.md) for the full close-out: invoke `/figure-comparison` (mandatory) and optionally `/check-sentence-by-sentence`, walk `open-questions.md` with the user, draft `REPRODUCTION-SUMMARY.md`, propagate un-acted opportunities into CLAUDE.md, commit.

REVIEW runs in your main session because `/figure-comparison` and `/check-sentence-by-sentence` both use `AskUserQuestion`, which isn't available inside ralph iterations.

## Disciplines

**Workdir is the state.** No state machine, no resume mechanic — file existence + `git log` + `astra validate` answer "what phase am I on" deterministically. Each iteration's first move is to survey the workdir on entry against the table above.

**Code-as-canonical, with disagreements recorded.** When the original codebase is at `work/reference/code/`, every iteration that touches a sub-analysis reads relevant code on entry. Where paper and code disagree on something material (a different choice would plausibly change a numeric result the paper reports), **code is canonical** for numerics, plotting, and method — but the disagreement is recorded: as a decision option in `astra.yaml` with both alternatives preserved, and as an entry in CLAUDE.md's *Paper-vs-code disagreements* section so it's visible to every iteration and to the user at REVIEW. Stylistic / cosmetic / pure-tooling differences aren't material — note them in `implementation-notes.md` and move on. Without this discipline, iterations drift to "looks right" rather than "matches" and material disagreements get silently absorbed.

**Rigor is a trajectory toward the user's intent.** A reproduction isn't one-shot — it reaches a baseline, then accumulates. The anchor is the user's **fidelity intent**, captured in `constitution.md`'s Goal section at INTERVIEW as prose — their own words for what "good enough" looks like (e.g. *"just checking the analysis is tractable"*, *"Figure 3 must be right; the rest can stay rough"*, *"every primary and secondary target lining up within stated tolerance"*).

Each iteration translates the fidelity intent into a per-spawn tactical decision when working on an artifact-producing phase (ARCHITECT, SPECIFY, LITERATURE, IMPLEMENT). Derive how much in-iteration self-review-via-fan-out to run from the gap between where the artifact currently stands (CLAUDE.md's Rigor *Current state* — *sketch / baseline / tightened / canonical*) and what the Goal's intent says the user cares about. *Cheap:* write the artifact and exit; let the next iteration's fresh-context survey serve as the review. *Heavy:* fan out parallel reviewers as one-level-deep sub-agents inside the iteration, merge findings, apply fixes, exit. Either way, update CLAUDE.md's Rigor *Current state* so the trajectory stays honest across iterations.

The default is **sequential review via iteration boundaries** — cheaper, no fan-out, and the fresh-context property is automatic. Reach for in-iteration fan-out when the parallelism actually pays (LITERATURE with many cited papers, SPECIFY with many independent sub-analyses, IMPLEMENT with many outputs).

The *sketch / baseline / tightened / canonical* and *cheap / heavy* vocabularies are the iteration's internal scaffolding for sizing its work. The user's surface is the intent prose; the scaffolding only shows through when they ask how an iteration sized itself.

**arxiv-LaTeX-first acquisition.** When the paper is on arxiv, the source tarball is the substrate; equations, ligatures, captions, tables come through clean. PDF + Docling is a fallback for non-arxiv only.

**Use the up-to-date `astra` CLI surfaces.** When `astra validate` already does the job, call it directly. Specifically: `astra validate <file>`, `astra validate --verify-evidence`, `astra paper add`. Use whatever the current `astra --help` surfaces — don't write skill-specific wrappers.

**No synthetic data.** Unless the paper itself uses synthetic data as input, every input dataset must be real (downloaded, queried, or fetched from a real archive). The implement reference repeats this; treat it as load-bearing.

**Open-questions accumulator.** Iterations run detached and can't reach the user interactively, so questions go to `<workdir>/open-questions.md` with the iteration's best-judgment default applied. The user resolves the accumulated questions at REVIEW close-out before the reproduction closes.

## Resuming an in-flight reproduction

When the user walks back into a workdir that already has artifacts:

1. **Skip INTERVIEW** unless the user explicitly wants to revise scope (in which case edit `constitution.md` together, no re-draft from scratch).
2. **If `constitution.md`'s `status:` is `active` and the tmux session isn't running**, re-launch the ralph loop: `.claude/skills/ralph/scripts/ralph constitution.md`. The next iteration surveys the workdir and picks up wherever the prior loop left off.
3. **If `constitution.md`'s `status:` is `closed`**, the reproduction is at REVIEW. Run REVIEW close-out in your main session.
4. **If ACQUIRE substrate is incomplete**, finish ACQUIRE in your main session before launching the loop — re-spawn `/paper-extraction` and/or `/lc-from-code` against the existing partial state (both are survey-first and skip done work).

## Anti-patterns

- **Spawning a "loop manager" sub-agent inside your main session.** The whole point of the ralph loop is fresh per-iteration context; you launch the loop, the loop runs detached, you come back when it's done. No nested orchestrator.
- **Doing the long middle in your main session instead of launching the loop.** INTERVIEW and ACQUIRE belong in your session; ARCHITECT through COMPARE belong in the loop. Doing phase work in your main session burns context that doesn't get reset; the loop exists precisely to give each phase fresh context.
- **Asking an iteration to use `AskUserQuestion`.** Iterations run detached. Surface questions to `open-questions.md` with a default applied; the user resolves at REVIEW.
- **Re-implementing what `astra` already does.** If `astra validate` returns clean, don't write a separate validator. If `astra paper add` caches the PDF, don't write a separate cache.
- **Bundling phases into one iteration.** Each iteration does one phase's worth of work. Conflating phases re-creates the failure mode the loop exists to avoid: no fresh-context review between phases.
- **Accreting amendment sections in `constitution.md`.** When something fundamental shifts, *reshape* the affected prose. The chronology lives in commits; the body lives in *now*.
