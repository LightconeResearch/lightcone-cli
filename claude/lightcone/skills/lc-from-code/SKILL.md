---
name: lc-from-code
description: Bring an existing project into ASTRA / lightcone-cli, starting from the code. Scans the codebase, drafts or augments astra.yaml, parameterizes decisions, and runs until outputs materialize. Triggers on "migrate", "convert", "existing project", "wrap this code", "start from code".
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(astra:*), Bash(lc:*), Bash(python:*), Bash(pip:*), Bash(git:*), Bash(mkdir:*), Bash(ls:*), Agent, AskUserQuestion
---

# /lc-from-code

End-to-end migration: scan existing code, draft or add to `astra.yaml`, parameterize decisions in the code, and run until everything materializes. This works both as a fresh start from code and as an augmenting pass inside an existing ASTRA project. The user's existing logic stays intact — changes should be minimal.

## Invocation contexts

This skill has two invocation contexts. The first is the user-driven default described in the phases below: do the full scan → spec → parameterize → run flow.

The second is **scan-only**, used when `/lc-from-paper`'s ACQUIRE invokes this skill against a cloned reference repo at `work/reference/code/`. The invocation prompt will tell you explicitly to *do only Phase 1's scan*, write the inventory to a path it specifies (typically `work/reference/code-index.md`), and **stop** — do not touch `astra.yaml` at the project root, do not parameterize any code, do not run anything, do not modify the cloned repo. In scan-only mode, **do the inventory work inline** (using Read, Glob, Grep directly) rather than dispatching the Explore sub-agent that fresh-migration mode uses below. The scan-only branch can run nested inside another agent context (no sub-agent dispatch is safe in that case), and the inventory is bounded enough to do inline. Trust the invocation prompt's instructions over the fresh-migration defaults below; if the prompt says scan-only, the scan-only contract holds.

## References

- [ASTRA Reference](../../guides/astra-reference.md) -- spec structure, decision identification, recipes, universes

## Phase 1: Scan & Spec

First, read the Decisions section of [ASTRA Reference](../../guides/astra-reference.md), then decide which mode applies:

- **Fresh migration:** no meaningful `astra.yaml` exists yet. Use the code scan to draft `astra.yaml` and `universes/baseline.yaml`.
- **Augment existing ASTRA:** `astra.yaml` already exists from a paper, user interview, or prior ASTRA work. Use the code scan to add to the current spec — recipes, dependencies, containers, code-backed decision options, baseline selections, implementation notes, and missing inputs / outputs where they naturally belong. Do not create a second `astra.yaml`, do not replace the existing structure wholesale, and surface major structure conflicts to the user before reshaping the spec.

### Scanning the project

In **fresh migration** mode (user's main session, full migration flow), spawn an Explore subagent to scan the project. Include the decision criteria in the prompt so the subagent can classify candidates:

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

In **scan-only** mode (invoked by `/lc-from-paper` ACQUIRE), do the same inventory work inline using Read / Glob / Grep:

- `Glob` for `**/*.py`, `**/*.ipynb`, `**/Dockerfile`, `**/Containerfile`, `**/requirements*.txt`, `**/environment*.yml`, `**/pyproject.toml`, and any other relevant dependency / container manifests. Inventory the matches.
- For each script and notebook, `Read` it (paginating with offset / limit for large files) to identify what it does, what it reads / writes, and any hardcoded analytical choices with `file:line` references.
- `Grep` for repeated patterns when surveying for candidate decisions across the tree (magic numbers, common method-selector patterns, config-dict keys).
- Apply the same decision criteria from the Decisions section of ASTRA Reference to classify candidates; the criteria are the filter regardless of whether the inventory came from an Explore sub-agent or inline reads.

Either way, write the scan results to `CLAUDE.md` under `## Project Notes` (fresh migration) or to the path the invocation prompt specifies (scan-only — typically `work/reference/code-index.md`) as a script inventory, then in fresh migration mode draft or add to `astra.yaml` from the scan results following the spec structure documented in `.claude/guides/astra-reference.md`. In scan-only mode, stop after the inventory file lands; do not touch `astra.yaml`. Use the decision criteria from [ASTRA Reference](../../guides/astra-reference.md) to filter candidate decisions down to only true analytical choices — most hardcoded values are implementation details, not decisions. Use current hardcoded values as defaults.

In augment mode, preserve the existing paper-derived or user-derived `inputs`, `outputs`, `decisions`, `findings`, and `narrative` unless the code scan shows a real conflict. Attach code evidence to the nearest existing home first. Create new ASTRA structure only when the code reveals a real analysis object that has no suitable home in the current spec.

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

Also generate or update `universes/baseline.yaml` with all defaults matching the current hardcoded values (so the first run reproduces existing behavior).

Write to `astra.yaml` and `universes/baseline.yaml`, then validate: `astra validate astra.yaml`. Fix any errors.

Use `AskUserQuestion` to ask the user to review the spec — they can open `astra.yaml` directly or right-click it and open in lightcone-ui. Wait for confirmation before proceeding to implementation.

## Phase 2: Implement

Parameterize the code from ASTRA decisions so the baseline run reproduces the existing behavior. The goal is minimal changes to user code. Use your best judgement for the approach — the options below are not exhaustive:

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
