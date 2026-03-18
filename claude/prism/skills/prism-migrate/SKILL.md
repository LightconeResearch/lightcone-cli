---
name: prism-migrate
description: Migrate an existing project into ASTRA/Prism. Scans code, generates astra.yaml, parameterizes decisions, and runs until outputs materialize. Use after `prism init . --existing-project`. Triggers on "migrate", "convert", "existing project".
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(astra:*), Bash(prism:*), Bash(python:*), Bash(pip:*), Bash(git:*), Bash(mkdir:*), Bash(ls:*), Agent, AskUserQuestion
---

# /prism-migrate

End-to-end migration: scan existing code, generate the ASTRA spec, parameterize decisions in the code, and run until everything materializes. The user's existing logic stays intact -- changes are limited to adding argument parsing and replacing hardcoded values with parameters.

## References

- [Decision Guide](../../guides/decision-guide.md) -- when a hardcoded value is vs. isn't a decision

## Phase 1: Scan

Read every script and notebook in the project. For each, note:
- What it does (read it, don't guess)
- What it reads (data files, configs) and writes (results, plots, models)
- Hardcoded analytical choices: magic numbers, commented alternatives, method-selecting branches, config dicts. Note file, line number, current value.
- How it's invoked (argparse, config file, nothing)
- Dependencies (requirements.txt, pyproject.toml, etc.)

Present a summary table:

```
| Script       | Purpose      | Reads          | Writes            | Hardcoded choices          |
|--------------|--------------|----------------|-------------------|----------------------------|
| train.py     | Train model  | data/train.csv | models/best.pt    | lr=0.001 (L23), epochs=50  |
| evaluate.py  | Evaluate     | models/best.pt | results/acc.json  | threshold=0.5 (L8)        |
```

Ask the user: "Does this look right? Anything missing or wrong?"

## Phase 2: Spec

From the scan, draft `astra.yaml`:

- **name/description**: derive from what the code does
- **inputs**: data files and external sources the code reads
- **outputs**: files the code produces, typed as `metric`, `figure`, `table`, `data`, or `report`. One output per file.
- **decisions**: hardcoded values that are analytical choices (apply the [Decision Guide](../../guides/decision-guide.md)). Filter aggressively -- not a decision unless changing it could change the conclusion. Use the current hardcoded values as defaults.
- **recipes**: `command:` pointing to existing scripts. Add `inputs:` for cross-output dependencies.
- **container**: reference existing Containerfile, or `python:3.12-slim` as default

Also generate `universes/baseline.yaml` with all defaults matching the current hardcoded values (so the first run reproduces existing behavior).

**Present the draft to the user for review.** Walk through the decisions specifically -- these are the most subjective part. Write to `astra.yaml` and `universes/baseline.yaml` after confirmation.

Validate: `astra validate astra.yaml`. Fix any errors.

## Phase 3: Implement

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

## Phase 4: Run & Debug

```bash
prism run --universe baseline
```

If it fails, read the error, fix it, and retry. Common issues:
- Missing dependency -- add to requirements.txt
- Import error -- check the script runs from the project root
- File not found -- check input paths relative to project root
- Output not where expected -- check output path convention

Iterate until `prism status` shows all outputs as `ok`.

## Phase 5: Finalize

1. Validate: `astra validate astra.yaml`
2. Update `CLAUDE.md` Analysis Context section with:
   - **Domain Context**: what the user explained about the project
   - **Migration Notes**: what was parameterized, any structural changes made
3. Commit all changes.

Present summary and confirm with the user.

## Rules

- **Minimal changes.** Only add argparse and replace hardcoded values. Do not refactor, rename, reorganize, or "improve" existing code.
- **Don't guess.** Read every script before making claims about what it does.
- **Filter decisions aggressively.** Most hardcoded values are implementation details, not analytical choices.
- **Preserve behavior.** The baseline universe with default values must reproduce the original behavior exactly.
- **One thing at a time.** Parameterize one script, commit, move to the next.
