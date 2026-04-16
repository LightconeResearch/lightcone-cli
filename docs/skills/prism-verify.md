# /prism-verify

Verify `astra.yaml`, code, and results for consistency and completeness.

## Purpose

`/prism-verify` is a read-only audit skill. It checks the analysis from four angles and produces a structured verification table.

## Checks performed

### 1. Spec validation

Runs `astra validate` and reports any schema errors in `astra.yaml`.

### 2. Recipe coverage

Checks that every declared output either has a `recipe:` block or a valid `from:` reference. Reports outputs with `no_recipe` status.

### 3. Decision–code alignment

**The most important check.** For every `decision:` entry in `astra.yaml`:

- Finds the code that implements it (by searching for the decision key in `scripts/`).
- Verifies that the actual implementation matches the documented options.
- Flags discrepancies where the spec says one thing but the code does another.

### 4. Results verification

Checks that materialised outputs contain non-empty, readable files. Flags outputs that are recorded as materialised in Dagster but whose result directories are empty or missing.

## Output format

```
## Verification Results

| Check | Status | Notes |
|-------|--------|-------|
| astra validate | ✓ | |
| Recipe coverage | ✓ | 4/4 outputs have recipes |
| Decision–code alignment | ✗ | `smoothing_kernel`: spec says [gaussian, tophat], code only implements gaussian |
| Results | ✓ | All 4 materialised outputs have non-empty results |
```

## Key rules

- This skill is **read-only** — it never modifies files.
- Decision–code alignment is the most likely source of reproducibility failures; always run this before publishing.

## Related

- [prism-build](prism-build.md) — fix any issues found by verify
