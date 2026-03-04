---
name: prism-verify
description: Verify a completed ASTRA analysis — check that results exist, decisions match code, success criteria are met, and the spec is up to date. Run after building an analysis.
allowed-tools: Read, Glob, Grep, Bash(astra:*), Bash(python:*), Bash(ls:*), Bash(cat:*), AskUserQuestion
---

# /prism-verify

Verify a completed ASTRA analysis. Checks that implementation matches specification, results are present and valid, and success criteria are met.

## References

- [UI Brand](./../ui-brand.md) — visual formatting patterns

## Setup

1. Read `astra.yaml`
2. Read universe file — default `universes/baseline.yaml`, or user-specified
3. Read `CLAUDE.md` for project context
4. Ask: "Should I also check success criteria against results, or just verify spec-implementation alignment?"

Stage banner: VERIFY — <universe_id>

Run all checks. Collect findings and present the full report at the end.

---

## Check 1: Schema & Semantic Validation

```bash
astra validate astra.yaml
astra universe check universes/<universe_id>.yaml
```

Record pass/fail. Continue with remaining checks even if validation fails.

---

## Check 2: Result Files

For every **output**, check that `results/<universe_id>/<output_id>.<ext>` exists. Record each as present or missing.

---

## Check 2.5: Materialization Status

```bash
prism status --universe <universe_id>
```

Cross-reference `prism status` output with the file-based check above:

1. **Status consistency** — if `prism status` shows "ok" but files are missing (or vice versa), flag the inconsistency
2. **Universe file exists** — `universes/<universe_id>.yaml` is present and valid

---

## Check 4: Metric Validation

For each `type: metric` output where the result file exists:

1. Check it has `{"value": ...}` JSON structure
2. Check the value is a valid number (int or float)

---

## Check 5: Decision-Code Alignment

**The most important check.** For the analysis:

1. Read implementation plans and code
2. Check that the code accepts parameters for each decision and that no decision values are hardcoded
3. Flag: hardcoded decision values, missing parameters for decisions

Be pragmatic — the code may parse option IDs into internal representations (e.g., `"w10_s5"` → `width=10, stride=5`). That's fine as long as the parsing is driven by the parameter value.

---

## Check 6: Success Criteria (optional)

**Skip if the user opted out during Setup.**

For each success criterion in `astra.yaml`:
1. If the criterion has `output` and `condition` fields, read the referenced output's result file and evaluate the condition. Report pass/fail.
2. If the criterion has only a `claim` (no `output` reference), use judgment: check if any result can verify it. Note "needs manual review" for qualitative criteria.
3. If no results relate to it, flag as unverifiable.

---

## Check 7: Spec Freshness

Scan for drift between spec and implementation:

1. **Undeclared outputs** — files in `results/<universe_id>/` not declared as outputs
2. **Stale descriptions** — implementation plans describe an approach that diverges from astra.yaml

Only flag things you're reasonably confident about.

---

## Report

Stage banner: VERIFICATION REPORT — <universe_id>

### Summary Table

```
| Check                    | Status |
|--------------------------|--------|
| Schema validation        | ✓      |
| Semantic validation      | ✓      |
| Result files (5/5)       | ✓      |
| Materialization status   | ✓      |
| Metric validation (2/2)  | ✓      |
| Decision-code alignment  | ⚠      |
| Success criteria (2/3)   | ⚠      |
| Spec freshness           | ✓      |
```

Omit success criteria row if skipped. Status: ✓ passed, ⚠ warnings, ✗ failures.

### Findings

List each finding grouped by check:

```
**Decision-Code Alignment**
⚠ `main.scaling` — universe selects `standard` but code imports MinMaxScaler
  at scripts/preprocess.py:42

**Success Criteria**
⚠ "Model size under 10MB" — no metric declared, verify manually
✓ "Accuracy > 95%" — accuracy.json reports 0.97
```

### Suggested Fixes

If there are warnings or failures, show an action prompt (see ui-brand.md) titled "SUGGESTED FIXES" with a numbered list of concrete fixes.

If everything passes, show a Next Up block confirming the analysis is verified for this universe.

---

## Rules

- **Read-only** — report findings and suggest fixes, never modify files
- **One universe at a time** — run again for additional universes
- **Pragmatic** — flag real problems, not style differences
- **Never skip Check 5** — decision-code alignment is the core value of this skill
- **Always read actual files** — don't assume metrics pass based on code logic
