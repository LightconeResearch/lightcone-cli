# The Agentic Workflow

The agentic surface is five slash commands. Each one is a structured
prompt — the agent follows a specific phased flow, not free-form chat.
This page walks through each of them in the order you'd naturally hit
them.

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

You don't write any code or YAML during `/lc-new`. By the time it
finishes, you have a precise specification. The agent enforces this:
the skill is *only allowed* to edit `astra.yaml`, files in
`universes/`, and `CLAUDE.md`.

## `/lc-build` — implement and run

**You have a scoped `astra.yaml`. You end with materialized outputs.**

This is the longest-running skill. It has two phases.

**Phase 1: plan.** The agent reads the spec, the universe file, and
your existing scripts (if any), and writes a plan to
`.lightcone/plans/build-plan-<universe>.md`. The plan covers
dependencies, decision selections, ordered build checklist, and
verification steps. It asks you to approve before doing anything else.

**Phase 2: loop.** Once you approve, the skill activates an
*autonomous loop*: the agent works through the plan, writes scripts,
runs `lc run` to materialize outputs, fixes failures, and commits as
it goes. The loop keeps going until either every output is
materialized or it hits its iteration limit (default 25).

You can interrupt the loop at any time. If you do, the next time you
run `/lc-build` it asks whether to resume or start fresh.

The plan file persists across crashes; only successful completion
deletes it.

## `/lc-verify` — audit a finished build

**You have materialized outputs. You end with a verification report.**

Read-only. Four checks:

1. `astra validate astra.yaml` passes.
2. `lc status` shows every output `ok` for the universe in question.
3. **Decision-code alignment** (the most important check). For every
   decision in the spec, the agent verifies the code accepts that
   decision as a parameter — i.e. the value isn't silently hardcoded.
4. Result files exist and look well-formed (a `type: metric` output
   should be parseable JSON, etc.).

The skill never modifies anything. If it finds a discrepancy, it
suggests concrete fixes; you re-run `/lc-build` (or fix by hand) and
re-verify.

## `/lc-migrate` — wrap existing code

**You have a folder of scripts. You end with an ASTRA project around
them.**

When you have an existing analysis (a notebook, a folder of `.py`
files, a config-driven pipeline), `/lc-migrate` does the wrapping for
you. Three phases:

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

The hard rule of `/lc-migrate` is **minimal changes**: the skill never
refactors, renames, or "improves" your code. It only adds the parameter
plumbing.

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
slash command is often the right move: the spec, plan, and universe
files are all on disk, so the agent picks up exactly where it left off.
