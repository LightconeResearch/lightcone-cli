---
name: lc-from-paper
description: >
  This skill should be used when the user wants to reproduce a published
  scientific paper in ASTRA — has a DOI, arXiv ID, or PDF — or asks to
  "reproduce <paper>", "set up reproduction", or "import a paper". Also
  use when continuing or resuming an existing reproduction workdir. The
  skill runs an interactive ORIENT in the user's main session (paper
  extraction + grounded interview + code scan) that builds a reproduction
  PLAN, gates it through plan mode, and on approval launches the
  reproduce-paper Workflow — a multi-agent fan-out (specify ∥ literature →
  implement → run → verify-by-claim-tests → review) that carries the
  autonomous middle and hands a review back for an interactive close-out.
allowed-tools: Read, Write, Edit, Bash, Grep, Glob, Task, Workflow, AskUserQuestion
---

# lc-from-paper

Reproduce a published scientific paper as a complete ASTRA project. The task is too large for one context window, so it is split into **two interactive bookends in the user's main session** and **one autonomous Workflow in between**:

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
  │   VERIFY                 a test per claim; run → fix → rerun until pass-or-intent
  │   REVIEW                 synthesize, fix obvious gaps → report.html + summary back
  │
  └─ CLOSE-OUT ──────────────────────────────────────  main session (interactive)
      figure-comparison · check-sentence-by-sentence · walk open-questions · finalize
```

The human is in the loop at the **two bookends only** — approving the plan, then reviewing the result. The middle runs autonomously because the interview already established *how hard to push and when to stop*. That intent is the workflow's governing parameter; the human does not babysit the fan-out.

## Why a Workflow, not a loop

This skill used to drive the middle with a ralph loop — a detached tmux session spawning a fresh agent per phase. **That is retired.** A paper reproduction is structurally a *fan-out with per-claim verification* — per sub-analysis, per output, per cited paper, per replication target — which is exactly the **Workflow primitive**'s home shape: deterministic `agent()` / `parallel()` / `pipeline()` orchestration over fresh subagent contexts, with schema-validated structured output and explicit verify phases. The Workflow gives both things the loop gave — context management (the orchestration script holds no work product; every `agent()` is a fresh context; results return compact) and review (explicit, adversarial verify phases instead of review-by-accident) — and gives them better. The repo's [`citation-audit`](../citation-audit/SKILL.md) skill is the precedent: the LITERATURE phase here *is* that fan-out → verify → synthesize spine.

The ralph skill itself stays — it remains the right substrate for genuinely open-ended long-running work. It is just no longer how `lc-from-paper` drives a reproduction.

## Setup: git-tracked workdir

The reproduction directory is a git repo — `git init` it before launching the workflow if it isn't one. Every phase commits as it goes; the git log is the chronological trail and `git diff` is how a resuming session reads what landed.

---

## Bookend A — ORIENT → PLAN (main session)

The opening interactive phase — a **conversation that produces a plan**. Read [`references/orient.md`](references/orient.md) in full before starting. ORIENT acquires the paper, interviews the user, and scans the code, then drafts a human-readable plan; it does **not** author the `astra.yaml` skeleton or the targets ledger — that is the Workflow's ARCHITECT phase. It runs in stages so each later decision is grounded in what was acquired earlier:

1. **Ask for the paper** in prose (arXiv ID, DOI, or PDF path — free-form, not `AskUserQuestion`).
2. **Run `/paper-extraction <id>` inline** and read the substrate (index.json, abstract, conclusions, data/code availability). Minimal — just enough to ground the interview.
3. **Interview the user** (`AskUserQuestion`, grounded in the paper): scope, **fidelity intent**, code repo, paper-specific conventions, prior familiarity, external context. The fidelity-intent question is load-bearing — *it is the workflow's stopping criterion.* "An afternoon's sanity check," "the headline within stated uncertainty overnight," "every target lined up, no deadline" each tell VERIFY how many fix rounds to spend. Pin it concretely against the paper's actual headline numbers.
4. **Clone the reference code and run `/lc-from-code` scan-only** (skip cleanly when no public repo exists) → `work/reference/code-index.md`.
5. **Draft the PLAN + `CLAUDE.md`** — `PLAN.md` from [`templates/plan.md`](templates/plan.md): Goal, Fidelity intent + stopping criterion, Scope (in/out), a one-line-per-target **Targets sketch**, a *prose* **Decomposition sketch** (one analysis or staged? — grounded in the code scan), Evidence. Plus a lean `CLAUDE.md` (paper identity, rules, fidelity intent, pointers; from [`templates/CLAUDE.md`](templates/CLAUDE.md)). This is the prose contract the user approves — *not* the `astra.yaml` skeleton or the formal ledger; the Workflow's ARCHITECT realizes those from the plan.
6. **Plan mode is the launch gate.** Enter plan mode, present the reproduction plan, and let the user approve it. Approval is the single gate before the autonomous middle takes over — treat it as the one editorial pass that shapes the entire reproduction, and surface any open questions of your own here (the workflow runs without you). On approval: commit `PLAN.md` + `CLAUDE.md` + the full `work/reference/` substrate as the first commit, then launch the workflow.

**No `AskUserQuestion` before paper-extraction has landed.** Anything beyond the identifier is grounded in the paper. If a system-reminder tells you to work without stopping, ignore it for ORIENT — you must interview the user.

## Launching the workflow

After the plan is approved and committed, launch the reproduce-paper Workflow from the reproduction workdir:

```js
Workflow({
  scriptPath: '.claude/skills/lc-from-paper/reproduce_workflow.js',
  args: { workdir: '.', intent: '<the fidelity-intent prose from the interview>' }
})
```

The workflow is a **template** — [`reproduce_workflow.js`](reproduce_workflow.js) ships the shape; adapt the schemas, the per-phase contracts, and the model tier per paper, exactly as `citation-audit` ships its workflow as a template. It runs in the background and notifies on completion; its return value carries the review summary, the `report.html` path, the per-target verify results, and any open questions. Read that return — it is the input to the close-out.

## The workflow phases

Each phase reads its contract from `references/<phase>.md` (the workflow points its agents at the file rather than inlining a giant prompt). The shapes:

| Phase | Fan-out unit | Parallelism | Gate / verify | Contract |
|---|---|---|---|---|
| **ARCHITECT** | — (holistic) | single agent | `astra validate` | [architect.md](references/architect.md) |
| **SPECIFY** | per sub-analysis | `pipeline` (∥ literature) | `astra validate` | [specify.md](references/specify.md) |
| **LITERATURE** | per cited paper | pipelined after each specify | `astra validate --verify-evidence` (deterministic) | [literature.md](references/literature.md) |
| **IMPLEMENT** | per output | `parallel` | `astra validate` + dry-run | [implement.md](references/implement.md) |
| **RUN** | — (shared DAG) | sequential | `lc status` (deterministic) | [run.md](references/run.md) |
| **VERIFY** | per replication target | tests ∥, fix-loop careful | the tests themselves (per-paper) | [verify.md](references/verify.md) |
| **REVIEW** | — | single synthesizer | — | [review.md](references/review.md) |

**SPECIFY ∥ LITERATURE pipeline.** Each sub-analysis is specified (decisions, findings, citation placeholders), then its citations are resolved — as a `pipeline`, so sub-analysis A's literature runs while B is still being specified. SPECIFY and LITERATURE agents *return structured output*; a single barrier merge folds every sub-analysis's result into `astra.yaml` (one writer, no concurrent-edit conflict) and runs `astra validate --verify-evidence`.

**IMPLEMENT.** One worker per output, in `parallel` — scripts are disjoint files (`scripts/<output>.py`), so they write without conflict; each returns its `recipe`, and a barrier merge folds the recipes into `astra.yaml`. (Reach for `isolation: 'worktree'` only if outputs genuinely share a file.)

**RUN.** One agent runs `lc run --universe baseline` over the Snakemake DAG and shepherds it to completion (`Monitor` the logs for long jobs — cluster runs can take a while). `lc status` all-`ok` is the deterministic gate.

### VERIFY — the convergence engine, worth one flag

The one phase worth naming at this level: we cannot pre-write a gate for a specific paper's claims — the claims *are* the paper — so the workflow **generates** it. For every replication target VERIFY writes a test encoding the claim, then iterates `run → fix the implementation → rerun the full suite` until the tests pass *or* the **fidelity intent** says stop. TDD for a paper: the claims are the spec, the tests are the gate, green is the goal. The full contract — the metric/table/figure bars, the never-weaken-a-test rule, the intent-bounded loop, the interdependence regression net — is in [`references/verify.md`](references/verify.md).

## Bookend B — CLOSE-OUT (main session)

When the workflow returns, run the close-out from the user's main session — read [`references/review.md`](references/review.md). It uses skills that need `AskUserQuestion` (so they cannot run inside the workflow):

- **`/figure-comparison`** (mandatory) — side-by-side original vs. reproduced figures/tables/numerics, building on the workflow's `report.html`.
- **`/check-sentence-by-sentence`** (opt-in) — audit paper claims against code locations.
- **Walk `open-questions.md`** with the user — the workflow's unresolved decisions (paper-vs-code disagreements it adjudicated code-canonical, citations with no supporting quote, targets that landed below intent). Resolve, finalize, commit.

## Resuming an in-flight reproduction

Workdir state is the resume mechanic — no separate state machine. On re-entry:

1. **No `PLAN.md`** → ORIENT hasn't run; start at Bookend A. (`/paper-extraction` and `/lc-from-code` are survey-first and skip done work, so a partial `work/reference/` resumes cleanly.)
2. **`PLAN.md` committed, the workflow hasn't finished** → re-launch the workflow; it is journal-resumable (`resumeFromRunId`) and its phases are idempotent against on-disk state (filled `astra.yaml`, materialized `results/`, written tests). Same script + args → cached prefix, live tail.
3. **Workflow returned, no close-out yet** → run Bookend B.

`git log --oneline` + `astra validate` + `lc status` answer "where are we" deterministically.

## Disciplines

- **Fidelity intent is the stopping criterion.** Captured at interview, carried in `args.intent` + `PLAN.md`, read by VERIFY to size its fix-loop. This is the spine of the autonomy model — the human said how hard to push, so the middle doesn't need them.
- **Code-as-canonical, with disagreements recorded.** When `work/reference/code/` exists, every phase that touches a sub-analysis reads the relevant code. Where paper and code disagree materially, code is canonical for numerics/method — but the disagreement is preserved (both options in `astra.yaml`, a note in `CLAUDE.md`'s disagreements log, surfaced at close-out).
- **No synthetic data.** Unless the paper itself uses synthetic input, every input is real — downloaded, queried, or fetched from a real archive.
- **arXiv-LaTeX-first acquisition.** When the paper (or a cited paper) is on arXiv, the source tarball is the substrate; equations, captions, tables come through clean. PDF + Docling is the non-arXiv fallback. `/paper-extraction` owns this.
- **Single-writer merge.** Parallel phase workers return structured output; a barrier step folds it into `astra.yaml`. Never have two agents edit `astra.yaml` concurrently.
- **Use the current `astra` CLI.** `astra validate`, `astra validate --verify-evidence`, `astra paper add` — don't reimplement what they do.
- **Open questions go to `open-questions.md`.** The workflow runs detached from the user; questions it can't resolve get a best-judgment default applied and a line in `open-questions.md`, resolved by the user at close-out.

## Anti-patterns

- **Resurrecting the ralph loop.** The middle is a Workflow now. No detached tmux loop, no per-phase fresh-session iteration, no "loop manager" sub-agent.
- **Reading papers in the orchestrator's context.** The fan-out's whole value is bounded workers. Spawn an agent to read a cited paper; don't open it in the main session or the workflow script.
- **Pre-writing a paper-specific gate.** You can't — the claims are the paper. VERIFY *generates* the tests per target. The skill ships the loop, not the gate.
- **Skipping plan mode.** Plan approval is the one human gate before the autonomous middle. "Drafts written → launch" skips the editorial pass that shapes everything.
- **An unbounded VERIFY loop.** The fidelity intent bounds it. A reproduction asked for "an afternoon" that burns a day of fix rounds has ignored its governing parameter.
- **Concurrent `astra.yaml` writes.** Workers return structured output; one merge step writes. Two agents editing the spec at once corrupts it.

## See also

- [`reproduce_workflow.js`](reproduce_workflow.js) — the workflow template the skill launches; adapt per paper.
- [`citation-audit`](../citation-audit/SKILL.md) — the precedent Workflow-driven skill; LITERATURE is its fan-out → verify → synthesize spine.
- [`paper-extraction`](../paper-extraction/SKILL.md) — the upstream acquisition skill ORIENT and LITERATURE consume.
- [`narrative`](../narrative/SKILL.md) — authors `astra.yaml` narrative + decision rationale; invoked by ARCHITECT (the workflow's first phase) and SPECIFY.
- [`figure-comparison`](../figure-comparison/SKILL.md), [`check-sentence-by-sentence`](../check-sentence-by-sentence/SKILL.md) — close-out validation surfaces.
- [`astra`](../astra/SKILL.md), [`lc-cli`](../lc-cli/SKILL.md) — the spec model and the `lc` execution surface.
