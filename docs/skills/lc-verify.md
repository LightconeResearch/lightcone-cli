# /lc-verify

Read-only audit. Checks that `astra.yaml`, the code, and the
materialized results all agree.

Source: [`claude/lightcone/skills/lc-verify/SKILL.md`](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/lc-verify/SKILL.md).

## Allowed tools

```
Read, Glob, Grep,
Bash(astra:*), Bash(lc:*), Bash(python:*), Bash(ls:*),
AskUserQuestion
```

No `Write`, no `Edit`. The skill cannot modify the project.

## What it checks (per universe; default `baseline`)

1. **Spec validation** — `astra validate astra.yaml`. Fix and iterate
   until clean.
2. **Materialization status** — `lc status --universe <U>`. Every
   output should be `ok`. Anything `stale`, `missing`, or `alias`
   that's not expected gets flagged.
3. **Decision-code alignment** — *the core value*. For every decision
   in `astra.yaml`, confirm the code accepts it as a parameter rather
   than hardcoding the value. Cross-checks `astra info --decisions`
   against argparse usage in `scripts/`.
4. **Results match spec** — for every output, verify the result files
   exist and look well-formed. For `type: metric` outputs, check that
   each JSON file parses and contains a `{"value": …}` entry.

## Report format

```
| Check                    | Status |
|--------------------------|--------|
| Spec validation          | ✓/✗    |
| Materialization (N/N)    | ✓/✗    |
| Decision-code alignment  | ✓/⚠/✗  |
| Results match spec (N/N) | ✓/✗    |
```

The skill lists each finding with file paths and line numbers, and
suggests concrete fixes when something fails.

## Hard rules

- Read-only — never modifies files.
- One universe at a time.
- Never skips the decision-code alignment check.
- Always reads actual result files; never infers from code.

## Related

- [`/lc-build`](lc-build.md) — fix anything `/lc-verify` flags.
- [`lc verify`](../cli/verify.md) — the deeper, hash-based audit on the
  CLI side. They complement each other: the skill checks
  spec-vs-code-vs-results alignment; the CLI checks data integrity.
