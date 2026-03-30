---
name: prism-migrate
description: Migrate an existing project into ASTRA/Prism. Scans code, generates astra.yaml, parameterizes decisions, and runs until outputs materialize. Use after `prism init . --existing-project`. Triggers on "migrate", "convert", "existing project".
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(astra:*), Bash(prism:*), Bash(python:*), Bash(pip:*), Bash(git:*), Bash(mkdir:*), Bash(ls:*), Agent, AskUserQuestion
---

# /prism-migrate

End-to-end migration: scan existing code, generate the ASTRA spec, parameterize decisions in the code, and run until everything materializes. The user's existing logic stays intact — changes should be minimal.

## References

- [ASTRA Reference](../../guides/astra-reference.md) -- spec structure, decision identification, recipes, universes

## Phase 1: Scan & Spec

Spawn an Explore subagent to scan the project:

First, read the Decisions section of [ASTRA Reference](../../guides/astra-reference.md), then spawn an Explore subagent. Include the decision criteria in the prompt so the subagent can classify candidates:

```
Agent(subagent_type="Explore", prompt="""
Scan this project thoroughly and return a structured inventory.

For every script and notebook, report:
- File path
- What it does (read the code, don't guess)
- What files it reads (data, configs, other scripts' outputs)
- What files it writes (results, plots, models, etc.)
- Hardcoded analytical choices: magic numbers, commented alternatives,
  method-selecting branches, config dicts. Include file, line number,
  current value, and what it controls.
- How it's currently invoked (argparse, config file, nothing)

Also report:
- Dependencies (requirements.txt, pyproject.toml, environment.yml, etc.)
- Data files present in the project
- Any existing container setup (Dockerfile, Containerfile)

Return the results as a markdown table:
| Script | Purpose | Reads | Writes | Hardcoded choices |

And a separate list of ALL candidate decisions with file:line references.
Err on the side of completeness — include anything that could plausibly
be an analytical choice. The orchestrator will filter down later.

For reference, here are the decision criteria for classifying candidates:
<decision-criteria>
{paste Decisions section from astra-reference.md here}
</decision-criteria>
""")
```

Write the scan results to `CLAUDE.md` under Analysis Context as a script inventory, then draft `astra.yaml` from the scan results following the spec structure documented in `.claude/guides/astra-reference.md`. Use the decision criteria from [ASTRA Reference](../../guides/astra-reference.md) to filter the subagent's candidate decisions down to only true analytical choices — most hardcoded values are implementation details, not decisions. Use current hardcoded values as defaults.

Include `recipe:` blocks on each output pointing to the script that produces it. Also generate `universes/baseline.yaml` with all defaults matching the current hardcoded values (so the first run reproduces existing behavior).

Write to `astra.yaml` and `universes/baseline.yaml`, then validate: `astra validate astra.yaml`. Fix any errors.

Use `AskUserQuestion` to ask the user to review the spec — they can open `astra.yaml` directly or right-click it and open in Prism-UI. Wait for confirmation before proceeding to implementation.

## Phase 2: Implement

Parameterize the code so decisions can be varied across universes. The goal is minimal changes to user code. Use your best judgement for the approach — the options below are not exhaustive:

**For scripts with hardcoded values:** Add argparse (or extend existing argument parsing) and replace hardcoded values with the parsed args. This is the simplest case.

**For notebooks:** Move the `.ipynb` to `notebooks/` (preserving it as reference), then create a `.py` script that does the parameterized version. The recipe points to the new script.

**For config-file-driven projects:** Create a thin wrapper script that accepts ASTRA decision args, writes/updates the config file, then calls the original entry point. The user's config-driven code stays untouched.

**Dependencies:** Check that `requirements.txt` includes all packages the code imports. If one doesn't exist, create it. If it's incomplete, add missing deps.

Whatever approach you use:

- **Don't refactor, restructure, or improve the code.** Just add the parameter plumbing.
- **Underscore convention:** Decision IDs use underscores in `astra.yaml` (`outlier_sigma`). Prism passes `--outlier_sigma`. Argument parsing must match.
- **Update output paths** to write to `results/{universe}/{output_id}.ext` following the convention in `CLAUDE.md`.
- **Update recipes** in `astra.yaml` if the entry point or command changed.

## Phase 3: Run & Debug

```bash
prism run --universe baseline
```

If it fails, read the error, fix it, and retry. Iterate until `prism status` shows all outputs as `ok`.

If the scan found existing results elsewhere in the project, compare them against the new outputs in `results/baseline/` to verify the migration preserved behavior.

Then validate: `astra validate astra.yaml`. Present summary to user.

## Rules

- **Minimal changes.** Do not refactor, rename, reorganize, or "improve" existing code.
- **Don't guess.** Read every script before making claims about what it does.
- **Filter decisions aggressively.** Most hardcoded values are implementation details, not analytical choices.
- **Preserve behavior.** The baseline universe with default values must reproduce the original behavior exactly.
