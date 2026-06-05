# <paper-slug>

Reproduction of **<paper title>** (<arXiv ID>). DOI: <doi>. One-line subject: <e.g. "BAO scale measurement from DESI DR1">.

This file is the durable auto-loading walk-up for the reproduction workdir — the rules every phase runs under, the running paper-vs-code disagreements log, and the pointers into the substrate. It is *not* the contract for what gets reproduced: that is [`PLAN.md`](PLAN.md) (Goal, Fidelity intent, Scope, Targets, Decomposition, Evidence), the human-approved spec the workflow runs against. Read this for *how we work here*; read `PLAN.md` for *what done means*.

**Fidelity intent:** <one line, mirrors PLAN.md — the stopping criterion. E.g. "Figure 3 must be right; the rest can stay rough — overnight." VERIFY reads this (via the workflow's `args.intent`) to size its fix-loop; it is why we push as hard as we do and no harder.>

## Rules

Universal across reproductions — they hold in every phase, the workflow's and the close-out's alike.

- **Code-as-canonical when `work/reference/code/` exists.** Every phase that touches a sub-analysis reads the relevant code first (`code-index.md` maps sub-analysis → file). Where paper and code disagree materially, code is canonical for numerics, plotting, and method — and the disagreement is recorded (both options in `astra.yaml`, a line in the log below). When `work/reference/code/` is absent, the paper is the only anchor: implement fresh from the spec, expect slower convergence, and surface gaps honestly rather than dressing them up.
- **Real data only.** Unless the paper itself uses synthetic input, every input dataset is downloaded, queried, or fetched from its real archive — never fabricated.
- **arXiv-LaTeX-first acquisition.** The arXiv source tarball is the substrate for the paper and every cited paper; equations, captions, and tables come through clean. PDF + Docling is the non-arXiv fallback only.
- **Single-writer merge for `astra.yaml`.** Per-phase workers are bounded and stateless: they read their inputs, do their job, and *return structured output* — they never edit `astra.yaml`. A single barrier merge step folds every worker's result into the spec. Two agents editing it at once corrupts it.
- **Fidelity intent is the stopping criterion.** The intent above (and in `PLAN.md`) bounds the work — most sharply VERIFY's fix-loop. "An afternoon" accepts what's close after a round or two; "no deadline" pushes every target to green. Don't burn rounds the intent didn't ask for.
- **Commit as you go.** Small, descriptive commits per significant change. The git log is the chronological trail; a resuming session reads `git log --oneline` + `git diff` to know what landed.
- **Open questions go to `open-questions.md`.** The workflow runs detached from the user, so a question it can't resolve gets a best-judgment default applied and a line in `open-questions.md` — resolved by the user at close-out.

## Paper-vs-code disagreements

Material disagreements between paper and code, logged as they are found. Code is canonical for numerics, plotting, and method (per the rule above); both options are preserved in `astra.yaml` as decision alternatives. The workflow's SPECIFY, IMPLEMENT, and VERIFY workers return any conflict they adjudicated code-canonical, and the merge/fix step appends it here — one line each, pointing at the corresponding decision so any later session sees them at a glance. Surfaced to the user at close-out (or earlier if they're around).

- (none yet)

## Pointers

- [`PLAN.md`](PLAN.md) — Goal, Fidelity intent, Scope, Targets, Decomposition, Evidence. The human-approved reproduction contract.
- [`targets/targets.md`](targets/targets.md) — the replication-target ledger (priority, expected value + stated uncertainty, comparison guidance). VERIFY writes a test per row.
- `astra.yaml` — the spec: sub-analyses, inputs, outputs, decisions, findings, prior_insights, recipes. The skeleton is built by the workflow's ARCHITECT phase from `PLAN.md`, then filled by SPECIFY/IMPLEMENT; the single source of truth for execution.
- [`reproduce_workflow.js`](.claude/skills/lc-from-paper/reproduce_workflow.js) — the autonomous middle: ARCHITECT → SPECIFY ∥ LITERATURE → IMPLEMENT → RUN → VERIFY → REVIEW. Phase contracts live in `references/<phase>.md`.
- `work/reference/index.json` — paper structural index (figures, tables, outline, citations with resolved DOIs); the starting surface for any "where in the paper does X happen" lookup.
- `work/reference/code-index.md` — code inventory (when code present): module map, candidate decisions with file:line, entry-points, gotchas. The sub-analysis → code mapping every phase consults.
- `open-questions.md` — accumulated questions the workflow couldn't resolve; walked with the user at close-out.
- <any paper-specific conventions or warnings the user surfaced during the interview>
