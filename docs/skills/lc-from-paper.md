# /lc-from-paper

Reproduce a published scientific paper as a complete ASTRA project. The
task is too large for one context window, so the skill splits it into
**two interactive bookends in the user's main session** wrapping **one
autonomous Workflow** in between. ORIENT → PLAN runs in the main session
— figuring out what the user wants, standing up the paper and code
substrate, and drafting a prose reproduction plan that's gated through
**plan mode**. On approval, the skill launches the reproduce-paper
Workflow (the multi-agent Workflow primitive — `agent()` / `parallel()`
/ `pipeline()` orchestration with schema-validated structured output and
explicit verify phases), which carries the autonomous middle —
ARCHITECT → SPECIFY ∥ LITERATURE → IMPLEMENT → RUN → VERIFY → REVIEW —
and hands a review summary back. CLOSE-OUT then returns to the user's
main session.

`/lc-from-paper` is the entry point of the paper-reproduction bundle.
Sibling skills ([`paper-extraction`](paper-extraction.md),
[`narrative`](narrative.md), [`figure-comparison`](figure-comparison.md),
[`check-sentence-by-sentence`](check-sentence-by-sentence.md)) live in
the same plugin and are invoked by role across the phases.

Source: [`claude/lightcone/skills/lc-from-paper/SKILL.md`](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/lc-from-paper/SKILL.md).

## Architecture

Two interactive bookends, one autonomous Workflow.

```
  ┌─ ORIENT → PLAN ──────────────────────────────────  main session (interactive)
  │   extract (minimal) · interview (fidelity intent = STOPPING CRITERION) ·
  │   lc-from-code scan · draft the PLAN (scope · intent · decomposition sketch) →
  │   PLAN MODE → on approval, launch the Workflow
  │
  ├─ reproduce_workflow.js ──────────────────────────  Workflow (autonomous middle)
  │   ARCHITECT              realize the plan → astra.yaml skeleton + targets ledger
  │   SPECIFY ∥ LITERATURE   pipeline per sub-analysis (no barrier)
  │   IMPLEMENT              parallel per output
  │   RUN                    lc run over the Snakemake DAG
  │   VERIFY                 a test per replication target; run → fix → rerun
  │   REVIEW                 synthesize → report.html + summary back
  │
  └─ CLOSE-OUT ──────────────────────────────────────  main session (interactive)
      figure-comparison · check-sentence-by-sentence · walk open-questions · finalize
```

1. **Interactive bookends in the user's main session.** ORIENT → PLAN
   and CLOSE-OUT are conversations with the user. ORIENT runs in stages
   — ask for the paper, run `/paper-extraction` inline, interview
   (grounded in the paper), clone the code and run `/lc-from-code`
   scan-only (if a repo exists), then draft a prose `PLAN.md` +
   `CLAUDE.md` from the full paper-plus-code context for the user to
   approve in plan mode.

2. **An autonomous Workflow for the heavy middle.** Once the plan is
   approved and committed, `/lc-from-paper` launches the reproduce-paper
   Workflow from the workdir. The Workflow is a deterministic
   orchestration script — `agent()` / `parallel()` / `pipeline()` over
   fresh subagent contexts — that fans out per sub-analysis, per output,
   per cited paper, and per replication target. Workers return
   schema-validated structured output; a single barrier merge folds each
   phase's results into `astra.yaml` (one writer, no concurrent-edit
   conflict). The orchestration script holds no work product, so context
   stays bounded; explicit verify phases give review by design rather
   than review by accident.

## Why a Workflow, not a loop

A paper reproduction is structurally a *fan-out with per-claim
verification* — per sub-analysis, per output, per cited paper, per
replication target. That is exactly the Workflow primitive's home shape.
The Workflow gives bounded worker contexts (the orchestration script
carries nothing; every `agent()` is fresh; results return compact) and
explicit adversarial verify phases, both better than an ad-hoc loop
could. The repo's [`citation-audit`](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/citation-audit/SKILL.md)
skill is the precedent: the LITERATURE phase here *is* that fan-out →
verify → synthesize spine.

## Bookend A — ORIENT → PLAN

ORIENT acquires the paper, interviews the user, and scans the code, then
drafts a human-readable plan; it does **not** author the `astra.yaml`
skeleton or the targets ledger — that is the Workflow's ARCHITECT phase.
It runs in stages, each grounded in what the earlier stages produced:

1. **Ask for the paper** in prose (the answer is free-form: arXiv ID,
   DOI, or PDF path). No `AskUserQuestion` here — it's the wrong shape
   for a free-form string.
2. **Run `/paper-extraction <id>` inline** and read the substrate it
   produced — index.json, abstract, conclusions, data/code
   availability. Minimal — just enough to ground the interview.
3. **Interview the user** with `AskUserQuestion` for scope, fidelity
   intent, code repo confirmation, paper-specific conventions, prior
   familiarity, and external context — each question referencing the
   paper's actual figures, claims, and structure. The fidelity-intent
   question is load-bearing: *it is the Workflow's stopping criterion.*
   "An afternoon's sanity check," "the headline within stated
   uncertainty overnight," and "every target lined up, no deadline" each
   tell VERIFY how many fix rounds to spend. Pin it concretely against
   the paper's actual headline numbers.
4. **Clone the reference code and run `/lc-from-code` scan-only** (skip
   cleanly when no public code repo exists). The scan produces
   `work/reference/code-index.md` — the Workflow's code surface.
5. **Draft `PLAN.md` + `CLAUDE.md`** — both files now informed by paper
   *and* code substrate. `PLAN.md` is the prose contract the user
   approves: Goal, fidelity intent + stopping criterion, Scope (in/out),
   a one-line-per-target **Targets sketch**, a *prose* **Decomposition
   sketch** (one analysis or staged? — grounded in the code scan), and
   Evidence. `CLAUDE.md` is the lean auto-loading walk-up: paper
   identity, rules, fidelity intent, pointers. This is the prose
   contract — *not* the `astra.yaml` skeleton or the formal ledger; the
   Workflow's ARCHITECT realizes those from the plan.
6. **Plan mode is the launch gate.** Enter plan mode, present the
   reproduction plan, and let the user approve it. Approval is the
   single gate before the autonomous middle takes over — treat it as the
   one editorial pass that shapes the entire reproduction. On approval:
   commit `PLAN.md` + `CLAUDE.md` + the full `work/reference/` substrate
   as the first commit, then launch the Workflow.

## Launching the Workflow

After the plan is approved and committed, `/lc-from-paper` launches the
reproduce-paper Workflow from the reproduction workdir, passing the
fidelity-intent prose from the interview as the governing parameter:

```js
Workflow({
  scriptPath: '.claude/skills/lc-from-paper/reproduce_workflow.js',
  args: { workdir: '.', intent: '<the fidelity-intent prose from the interview>' }
})
```

[`reproduce_workflow.js`](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/lc-from-paper/reproduce_workflow.js)
ships the *shape* — the agent adapts the schemas, per-phase contracts,
and model tier per paper. It runs in the background and notifies on
completion; its return value carries the review summary, the
`report.html` path, the per-target verify results, and any open
questions. That return is the input to the close-out.

## Workflow phases

Each phase reads its contract from `references/<phase>.md` (the Workflow
points its agents at the file rather than inlining a giant prompt). The
shapes:

| Phase | Fan-out unit | Parallelism | Gate / verify |
|-------|--------------|-------------|----------------|
| **ARCHITECT** | — (holistic) | single agent | `astra validate` — realize the plan into the `astra.yaml` skeleton + `targets/targets.md` ledger |
| **SPECIFY** | per sub-analysis | `pipeline` (∥ literature) | `astra validate` — decisions, findings, citation placeholders, anchored narrative |
| **LITERATURE** | per cited paper | pipelined after each specify | `astra validate --verify-evidence` — `prior_insights:` evidence each carries resolved `quote:` + `location:` |
| **IMPLEMENT** | per output | `parallel` | `astra validate` + dry-run — `scripts/<output>.py`, `requirements.txt`, recipes |
| **RUN** | — (shared DAG) | sequential | `lc status` all-`ok` — `lc run` over the Snakemake DAG |
| **VERIFY** | per replication target | tests ∥, fix-loop careful | the generated tests themselves (per-paper) |
| **REVIEW** | — | single synthesizer | — `report.html` + a summary returned to the main session |

**SPECIFY ∥ LITERATURE pipeline.** Each sub-analysis is specified
(decisions, findings, citation placeholders), then its citations are
resolved — as a `pipeline`, so sub-analysis A's literature runs while B
is still being specified. SPECIFY and LITERATURE agents *return
structured output*; a single barrier merge folds every sub-analysis's
result into `astra.yaml` (one writer, no concurrent-edit conflict) and
runs `astra validate --verify-evidence`.

**IMPLEMENT.** One worker per output, in `parallel` — scripts are
disjoint files (`scripts/<output>.py`), so they write without conflict.
Each returns its `recipe`, and a barrier merge folds the recipes into
`astra.yaml`.

**RUN.** One agent runs `lc run --universe baseline` over the Snakemake
DAG and shepherds it to completion (long cluster jobs get monitored).
`lc status` all-`ok` is the deterministic gate.

### VERIFY — the convergence engine

The one phase worth naming at this level. We cannot pre-write a gate for
a specific paper's claims — the claims *are* the paper — so the Workflow
**generates** it. For every replication target VERIFY writes a test
encoding the claim, then iterates `run → fix the implementation → rerun
the full suite` until the tests pass *or* the **fidelity intent** says
stop. TDD for a paper: the claims are the spec, the tests are the gate,
green is the goal. The fix loop targets the implementation, never the
test, and is bounded by the fidelity intent — a reproduction asked for
"an afternoon" doesn't burn a day of fix rounds.

## Bookend B — CLOSE-OUT

When the Workflow returns, `/lc-from-paper` runs the close-out from the
user's main session. It uses skills that need `AskUserQuestion` (so they
cannot run inside the Workflow):

- **`/figure-comparison`** (mandatory) — side-by-side original vs.
  reproduced figures, tables, and numerics, building on the Workflow's
  `report.html`.
- **`/check-sentence-by-sentence`** (opt-in) — audit paper claims
  against code locations.
- **Walk `open-questions.md`** with the user — the Workflow's unresolved
  decisions (paper-vs-code disagreements it adjudicated code-canonical,
  citations with no supporting quote, targets that landed below intent).
  Resolve, finalize, commit.

## Resuming an in-flight reproduction

Workdir state is the resume mechanic — no separate state machine. File
existence, `git log`, `astra validate`, and `lc status` answer "where
are we" deterministically:

- **No `PLAN.md`** → ORIENT hasn't run; start at Bookend A.
  `/paper-extraction` and `/lc-from-code` are survey-first and skip done
  work, so a partial `work/reference/` resumes cleanly.
- **`PLAN.md` committed, the Workflow hasn't finished** → re-launch the
  Workflow; it is journal-resumable and its phases are idempotent
  against on-disk state (filled `astra.yaml`, materialized `results/`,
  written tests). Same script + args → cached prefix, live tail.
- **Workflow returned, no close-out yet** → run Bookend B.

## Disciplines

- **Fidelity intent is the stopping criterion.** Captured at interview,
  carried in `args.intent` + `PLAN.md`, read by VERIFY to size its
  fix-loop. This is the spine of the autonomy model — the user said how
  hard to push, so the middle doesn't need them.
- **Code-as-canonical, with disagreements recorded.** When the reference
  code exists, every phase that touches a sub-analysis reads the
  relevant code. Where paper and code disagree materially, code is
  canonical for numerics and method — but the disagreement is preserved
  (both options in `astra.yaml`, a note in `CLAUDE.md`'s disagreements
  log, surfaced at close-out).
- **Single-writer merge.** Parallel phase workers return structured
  output; one barrier step folds it into `astra.yaml`. Never two agents
  editing the spec at once — concurrent writes corrupt it.
- **No synthetic data.** Unless the paper itself uses synthetic input,
  every input is real — downloaded, queried, or fetched from a real
  archive.
- **arXiv LaTeX first.** When the paper (or a cited paper) is on arXiv,
  the source tarball is the substrate; equations, captions, and tables
  come through clean. PDF + Docling is the non-arXiv fallback.
- **Open questions for the autonomous middle.** The Workflow runs
  detached from the user, so `AskUserQuestion` isn't available inside it.
  Questions it can't resolve get a best-judgment default and a line in
  `open-questions.md`; the user resolves them at CLOSE-OUT.

## Anti-patterns

- Resurrecting a detached per-phase loop. The middle is a Workflow — no
  detached tmux session, no per-phase fresh-session iteration, no "loop
  manager" sub-agent.
- Reading papers in the orchestrator's context. The fan-out's whole
  value is bounded workers. Spawn an agent to read a cited paper; don't
  open it in the main session or the Workflow script.
- Pre-writing a paper-specific gate. You can't — the claims are the
  paper. VERIFY *generates* the tests per target. The skill ships the
  loop, not the gate.
- Skipping plan mode. Plan approval is the one human gate before the
  autonomous middle. "Drafts written → launch" skips the editorial pass
  that shapes everything.
- Concurrent `astra.yaml` writes. Workers return structured output; one
  merge step writes.

## Related

- [Bundle README](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/README.md)
  — why the bundle is co-located rather than a separate plugin install.
- [`reproduce_workflow.js`](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/lc-from-paper/reproduce_workflow.js)
  — the Workflow template the skill launches; adapt per paper.
- [`/paper-extraction`](paper-extraction.md) — ORIENT Stage 2's
  acquisition path; also invoked per cited paper by the LITERATURE phase.
- [`/narrative`](narrative.md) — ARCHITECT's structural narrative and
  SPECIFY's anchored content narrative.
- [`/figure-comparison`](figure-comparison.md) — CLOSE-OUT (mandatory)
  and also user-invokable.
- [`/check-sentence-by-sentence`](check-sentence-by-sentence.md) —
  CLOSE-OUT (opt-in) and also user-invokable.
