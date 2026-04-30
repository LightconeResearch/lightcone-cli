---
name: lc-migrate
description: Migrate an existing project into ASTRA / lightcone-cli. Scans code, generates astra.yaml, parameterizes decisions, and runs until outputs materialize. Triggers on "migrate", "convert", "existing project".
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(astra:*), Bash(lc:*), Bash(python:*), Bash(pip:*), Bash(git:*), Bash(mkdir:*), Bash(ls:*), Agent, AskUserQuestion
---

# /lc-migrate

End-to-end migration: scan existing code, generate the ASTRA spec, parameterize decisions in the code, and run until everything materializes. The user's existing logic stays intact — changes should be minimal.

## References

- [ASTRA Reference](../../guides/astra-reference.md) -- spec structure, decision identification, recipes, universes

## Phase 1: Scan & Spec

First, read the Decisions section of [ASTRA Reference](../../guides/astra-reference.md), then spawn an Explore subagent to scan the project. Include the decision criteria in the prompt so the subagent can classify candidates:

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

Write the scan results to `CLAUDE.md` under `## Project Notes` as a script inventory, then draft `astra.yaml` from the scan results following the spec structure documented in `.claude/guides/astra-reference.md`. Use the decision criteria from [ASTRA Reference](../../guides/astra-reference.md) to filter the subagent's candidate decisions down to only true analytical choices — most hardcoded values are implementation details, not decisions. Use current hardcoded values as defaults.

For each output, list the upstream artifacts it depends on under `Output.inputs: [...]` and the decisions it consumes under `Output.decisions: [...]`. Then add a `recipe.command` template that references each via `{inputs.<id>}` / `{decisions.<id>}` and writes to `{output}`. Example:

```yaml
outputs:
  - id: galaxy_catalog
    type: data
    inputs: [survey_data]
    decisions: [magnitude_cut, redshift_min]
    recipe:
      command: >
        python src/build_catalog.py
        --survey {inputs.survey_data}
        --magnitude_cut {decisions.magnitude_cut}
        --redshift_min {decisions.redshift_min}
        --output {output}
```

Also generate `universes/baseline.yaml` with all defaults matching the current hardcoded values (so the first run reproduces existing behavior).

Write to `astra.yaml` and `universes/baseline.yaml`, then validate: `astra validate astra.yaml`. Fix any errors.

Use `AskUserQuestion` to ask the user to review the spec — they can open `astra.yaml` directly or right-click it and open in lightcone-ui. Wait for confirmation before proceeding to implementation.

## Phase 2: Implement

Parameterize the code so decisions can be varied across universes. The goal is minimal changes to user code. Use your best judgement for the approach — the options below are not exhaustive:

**For scripts with hardcoded values:** Add argparse (or extend existing argument parsing) and replace hardcoded values with the parsed args. This is the simplest case.

**For notebooks:** Move the `.ipynb` to `notebooks/` (preserving it as reference), then create a `.py` script that does the parameterized version. The recipe points to the new script.

**For config-file-driven projects:** Create a thin wrapper script that accepts ASTRA decision args, writes/updates the config file, then calls the original entry point. The user's config-driven code stays untouched.

**Dependencies:** Check that `requirements.txt` includes all packages the code imports. If one doesn't exist, create it. If it's incomplete, add missing deps.

**Containers:** Set `container:` in `astra.yaml` based on what the scan found.
- Existing `Containerfile` or `Dockerfile`: point at it (e.g. `container: Containerfile`).
- No container setup but a `requirements.txt`: write a minimal `Containerfile` (`FROM python:3.12-slim`, copy and `pip install -r requirements.txt`, then `COPY . .`) and point `container:` at it.
- Nothing to go on: set `container: python:3.12-slim` as a starting point — the user can swap to a real `Containerfile` later.

Whatever approach you use:

- **Don't refactor, restructure, or improve the code.** Just add the parameter plumbing.
- **The recipe template is what wires decisions to scripts.** Each `{decisions.<id>}` placeholder in `recipe.command` substitutes the active option ID at runtime, and the recipe author chooses the script-side flag name. Underscore IDs in the spec → match-them-yourself flags in the script (e.g. `--outlier_sigma {decisions.outlier_sigma}` paired with `parser.add_argument('--outlier_sigma')`). There is no auto-injection.
- **Output paths.** The recipe receives the output directory as `{output}` — pass that through to the script (`--output {output}`) and have the script write its artifact inside that directory (e.g. `{output}/data.parquet`).
- **Update recipes** in `astra.yaml` if the entry point or command changed.

## Phase 3: Run & Debug

```bash
lc run --universe baseline
```

If it fails, read the error, fix it, and retry. Iterate until `lc status` shows all outputs as `ok`.

If the scan found existing results elsewhere in the project, compare them against the new outputs in `results/baseline/<output_id>/` to verify the migration preserved behavior.

Then validate the spec and the provenance chain: `astra validate astra.yaml` and `lc verify`. Present summary to user.

## Rules

- **Minimal changes.** Do not refactor, rename, reorganize, or "improve" existing code.
- **Don't guess.** Read every script before making claims about what it does.
- **Filter decisions aggressively.** Most hardcoded values are implementation details, not analytical choices.
- **Preserve behavior.** The baseline universe with default values must reproduce the original behavior exactly.
