# /prism-build

Implement, run, and debug an ASTRA analysis until all outputs are materialised.

## Purpose

`/prism-build` is the main implementation skill. It takes a scoped `astra.yaml` (from `/prism-new` or `/prism-migrate`) and drives the cycle of writing scripts, running them via `prism run`, fixing failures, and verifying results until the full analysis is complete.

## Workflow phases

### Phase 0 — Crash recovery

Before starting, checks for a `.prism/build-plan.md` from a previous session. If found, resumes from the last known state rather than starting over.

### Phase 1 — Setup & Plan

1. Reads `astra.yaml` and the full analysis spec.
2. Runs `prism status` to see what's already done.
3. Checks that `.venv/` exists and dependencies are installed.
4. Writes `.prism/build-plan.md` with the implementation plan, mapping outputs to scripts.
5. Reads `astra-reference.md` and `prism-reference.md` before writing any code.

### Phase 2 — Activate Loop (repeat until complete)

For each pending output, in dependency order:

1. **Write** the implementation script in `scripts/`.
2. **Run** `prism run {output_id}` to materialise it.
3. **Verify** the output was created and is non-empty.
4. **Fix** any failures iteratively.
5. Check off the output in `.prism/build-plan.md`.
6. Move to the next output.

## Key rules

- Always use `prism run` to execute recipes — never `python scripts/foo.py` directly.
- Write scripts that accept `--universe`, `--key value` CLI arguments for universe decision injection.
- Use `results/{universe_id}/{output_id}/` as the output directory (available as `$ASTRA_OUTPUT_DIR`).
- Never materialise outputs without verifying the previous dependency chain.
- Commit after each successfully materialised output.

## Plan file format

`.prism/build-plan.md` tracks progress across sessions:

```markdown
# Build Plan

## Outputs
- [ ] preprocessing — scripts/preprocess.py
- [x] accuracy — scripts/compute_accuracy.py
- [ ] conclusion — scripts/summarize.py
```

## Related

- [prism-verify](prism-verify.md) — run after build to check consistency
- `claude/prism/guides/prism-reference.md` — CLI and execution reference
