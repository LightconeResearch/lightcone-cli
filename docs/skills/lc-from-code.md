# /lc-from-code

Import an existing codebase into ASTRA. The skill scans the project,
drafts `astra.yaml` against what the code already does, parameterizes
its hardcoded analytical choices, and runs until outputs materialize.
Existing logic stays intact; the edits are minimal parameter plumbing.

Source: [`claude/lightcone/skills/lc-from-code/SKILL.md`](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/lc-from-code/SKILL.md).

## Allowed tools

```text
Read, Write, Edit, Glob, Grep,
Bash(astra:*), Bash(lc:*), Bash(python:*), Bash(pip:*), Bash(git:*), Bash(mkdir:*), Bash(ls:*),
Agent, AskUserQuestion
```

## Phases

### Phase 1 — Scan & spec

The skill spawns an `Explore` subagent (Claude Code's general-purpose
search agent) with `/astra`'s Decisions criteria inlined into the
prompt. The subagent returns a structured inventory:

- **Per script/notebook**: file path, what it does, files it reads
  and writes, hardcoded analytical choices (with `file:line`, current
  value, what it controls), how it's invoked.
- **Project-level**: dependency files, data files, any existing
  container setup.

The main agent keeps only the genuinely analytical choices (most
hardcoded values are implementation details), drafts `astra.yaml` with
`recipe:` blocks pointing at the existing scripts, and generates
`universes/baseline.yaml` with defaults matching the current hardcoded
values — so the first run reproduces existing behavior. `astra validate
astra.yaml` then checks the spec, and the user reviews before Phase 2.

### Phase 2 — Implement (parameterize)

The approach depends on the shape of each script:

- **Script with hardcoded values.** Add or extend `argparse`; replace
  the hardcoded values with parsed args.
- **Notebook.** Move the `.ipynb` to `notebooks/` (kept as reference)
  and create a `.py` script that does the parameterized version. The
  recipe points at the new script.
- **Config-file-driven project.** Write a thin wrapper that accepts
  ASTRA decision args, writes the config, and calls the original
  entry point. The user's config-driven code stays untouched.

Hard conventions enforced by the prompt:

- Decision IDs use underscores (`outlier_sigma`), and lightcone-cli
  passes them as `--outlier_sigma`. Argument parsing must match.
- Each output is a *directory*, `results/{universe}/{output_id}/`. The
  recipe receives `{output}` as that directory; scripts write artifacts
  inside it (`{output}/data.parquet`).
- Don't refactor, restructure, or "improve" existing code — parameter
  plumbing only.

### Phase 3 — Run & debug

Run `lc run --universe baseline`, then iterate fixes until `lc status`
shows every output `ok`. If the scan surfaced existing results
elsewhere in the project, compare them against the new
`results/baseline/<output_id>/` to confirm the migration preserved
behavior. Re-validate with `astra validate astra.yaml` and present
the summary.

## Hard rules

- **Minimal changes.** No refactor, no rename, no reorganize.
- **Never guess.** Read every script before claiming what it does.
- **Filter decisions aggressively.** Most hardcoded values are
  implementation details, not decisions.
- **Preserve behavior.** The baseline universe, with default values,
  must reproduce the original exactly.

## Related

- [`/lc-new`](lc-new.md) — for greenfield analyses.
- After migration, run `lc verify` to confirm the spec is valid and
  the provenance chain is intact.
