You are inside a prism-build loop (universe: {{UNIVERSE}}). Each iteration: survey, work, commit, exit. The stop hook re-invokes you with this prompt until you're done.

## Survey

Run these commands and read their output:

1. `prism status --universe {{UNIVERSE}}` -- what's materialized, what's pending, what has no recipe
2. `git log --oneline -10` -- what happened recently
3. `astra validate astra.yaml` -- is the spec valid
4. Read `.prism/plans/build-plan-{{UNIVERSE}}.md` -- your implementation plan (cross off completed items as you go)

## Decide What to Do

**Follow the plan.** Read `.prism/plans/build-plan-{{UNIVERSE}}.md` and work on the next unchecked item. The plan was designed with the right ordering — shared utilities before scripts that use them, upstream outputs before downstream ones. Trust it.

If the plan is fully checked off or doesn't cover what `prism status` reveals, fall back to the status-based rules below.

### `astra validate` fails → Fix the spec first

Always fix validation errors before doing anything else. Commit. Exit.

### All outputs show `ok` → Verify & Complete

All outputs are materialized. Time to verify.

1. **Inline checks:**
   - `astra validate astra.yaml` passes
   - `prism status --universe {{UNIVERSE}}` shows all `ok`
   - Decision-code alignment: `grep -r "add_argument" scripts/` and compare against `astra info --decisions` — every decision must be a parameter, no hardcoded values
2. **If any issues found:** fix them, re-materialize if needed, commit. Exit (loop continues).
3. **If all clean:** Spawn a verification sub-agent with explicit steps (do not rely on skill dispatch — the sub-agent cannot invoke `/prism-verify` directly):
   ```
   Agent tool, subagent_type: general-purpose
   Prompt: "Verify the spec, code, and results all agree for universe {{UNIVERSE}}. Run these checks in order:
   1. Spec validation: run `astra validate astra.yaml` — must pass with no errors.
   2. Materialization status: run `prism status --universe {{UNIVERSE}}` — every output must show `ok`.
   3. Decision-code alignment (most important): run `astra info --decisions` and `grep -r 'add_argument' scripts/`. Every decision in the spec must be accepted as a CLI parameter in the code, with no hardcoded values.
   4. Results match spec: for every output in astra.yaml, confirm `results/{{UNIVERSE}}/<output_id>.<ext>` exists and looks well-formed. For `type: metric` outputs, check for valid `{'value': ...}` JSON.
   Report all findings with file paths and line numbers. If all checks pass, end your report with exactly: VERIFIED"
   ```
4. **If sub-agent reports issues:** fix them, commit. Exit (loop continues).
5. **If sub-agent says VERIFIED:** Output exactly: `<promise>BUILD_COMPLETE</promise>`, then clean up the build plan (`rm .prism/plans/build-plan-{{UNIVERSE}}.md`).

## Reference: How Work Gets Done

These are the kinds of work you'll do, guided by the plan. Not a rigid sequence — the plan determines the order.

### Writing scripts

1. **Write the script.** Parameterize all decisions from `astra.yaml` as command-line arguments (underscore convention: `stellar_mass_cut` → `--stellar_mass_cut`).
   The script must contain real, functional logic that produces genuine results from actual input data. No `# TODO` stubs, no hardcoded dummy values standing in for computation, no `pass` in place of real logic, no synthetic/mock data generation when real data is specified. If you cannot implement the full logic (e.g., missing a library or unclear algorithm), document the blocker in the build plan and move on — do not ship a fake version.
2. **Test locally:** `python scripts/<name>.py --decision1 value1 --decision2 value2` using values from `universes/{{UNIVERSE}}.yaml`.
   Note: manual script runs may write to `results/` but do NOT register as materialized.
   Only `prism run` creates the Dagster events that `prism status` recognizes.
3. **Debug until it works.** Read tracebacks, check imports (`python -c "import module"`), verify decision parameter names match `astra.yaml`.
4. **Commit** with a message describing what the script does.

### Adding recipes & materializing

1. **Add the recipe block** to `astra.yaml` under the output's `recipe:` key.
2. **Validate:** `astra validate astra.yaml`
3. **Check execution environment:** If the target is SLURM, check `echo $SLURM_JOB_ID`. If empty, you are on a login node — warn the user to start an interactive allocation (`salloc`) before running. Do not submit batch jobs during the build loop; interactive execution is required for fast iteration.
4. **Run it:** `prism run <OUTPUT> --universe {{UNIVERSE}}`
5. **If it fails:** Read the error output carefully and diagnose the root cause before retrying. Never re-run the same command without changing something first. Common causes:
   - Container not built → `prism build`
   - Upstream not materialized → materialize dependency first
   - Script error inside container → fix the script, then re-run
   If a second attempt also fails, note the failure in your commit message and in the build plan, then move on to other work. Come back to it in a later iteration with fresh context.
6. **If it succeeds:** Verify the result file exists at `results/{{UNIVERSE}}/<output_id>.<ext>` and looks well-formed.
7. **Commit** with a message noting what was materialized.

## Rules

**Work on 1-3 things per iteration.** Do NOT try to clear the entire queue. Exit after substantial progress so the next iteration gets fresh context.

**Exit before compaction.** Exit the iteration when ANY of these apply:
- You have materialized an output or made a failed attempt at one
- You have written or substantially modified more than 2 scripts
- A command produced more than ~200 lines of output
- You are on your 3rd or later `prism run` invocation this iteration
- You have read the same file more than once this iteration
Do not wait until context feels tight. Exit early and often — the next iteration gets fresh context and costs nothing.

**Commit messages are memory.** The next iteration discovers what you did via `git log`. Write descriptive commit messages.

**Trust the spec.** `astra.yaml` is the source of truth. Don't ask permission, don't second-guess decisions. Build what it says.

**Update the plan.** After completing work, edit `.prism/plans/build-plan-{{UNIVERSE}}.md` to cross off completed items and add notes about what you learned.

**Document blockers.** If you hit something you can't resolve (missing data, ambiguous spec, external dependency), add it to the Open Questions section in `CLAUDE.md` and move on to other work.

**No placeholders.** Every script must perform real computation on real data. Code that fakes results — hardcoded return values, TODO stubs, synthetic data standing in for real inputs — is worse than no code at all. If you cannot implement something fully, skip it and document why in the build plan.
