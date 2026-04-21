# /lc-build

Implement, run, and debug an ASTRA analysis until all outputs are materialised.

## Purpose

`/lc-build` is the main implementation skill. It takes a scoped `astra.yaml` (from `/lc-new` or `/lc-migrate`) and drives the cycle of writing scripts, running them via `lc run`, fixing failures, and verifying results until the full analysis is complete.

## Workflow phases

### Phase 0 — Crash recovery

Before starting, checks for a `.lightcone/build-plan.md` from a previous session. If found, resumes from the last known state rather than starting over.

### Phase 1 — Setup & Plan

1. Reads `astra.yaml` and the full analysis spec.
2. Runs `lc status` to see what's already done.
3. Checks that `.venv/` exists and dependencies are installed.
4. Writes `.lightcone/build-plan.md` with the implementation plan, mapping outputs to scripts.
5. Reads `astra-reference.md` and `lightcone-cli-reference.md` before writing any code.

### Phase 2 — Activate Loop (repeat until complete)

For each pending output, in dependency order:

1. **Write** the implementation script in `scripts/`.
2. **Run** `lc run {output_id}` to materialise it.
3. **Verify** the output was created and is non-empty.
4. **Fix** any failures iteratively.
5. Check off the output in `.lightcone/build-plan.md`.
6. Move to the next output.

## Key rules

- Always use `lc run` to execute recipes — never `python scripts/foo.py` directly.
- Write scripts that accept `--universe`, `--key value` CLI arguments for universe decision injection.
- Use `results/{universe_id}/{output_id}/` as the output directory (available as `$ASTRA_OUTPUT_DIR`).
- Never materialise outputs without verifying the previous dependency chain.
- Commit after each successfully materialised output.

## Plan file format

`.lightcone/build-plan.md` tracks progress across sessions:

```markdown
# Build Plan

## Outputs
- [ ] preprocessing — scripts/preprocess.py
- [x] accuracy — scripts/compute_accuracy.py
- [ ] conclusion — scripts/summarize.py
```

## Related

- [lc-verify](lc-verify.md) — run after build to check consistency
- `claude/lightcone/guides/lightcone-cli-reference.md` — CLI and execution reference
