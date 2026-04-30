---
name: lc-verify
description: Verify that astra.yaml, code, and results are consistent. Run after building an analysis.
allowed-tools: Read, Glob, Grep, Bash(astra:*), Bash(lc:*), Bash(python:*), Bash(ls:*), AskUserQuestion
---

# /lc-verify

Verify that the spec, code, and results all agree. Default universe is `baseline` unless specified.

## Checks

### 1. Spec validation

```bash
astra validate astra.yaml
```

### 2. Materialization status

```bash
lc status --universe <universe_id>
```

Every output should show `ok`. Flag anything `missing`, `stale`, or only present as an `alias`.

### 3. Decision-code alignment

**The most important check.** For every output, every decision listed in `Output.decisions` must:

1. Appear as a `{decisions.<id>}` placeholder in the same Output's recipe `command`. (`astra validate` enforces this; rerun if the spec changed.)
2. Be accepted as a parameter by the script the recipe invokes, with no hardcoded value.

Compare `astra info --decisions` against `grep -r "add_argument" scripts/` (or whatever the script's parsing convention is). Cross-check the recipe text to see how each decision is passed.

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
