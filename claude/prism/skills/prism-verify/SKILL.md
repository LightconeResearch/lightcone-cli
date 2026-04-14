---
name: prism-verify
description: Verify that astra.yaml, code, and results are consistent. Run after building an analysis.
allowed-tools: Read, Glob, Grep, Bash(astra:*), Bash(prism:*), Bash(python:*), Bash(ls:*), AskUserQuestion
---

# /prism-verify

Verify that the spec, code, and results all agree. Default universe is `baseline` unless specified.

## Checks

### 1. Spec validation

```bash
astra validate astra.yaml
```

### 2. Materialization status

```bash
prism status --universe <universe_id>
```

Every output should show `ok`. Flag anything pending, missing, or without a recipe.

### 3. Decision-code alignment

**The most important check.** For every decision in `astra.yaml`, confirm the code accepts it as a parameter and does not hardcode its value. Compare `astra info --decisions` against `grep -r "add_argument" scripts/`.

### 4. Results match spec

For every output in `astra.yaml`, verify `results/<universe_id>/<output_id>.<ext>` exists and looks well-formed. For `type: metric` outputs, check for valid `{"value": ...}` JSON.

## Report

```
| Check                    | Status |
|--------------------------|--------|
| Spec validation          | ✓/✗    |
| Materialization (N/N)    | ✓/✗    |
| Decision-code alignment  | ✓/⚠/✗  |
| Results match spec (N/N) | ✓/✗    |
```

List each finding with file paths and line numbers. If there are failures, suggest concrete fixes.

## Rules

- **Read-only** — never modify files
- **One universe at a time**
- **Never skip check 3** — decision-code alignment is the core value
- **Always read actual result files** — don't infer from code
