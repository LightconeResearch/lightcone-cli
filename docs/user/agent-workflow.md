# The Agentic Workflow

The agentic surface is three entry slash commands plus feedback. The
`/lc-from-*` family is parallel by what you start from — a question,
code, or a paper — and `/lc-feedback` handles bug reports. Each one is
a structured prompt: the agent follows a specific phased flow, not
free-form chat. This page walks through each of them in the order you'd
naturally hit them.

The skills are structured entry points; they aren't requirements. Once
you're inside a project, you can also just describe what you're working
on to Claude — `astra.yaml` and the `lc` CLI keep things tracked
whether you go through a skill or not.

> The bracketed `→ astra.yaml` etc. notes show what each phase actually
> writes to disk. You stay in charge of approving everything; the agent
> never publishes a paper for you.

## `/lc-new` — scope a new analysis

**You start with a research question. You end with a complete
`astra.yaml` (and optionally a literature evidence trail).**

The skill walks you through four phases:

1. **Research question.** "What are you trying to learn?" The agent
   sharpens the question with a few follow-ups and writes the project
   metadata (`name`, `description`, `version`) to `astra.yaml` right
   away — `→ astra.yaml`.
2. **Analysis structure.** "Walk me through your analysis step by
   step." You describe what goes in and what comes out; the agent
   fills in `inputs:` and `outputs:`, one entry per metric or plot or
   artifact. If the analysis is multi-stage, it suggests splitting it
   into sub-analyses with confirmation. `→ astra.yaml`.
3. **Deep dive (optional).** A literature pass. The agent searches
   for relevant papers and asks which ones to extract. For each
   approved paper it spawns a subagent (`lc-extractor`) that reads the
   PDF and pulls verbatim quotes. Quotes are *machine-verified* against
   the source — paraphrases get rejected. The agent then turns the
   conversation + literature into a list of candidate decisions
   (methodological choices that could shift results). `→ astra.yaml`.
4. **Finalize.** `astra validate astra.yaml` to make sure the spec is
   valid; `astra universe generate -n baseline` to seed a baseline
   universe; the `narrative:` block in `astra.yaml` gets filled in
   (`summary`, `methods`, `inputs`, `outputs` — `findings` stays TODO
   until results exist); the `## Working Notes` section of `CLAUDE.md`
   gets the conversational context that wouldn't otherwise survive a
   `/clear`.

You don't write any code or YAML during `/lc-new`. By the
time it finishes, you have a precise specification. The agent enforces
this: the skill is *only allowed* to edit `astra.yaml`, files in
`universes/`, and `CLAUDE.md`.

## `/lc-from-code` — wrap existing code

**You have a folder of scripts. You end with an ASTRA project around
them.**

When you have an existing analysis (a notebook, a folder of `.py`
files, a config-driven pipeline), `/lc-from-code` does the wrapping
for you. Three phases:

1. **Scan.** A subagent reads every script and notebook and returns a
   structured inventory: what each script reads, writes, and contains
   in the way of hardcoded analytical choices.
2. **Spec.** From the inventory, the agent drafts an `astra.yaml`
   with `recipe:` blocks pointing at the existing scripts and a
   `baseline` universe whose defaults match the current hardcoded
   values. The first run reproduces existing behavior.
3. **Implement & debug.** The agent adds CLI argument parsing for the
   identified decisions, leaves the actual analytical logic alone, and
   iterates on `lc run` until everything materializes.

The hard rule of `/lc-from-code` is **minimal changes**: the skill
never refactors, renames, or "improves" your code. It only adds the
parameter plumbing.

## `/lc-from-paper` — reproduce a published paper

**You have a DOI or arXiv ID. You end with a reproduction project: two
interactive bookends in your main session wrapping one autonomous
Workflow that carries the heavy middle.**

`/lc-from-paper` is the entry point of the paper-reproduction bundle.
It opens with **ORIENT → PLAN** — the first bookend, in your main
session — which runs in stages: ask for the paper, run
`/paper-extraction` inline (so subsequent questions are grounded in the
actual paper), interview you (scope, fidelity intent — your prose answer
to "when is this good enough," which becomes the Workflow's *stopping
criterion* — code repo confirmation, paper-specific conventions, prior
familiarity, external context), clone the reference code and run
`/lc-from-code` scan-only (when a repo exists), then draft **two files**
at the workdir root: a prose `PLAN.md` (Goal, fidelity intent + stopping
criterion, scope, a Targets sketch, a Decomposition sketch, evidence)
and `CLAUDE.md` (the auto-loading walk-up with rules, the paper-vs-code
disagreements log, pointers). You approve the plan in **plan mode** —
the single gate before the autonomous middle takes over — then a single
first commit captures `PLAN.md` + `CLAUDE.md` + the full
`work/reference/` substrate.

On approval, the skill launches the reproduce-paper **Workflow**
(`reproduce_workflow.js`) from the workdir. It's the multi-agent
Workflow primitive — deterministic `agent()` / `parallel()` /
`pipeline()` orchestration over fresh subagent contexts — that carries
the middle: **ARCHITECT** (realize the plan into the `astra.yaml`
skeleton + a `targets/targets.md` ledger) → **SPECIFY ∥ LITERATURE**
(pipeline per sub-analysis) → **IMPLEMENT** (parallel per output) →
**RUN** (`lc run` over the Snakemake DAG) → **VERIFY** → **REVIEW**.
Workers return schema-validated structured output; a single barrier
merge folds each phase's results into `astra.yaml`. The Workflow runs in
the background and can't ask you questions, so it logs anything it can't
resolve to `open-questions.md` with a best-judgment default and keeps
moving.

**VERIFY** is the convergence engine. We can't pre-write a gate for a
specific paper's claims — the claims *are* the paper — so the Workflow
*generates* it: for each replication target it writes a test encoding
the claim, then iterates `run → fix the implementation → rerun` until
the tests pass *or* your fidelity intent says stop. TDD for a paper, and
the intent bounds the fix loop so a reproduction asked for "an
afternoon" doesn't burn a day.

When the Workflow returns — with a review summary, a `report.html`, and
per-target verify results — the agent runs **CLOSE-OUT** (the second
bookend) back in your session: `/figure-comparison` against the targets,
optional `/check-sentence-by-sentence`, and a walk through the
accumulated `open-questions.md`. You resolve, finalize, and commit.

The bundle composes sibling skills: `paper-extraction`, `narrative`,
`figure-comparison`, and `check-sentence-by-sentence`. See
[`claude/lightcone/skills/README.md`](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/README.md)
for the full bundle map.

## `/lc-feedback` — file an issue without context-switching

**Something broke. You end with a GitHub issue URL.**

Inline arguments are encouraged: `/lc-feedback pipeline dies on second
output`. The skill triages the right repo (ASTRA vs lightcone-cli),
collects the version of `astra` and `lightcone-cli`, your Python
version, and your OS, drafts a minimal issue body with a trimmed error
trace, asks you to confirm, and runs `gh issue create`. One round trip,
back to work.

It needs `gh auth status` to succeed first; it'll tell you to
`gh auth login` if not.

## When things go sideways

You don't need to memorize the phases. The agent will tell you what
phase it's in via stage banners, and the skills are written to be
interruptible — every phase writes to disk so a `/clear` (which frees
up context) doesn't lose your work.

If a skill seems stuck, a quick `/clear` followed by reinvoking the
slash command is often the right move: the spec, universe files, and
written work products are all on disk, so the agent can pick up where
it left off.
