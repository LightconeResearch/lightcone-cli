# /prism-migrate

Migrate existing research code into ASTRA/Prism format.

## Purpose

`/prism-migrate` is used after `prism init --existing-project` to analyse existing code and generate a complete `astra.yaml` that accurately reflects what the code does.

## Workflow phases

### Phase 1 — Scan & Spec

1. Reads all existing scripts to understand inputs, outputs, and methodology.
2. Identifies decision points (parameters with multiple valid choices).
3. Drafts `astra.yaml` with inputs, outputs, and decisions extracted from the code.
4. Asks for clarification on anything ambiguous.

### Phase 2 — Implement

1. Adds `recipe:` blocks to each output, pointing to the existing scripts.
2. Modifies scripts to accept `--universe`, `--key value` CLI arguments where needed.
3. Creates the universe file(s) based on the parameters currently hardcoded in the code.
4. Writes `Containerfile` and `requirements.txt` if not already present.

### Phase 3 — Run & Debug

1. Runs `prism run` and fixes any issues.
2. Verifies that all outputs materialise correctly.
3. Runs `/prism-verify` to confirm spec–code alignment.

## Key rules

- Never fabricate decisions not present in the original code.
- Existing logic must be preserved — migration is not a refactor.
- If the code uses hardcoded parameters, those become decision options.
- Keep script changes minimal: only add CLI argument parsing.

## Related

- [prism-new](prism-new.md) — for creating analyses from scratch
- [prism-verify](prism-verify.md) — run after migration to confirm consistency
