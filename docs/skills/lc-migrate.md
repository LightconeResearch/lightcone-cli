# /lc-migrate

Migrate an existing project into ASTRA / lightcone-cli. Scans the
code, generates `astra.yaml`, parameterizes hardcoded analytical
choices, and runs until outputs materialize. Existing logic stays
intact — changes should be minimal.

Source: [`claude/lightcone/skills/lc-migrate/SKILL.md`](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/lc-migrate/SKILL.md).

## Allowed tools

```
Read, Write, Edit, Glob, Grep,
Bash(astra:*), Bash(lc:*), Bash(python:*), Bash(pip:*), Bash(git:*), Bash(mkdir:*), Bash(ls:*),
Agent, AskUserQuestion
```

## Phases

### Phase 1 — Scan & spec

The skill spawns an `Explore` subagent (Claude Code's general-purpose
search agent) with the decision criteria from `astra-reference.md`
inlined into the prompt. The subagent returns a structured inventory:

- Per script/notebook: file path, what it does, files it reads & writes,
  hardcoded analytical choices (with file:line, current value, what it
  controls), how it's invoked.
- Project-level: dependency files, data files, existing container
  setup.

The main agent filters the candidate decisions down to true analytical
choices (most hardcoded values are implementation details, not
decisions), drafts `astra.yaml` with `recipe:` blocks pointing at the
existing scripts, and generates `universes/baseline.yaml` with all
defaults matching the current hardcoded values — so the first run
reproduces existing behavior. Spec is then validated with
`astra validate astra.yaml`.

The user is asked to review before Phase 2.

### Phase 2 — Implement (parameterize)

The skill picks an approach per script type:

- **Script with hardcoded values** — add (or extend) argparse, replace
  hardcoded values with parsed args.
- **Notebook** — move the `.ipynb` to `notebooks/` (preserved as
  reference), create a `.py` script that does the parameterized
  version. The recipe points at the new script.
- **Config-file-driven project** — write a thin wrapper script that
  accepts ASTRA decision args, writes the config, then calls the
  original entry point. The user's config-driven code stays untouched.

Hard conventions enforced by the prompt:

- Decision IDs use underscores in `astra.yaml` (`outlier_sigma`).
  lightcone-cli passes `--outlier_sigma`. Argument parsing must match.
- Output paths follow `results/{universe}/{output_id}.ext` (the
  per-output convention).
- Don't refactor, restructure, or "improve" existing code — only
  parameter plumbing.

### Phase 3 — Run & debug

`lc run --universe baseline`. Iterate fixes until `lc status` shows all
outputs `ok`. If the scan turned up existing results elsewhere in the
project, compare them against the new `results/baseline/` to verify
the migration preserved behavior. Then `astra validate astra.yaml` and
present the summary.

## Hard rules

- Minimal changes — no refactor, rename, reorganize.
- Never guess — read every script before claiming what it does.
- Filter decisions aggressively — most hardcoded values are
  implementation details.
- Preserve behavior — the baseline universe with default values must
  reproduce the original behavior exactly.

## Related

- [`/lc-new`](lc-new.md) — for greenfield analyses.
- [`/lc-verify`](lc-verify.md) — run after migration to confirm
  spec-code-results alignment.
