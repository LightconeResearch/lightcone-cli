---
name: prism-migrate
description: Migrate an existing project into ASTRA/Prism. Scans code, generates astra.yaml, parameterizes decisions, and runs until outputs materialize. Use after `prism init . --existing-project`. Triggers on "migrate", "convert", "existing project".
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(astra:*), Bash(prism:*), Bash(python:*), Bash(pip:*), Bash(git:*), Bash(mkdir:*), Bash(ls:*), Agent, AskUserQuestion
---

# /prism-migrate

End-to-end migration: scan existing code, generate the ASTRA spec, parameterize decisions in the code, and run until everything materializes. The user's existing logic stays intact -- changes are limited to adding argument parsing and replacing hardcoded values with parameters.

## References

- [Decision Guide](../../guides/decision-guide.md) -- when a hardcoded value is vs. isn't a decision

## Phase 1: Scan & Spec

Spawn an Explore subagent to scan the project:

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

And a separate list of candidate decisions with file:line references.
""")
```

Write the scan results to `CLAUDE.md` under Analysis Context as a script inventory, then draft `astra.yaml` from the scan results following the spec structure documented in `CLAUDE.md`. Use the [Decision Guide](../../guides/decision-guide.md) to filter candidate decisions — most hardcoded values are implementation details, not decisions. Use current hardcoded values as defaults.

Also generate `universes/baseline.yaml` with all defaults matching the current hardcoded values (so the first run reproduces existing behavior).

**Present the draft spec to the user for review.** Walk through the decisions specifically — these are the most subjective part. Write to `astra.yaml` and `universes/baseline.yaml` after confirmation.

Validate: `astra validate astra.yaml`. Fix any errors.

## Phase 2: Implement

For each script that has decisions, make minimal edits:

1. **Add argument parsing** at the top (or extend existing argparse):
   ```python
   parser = argparse.ArgumentParser()
   parser.add_argument('--learning_rate', type=float, default=0.001)
   parser.add_argument('--threshold', type=float, default=0.5)
   args = parser.parse_args()
   ```

2. **Replace hardcoded values** with the parsed args:
   ```python
   # Before: lr = 0.001
   # After:
   lr = args.learning_rate
   ```

3. **Update output paths** to write to `results/{universe}/{output_id}.ext`:
   ```python
   import os
   universe = os.environ.get('PRISM_UNIVERSE', 'baseline')
   output_dir = f'results/{universe}'
   os.makedirs(output_dir, exist_ok=True)
   ```

That's it. Don't refactor, don't restructure, don't improve the code. Just add the parameter plumbing and output path convention.

**Underscore convention:** Decision IDs use underscores in `astra.yaml` (`learning_rate`). Prism passes `--learning_rate`. argparse must match: `parser.add_argument('--learning_rate')`.

Commit after each script is parameterized.

## Phase 3: Run & Debug

```bash
prism run --universe baseline
```

If it fails, read the error, fix it, and retry. Common issues:
- Missing dependency -- add to requirements.txt
- Import error -- check the script runs from the project root
- File not found -- check input paths relative to project root
- Output not where expected -- check output path convention

Iterate until `prism status` shows all outputs as `ok`.

Then validate: `astra validate astra.yaml`. Commit all changes. Present summary to user.

## Rules

- **Minimal changes.** Only add argparse and replace hardcoded values. Do not refactor, rename, reorganize, or "improve" existing code.
- **Don't guess.** Read every script before making claims about what it does.
- **Filter decisions aggressively.** Most hardcoded values are implementation details, not analytical choices.
- **Preserve behavior.** The baseline universe with default values must reproduce the original behavior exactly.
- **One thing at a time.** Parameterize one script, commit, move to the next.
