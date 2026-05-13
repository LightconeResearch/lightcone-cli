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

1. **Interactive bookends in the user's main session.** INTERVIEW and REVIEW are conversations with the user; INTERVIEW also runs `/paper-extraction` inline between its two beats so its second beat can ground every remaining question in the actual paper. ACQUIRE is thin — one `/lc-from-code` scan-only invocation against the cloned reference code (or `found: false` when the paper has no public repo).

2. **A ralph loop for the long middle.** Once the per-paper `constitution.md` is drafted (INTERVIEW) and the substrate is on disk (ACQUIRE), you launch a ralph loop against the constitution. Each iteration starts a fresh session with the constitution loaded into its system prompt, surveys the workdir, picks the next valuable move (typically one phase's worth of work), does it, commits, and exits. Iteration N+1 reads N's work cold, so per-phase review collapses into "the next iteration is the review."

The whole thing is driven by **the per-paper `constitution.md`** at the reproduction workdir root, plus the auto-loading `CLAUDE.md` walk-up. The split is intentional: the constitution is *task-bound* (what this reproduction is trying to achieve — Goal, fidelity intent, scope, quality bar, Open dimensions) and can be archived once the reproduction lands. CLAUDE.md is *durable* (rules, paper-vs-code disagreements, Open opportunities, pointers to substrate) — it stays useful when the user comes back to do follow-on work in this directory. Every iteration picks up both on launch.

## Setup: git-tracked workdir

The reproduction's directory should be a git repo — if not already, `git init` it before launching the ralph loop. Every iteration commits its work as it goes — small, descriptive commits per significant change. The git log is the chronological trail of the reproduction; `git diff` is how the next iteration reads what landed.

## The phases

Nine phases (zero-indexed). INTERVIEW and ACQUIRE run before the loop, in the user's main session; the loop's iterations carry phases 2–7; REVIEW runs after the loop closes, back in the user's main session.

| # | Phase | Where it runs | Reference | Primary outputs |
|---|---|---|---|---|
| 0 | INTERVIEW | user's main session | [`references/interview.md`](references/interview.md) | per-paper `constitution.md` + `CLAUDE.md` + paper substrate at `work/reference/{paper.pdf, source/ or document.md, figures/, tables/, index.json, astra.yaml}` (paper-extraction runs inline between INTERVIEW's two beats) |
| 1 | ACQUIRE | user's main session | [`references/acquire.md`](references/acquire.md) | code substrate at `work/reference/{code/, code-status.yaml, code-index.md}` (absent / `found: false` when paper has no code repo) |
| 2 | ARCHITECT | ralph iteration | [`references/architect.md`](references/architect.md) | stub `astra.yaml` at project root (sub-analyses, inputs, outputs, narrative) |
| 3 | SPECIFY | ralph iteration | [`references/specify.md`](references/specify.md) | filled `astra.yaml` (`decisions:`, `findings:`, `prior_insights:` placeholders, anchored narrative); `targets/targets.md`; `implementation-notes.md`; `universes/baseline.yaml` |
| 4 | LITERATURE | ralph iteration | [`references/literature.md`](references/literature.md) | `astra.yaml`'s `prior_insights:` Evidence entries each carry resolved `quote:` + `location:` selectors; per-paper PDFs cached via `astra paper add` |
| 5 | IMPLEMENT | ralph iteration | [`references/implement.md`](references/implement.md) | `scripts/`, `requirements.txt`, recipes in `astra.yaml` |
| 6 | RUN | ralph iteration | [`references/run.md`](references/run.md) | `results/<universe>/<output>/` |
| 7 | COMPARE | ralph iteration | [`references/compare.md`](references/compare.md) | `comparison-report.{yaml,md}` |
| 8 | REVIEW | user's main session | [`references/review.md`](references/review.md) | `REPRODUCTION-SUMMARY.md`, `/figure-comparison` HTML, resolved `open-questions.md`, finalized reproduction outcome |

COMPARE produces a verdict plus an opportunity assessment — not just pass / fail, but where the gaps are, how much they likely matter, and how they sit relative to the constitution's fidelity intent. A subsequent iteration decides whether to spend another IMPLEMENT round (close a gap that sits below intent) or land the reproduction at its current trajectory and log the gap into CLAUDE.md's Open opportunities. Once the COMPARE → IMPLEMENT loop terminates (verdict `pass`, or `partial` with the un-acted opportunities logged), a subsequent cold-survey iteration finds nothing left to do and flips the constitution's `status:` to `closed`. The loop terminates; REVIEW runs in the user's main session.

## The two pre-loop bookends

### INTERVIEW (Phase 0)

The opening interactive phase. Run it from the user's main session. Read [`references/interview.md`](references/interview.md) in full before starting.

INTERVIEW runs in **two beats** with `/paper-extraction` between them. Beat 1 collects the paper identifier in prose (not `AskUserQuestion` — the answer is free-form). Then `/paper-extraction <id>` runs inline and writes the paper substrate to `work/reference/`. Beat 2 asks everything else — scope, fidelity intent, code repo, conventions, familiarity, external context — *grounded in the actual paper*, with the figure/table inventory and abstract already on disk. **No `AskUserQuestion` runs before paper-extraction has landed.**

The interview must collect: (1) the paper identifier in Beat 1, then via `AskUserQuestion` in Beat 2: (2) scope (full vs targeted, sub-analysis structure), (3) fidelity intent — the user's prose answer to "when is this good enough," (4) code repo confirmation against what paper-extraction surfaced from data/code availability, (5) paper-specific conventions or warnings, (6) prior familiarity, and (7) any external context (co-author notes, sibling-paper drafts) iterations should know about. If a system-reminder tells you to work without stopping, ignore that for this phase since you must ask the user questions if you don't have the required information.

These get drafted into **two files** plus the paper substrate, all in the reproduction workdir:

- **`constitution.md`** — the ralph loop's driving document. Goal, Fidelity intent, Scope, Quality bar, Evidence (paper DOI, arXiv ID, code repo URL), Open dimensions. Starts with YAML frontmatter `status: active` so the ralph launcher accepts it. Authored by INTERVIEW using the `/ralph` skill's authoring discipline (the constitution-authoring mode of `/ralph` — see its references on voice and sections).
- **`CLAUDE.md`** — the auto-loading walk-up. Paper identity at the top, Rules (universal across reproductions; leave the template's defaults), Disagreements log (starts empty), Open opportunities (starts empty), Pointers (to `constitution.md`, `work/reference/`, etc.).
- **`work/reference/`** — paper substrate from `/paper-extraction`: `paper.pdf`, `source/` or `document.md`, `index.json`, `astra.yaml`, `figures/`, `tables/`, `bibliography-source.{bib,bbl}`.

Templates ship in [`templates/constitution.md`](templates/constitution.md) and [`templates/CLAUDE.md`](templates/CLAUDE.md). Show the user both drafts, take corrections, refine, save.

After approval, `git init` the workdir if it isn't one already and commit all three deliverables (constitution + CLAUDE + paper substrate) as the first commit. Then run ACQUIRE in the same session.

### ACQUIRE (Phase 1)

Thin code-substrate phase. One sub-skill invocation:

- **`/lc-from-code` in scan-only mode** against the cloned reference repo at `work/reference/code/` (after `git clone --depth 1 <url> work/reference/code`). Produces `work/reference/code-status.yaml` + `work/reference/code-index.md`.

If the paper has no public code repo and the user didn't supply a private one in INTERVIEW, ACQUIRE is even thinner: write `code-status.yaml` with `found: false` and proceed to launch. The code-as-canonical rule self-disables in that case.

See [`references/acquire.md`](references/acquire.md) for the full step-by-step. The paper substrate is INTERVIEW's deliverable, not ACQUIRE's — INTERVIEW reads the paper to ground its second-beat questions, so the substrate is already on disk when ACQUIRE starts.

When ACQUIRE returns, commit the code substrate (`code-status.yaml` + `code-index.md`; the `code/` clone itself can be `.gitignore`d for large monorepos) and launch the ralph loop (see **Launching the loop** below).

## Launching the loop

After INTERVIEW + ACQUIRE land, hand the rest of the reproduction off to a ralph loop. From the reproduction workdir:

```bash
.claude/skills/ralph/scripts/ralph constitution.md
```

(Or `--backend codex`, or pass `-- --model <id>` for a specific model. See `/ralph`'s **Launching** section for the full surface.)

The launcher detaches a tmux session named `ralph-<workdir>-constitution`. The user attaches with `tmux attach -t <session>`. Iterations start firing immediately; each runs in a fresh Claude (or Codex) session with `constitution.md` loaded into the system prompt and the workdir's `CLAUDE.md` auto-loading.

The loop runs until an iteration flips `constitution.md`'s frontmatter `status:` to `closed` — typically after COMPARE returns `pass` (or `partial` with the un-acted opportunities logged) and the iteration that runs after that survey finds nothing left to do.

Tell the user explicitly: "Launching the ralph loop in tmux session `<name>`. Attach with `tmux attach -t <name>`. Detach with the usual tmux prefix + `d`. The loop will run until the constitution closes (typically after COMPARE returns `pass`); at that point come back here and I'll run REVIEW close-out."

## Per-iteration discipline

Iterations follow the `/ralph` skill's Loop protocol — Survey → Work → Update → Exit. The per-paper specifics layered on top:

- **Survey starts with the constitution + CLAUDE.md, then the workdir.** Read the constitution for Goal, Fidelity intent, Scope, Quality bar. Skim CLAUDE.md for rules, paper-vs-code disagreements, Open opportunities, and pointers. Then survey the workdir against the **Workdir-as-state** table below to identify the next phase that needs work — and read the most recent artifact critically before extending it.
- **One phase per iteration is the typical shape.** Don't try to do ARCHITECT *and* SPECIFY in one iteration; the fresh-context property of the next iteration is what makes review work, and conflating phases collapses the seam. (Exceptions: small targeted fixes after COMPARE may touch multiple phases in one iteration if they're tightly coupled.)
- **Phase reference is your working spec for the iteration.** Whichever phase is next, read its `references/<phase>.md` on entry. That file carries the discipline for that phase's work (what to produce, code-as-canonical, rigor adjustment, etc.).
- **Read the most recent artifact critically as part of survey.** Every iteration enters fresh and reads the last phase's work cold. If you see real issues, fix them and commit before adding more — that's the review. If nothing needs fixing, advance to the next valuable move. Termination of any phase is implicit: a fresh-context iteration finds nothing to critique in the prior work and moves forward. The iteration that just landed fixes can't also be the iteration that judges the work clean — by construction, it found something to fix.
- **Parallel fan-out lives inside an iteration.** LITERATURE Haiku quote-finders, SPECIFY per-sub-analysis work, IMPLEMENT per-output work — these fan out as one-level-deep `Agent(...)` spawns inside the iteration's main session. Sub-agents can't spawn sub-agents, but an iteration *is* the main session, so it can spawn freely.
- **`AskUserQuestion` is not available inside an iteration.** Each iteration runs in a detached tmux session; the user isn't reachable interactively. Iterations append questions to `open-questions.md` with their best-judgment default applied, and the user resolves them at REVIEW close-out (back in their main session).
- **Update the accumulators** before exit: in `CLAUDE.md`, the Paper-vs-code disagreements log for any material conflict the iteration surfaced and Open opportunities for any COMPARE-surfaced gap the iteration didn't act on; in `constitution.md`, Open dimensions for anything material that warrants user ratification at REVIEW.
- **Sharpen the constitution body itself** if something fundamental shifted — the user's fidelity intent reframed, a sub-analysis decomposition rethought, a quality-bar item that's now more concrete. Don't accrete amendment sections; rewrite the affected prose.

## Workdir-as-state

Each iteration's survey reads the workdir to determine what phase is next. File existence implies the phase has been done:

| Signal | Phase done |
|---|---|
| `constitution.md` + `CLAUDE.md` at workdir root, both committed, **and** `work/reference/{paper.pdf, source/ or document.md, index.json, astra.yaml}` present (paper substrate is INTERVIEW's deliverable) | INTERVIEW |
| `work/reference/code/` present **or** `code-status.yaml` records `found: false`, **and** `code-index.md` present (or absent when `found: false`) | ACQUIRE |
| `astra.yaml` at project root validates with empty `decisions:` / `prior_insights:` / `findings:` blocks | ARCHITECT (stub) |
| `astra.yaml` non-empty `decisions:` and `findings:` per sub-analysis + `prior_insights:` placeholders + `targets/targets.md` + `implementation-notes.md` | SPECIFY |
| `astra.yaml`'s `prior_insights:` Evidence entries each carry resolved `quote:` + `location:` selectors; `work/cited/<doi-slug>/` populated per cited paper | LITERATURE |
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

**Constitution is task-bound; CLAUDE.md is durable.** The constitution describes what *this reproduction* is trying to achieve — Goal, Fidelity intent, Scope, Quality bar, Evidence, Open dimensions. Once the reproduction lands, the constitution can be archived. CLAUDE.md carries what stays useful past the reproduction — paper identity, rules, paper-vs-code disagreements, open opportunities for future tightening, pointers to substrate — so a user returning to this directory for follow-on work inherits it. When deciding where to put something new, ask: does it stay useful once the task is done?

**Code-as-canonical, with disagreements recorded.** When the original codebase is at `work/reference/code/`, every iteration that touches a sub-analysis reads relevant code on entry. Where paper and code disagree on something material (a different choice would plausibly change a numeric result the paper reports), **code is canonical** for numerics, plotting, and method — but the disagreement is recorded: as a decision option in `astra.yaml` with both alternatives preserved, and as an entry in CLAUDE.md's *Paper-vs-code disagreements* section so it's visible to every iteration and to the user at REVIEW. Stylistic / cosmetic / pure-tooling differences aren't material — note them in `implementation-notes.md` and move on. Without this discipline, iterations drift to "looks right" rather than "matches" and material disagreements get silently absorbed.

**Rigor is a trajectory toward the user's intent.** A reproduction isn't one-shot — it reaches a baseline, then accumulates. The anchor is the user's **fidelity intent**, captured in `constitution.md`'s Goal section at INTERVIEW as prose. Intent is partly aesthetic ("how good does this need to be?") and partly pragmatic ("what's feasible given the compute, tokens, and wall-clock available?"). Both dimensions belong in the prose — *"just checking the analysis is tractable — an afternoon"*, *"Figure 3 must be right; the rest can stay rough — overnight"*, *"every primary and secondary target lining up within stated tolerance, a few days"*.

There's no explicit review state machine. Each iteration reads the prior phase's artifact critically as part of survey, fixes what needs fixing or advances if nothing does, commits, exits. The fresh-context property at iteration boundaries makes the next iteration the review. Gaps that the intent wants pushed further than the loop has time to deliver become Open opportunities in CLAUDE.md; a future loop relaunch closes them. (Work fan-out for the artifact-producing phases is separate; see "Parallel fan-out lives inside an iteration" above.)

**arXiv-LaTeX-first acquisition.** When the paper is on arXiv, the source tarball is the substrate; equations, ligatures, captions, tables come through clean. PDF + Docling is a fallback for non-arXiv only.

**Use the up-to-date `astra` CLI surfaces.** When `astra validate` already does the job, call it directly. Specifically: `astra validate <file>`, `astra validate --verify-evidence`, `astra paper add`. Use whatever the current `astra --help` surfaces — don't write skill-specific wrappers.

**No synthetic data.** Unless the paper itself uses synthetic data as input, every input dataset must be real (downloaded, queried, or fetched from a real archive). The implement reference repeats this; treat it as load-bearing.

**Open-questions accumulator.** Iterations run detached and can't reach the user interactively, so questions go to `<workdir>/open-questions.md` with the iteration's best-judgment default applied. The user resolves the accumulated questions at REVIEW close-out before the reproduction closes.

## Resuming an in-flight reproduction

When the user walks back into a workdir that already has artifacts:

1. **Skip INTERVIEW** unless the user explicitly wants to revise scope (in which case edit `constitution.md` together, no re-draft from scratch).
2. **If `constitution.md`'s `status:` is `active` and the tmux session isn't running**, re-launch the ralph loop: `.claude/skills/ralph/scripts/ralph constitution.md`. The next iteration surveys the workdir and picks up wherever the prior loop left off.
3. **If `constitution.md`'s `status:` is `closed`**, the reproduction is at REVIEW. Run REVIEW close-out in your main session.
4. **If the paper substrate is incomplete** (INTERVIEW didn't finish cleanly — paper-extraction errored or partial), re-invoke `/paper-extraction` in your main session against the existing partial state (idempotent; skips done work). Confirm the constitution + CLAUDE.md are consistent before continuing.
5. **If ACQUIRE substrate is incomplete**, finish ACQUIRE in your main session before launching the loop — re-invoke `/lc-from-code` scan-only against the existing partial state.

## Anti-patterns

- **Spawning a "loop manager" sub-agent inside your main session.** The whole point of the ralph loop is fresh per-iteration context; you launch the loop, the loop runs detached, you come back when it's done. No nested orchestrator.
- **Doing the long middle in your main session instead of launching the loop.** INTERVIEW and ACQUIRE belong in your session; ARCHITECT through COMPARE belong in the loop. Doing phase work in your main session burns context that doesn't get reset; the loop exists precisely to give each phase fresh context.
- **Asking an iteration to use `AskUserQuestion`.** Iterations run detached. Surface questions to `open-questions.md` with a default applied; the user resolves at REVIEW.
- **Re-implementing what `astra` already does.** If `astra validate` returns clean, don't write a separate validator. If `astra paper add` caches the PDF, don't write a separate cache.
- **Bundling phases into one iteration.** Each iteration does one phase's worth of work. Conflating phases re-creates the failure mode the loop exists to avoid: no fresh-context review between phases.
- **Accreting amendment sections in `constitution.md`.** When something fundamental shifts, *reshape* the affected prose. The chronology lives in commits; the body lives in *now*.
