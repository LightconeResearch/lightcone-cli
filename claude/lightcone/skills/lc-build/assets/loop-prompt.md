You are inside a lc-build loop (universe: {{UNIVERSE}}). Each iteration: survey, work, commit, exit. The stop hook re-invokes you with this prompt until you're done.

## Survey

Run these commands and read their output:

1. `lc status --universe {{UNIVERSE}}` -- what's materialized (`ok`), missing, stale, or an alias of an upstream output
2. `git log --oneline -10` -- what happened recently
3. `astra validate astra.yaml` -- is the spec valid
4. Read `.lightcone/plans/build-plan-{{UNIVERSE}}.md` -- your implementation plan (cross off completed items as you go)

## Decide What to Do

**Follow the plan.** Read `.lightcone/plans/build-plan-{{UNIVERSE}}.md` and work on the next unchecked item. The plan was designed with the right ordering — shared utilities before scripts that use them, upstream outputs before downstream ones. Trust it.

If the plan is fully checked off or doesn't cover what `lc status` reveals, fall back to the status-based rules below.

### `astra validate` fails → Fix the spec first

Always fix validation errors before doing anything else. Commit. Exit.

### All outputs show `ok` → Verify & Complete

All outputs are materialized. Time to verify.

1. **Inline checks:**
   - `astra validate astra.yaml` passes (this also catches undeclared `{decisions.X}` / `{inputs.X}` placeholders in recipes)
   - `lc status --universe {{UNIVERSE}}` shows all `ok`
   - Decision-code alignment: every decision listed in any `Output.decisions` must be (a) referenced by a `{decisions.<id>}` placeholder in the same Output's recipe command, AND (b) accepted as a parameter by the script the recipe invokes. `astra info --decisions` lists the decisions; `grep -r "add_argument" scripts/` (or whatever the script's parsing convention is) confirms the script side. No hardcoded values.
2. **If any issues found:** fix them, re-materialize if needed, commit. Exit (loop continues).
3. **If all clean:** Spawn a verification sub-agent with explicit steps (do not rely on skill dispatch — the sub-agent cannot invoke `/lc-verify` directly):
   ```
   Agent tool, subagent_type: general-purpose
   Prompt: "Verify the spec, code, and results all agree for universe {{UNIVERSE}}. Run these checks in order:
   1. Spec validation: run `astra validate astra.yaml` — must pass with no errors.
   2. Materialization status: run `lc status --universe {{UNIVERSE}}` — every output must show `ok`.
   3. Decision-code alignment (most important): run `astra info --decisions`. For every output that declares decisions in `Output.decisions`, confirm (a) the recipe's `command` references each one via a `{decisions.<id>}` placeholder, and (b) the invoked script accepts each as a parameter (typically via `add_argument`). No hardcoded option values. `astra validate` flags placeholder/declaration mismatches; this check covers the script side.
   4. Results match spec: for every output in astra.yaml, confirm `results/{{UNIVERSE}}/<output_id>.<ext>` exists and looks well-formed. For `type: metric` outputs, check for valid `{'value': ...}` JSON.
   Report all findings with file paths and line numbers. If all checks pass, end your report with exactly: VERIFIED"
   ```
4. **If sub-agent reports issues:** fix them, commit. Exit (loop continues).
5. **If sub-agent says VERIFIED:** Output exactly: `<promise>BUILD_COMPLETE</promise>`, then clean up the build plan (`rm .lightcone/plans/build-plan-{{UNIVERSE}}.md`).

## Reference: How Work Gets Done

These are the kinds of work you'll do, guided by the plan. Not a rigid sequence — the plan determines the order.

### Writing scripts

1. **Write the script.** Make every decision the recipe will pass into it a real CLI parameter (typically `argparse`). The recipe's `command` template is what wires decisions to the script — e.g. `python scripts/fit.py --stellar_mass_cut {decisions.stellar_mass_cut} --output {output}` paired with `parser.add_argument('--stellar_mass_cut')`. Pick the script-side flag names so they match the recipe template; the spec doesn't dictate them.
   The script must contain real, functional logic that produces genuine results from actual input data. No `# TODO` stubs, no hardcoded dummy values standing in for computation, no `pass` in place of real logic, no synthetic/mock data generation when real data is specified. If you cannot implement the full logic (e.g., missing a library or unclear algorithm), document the blocker in the build plan and move on — do not ship a fake version.
2. **Test locally:** invoke the script with concrete values, e.g. `python scripts/<name>.py --decision1 value1 --decision2 value2 --output /tmp/check`, using values from `universes/{{UNIVERSE}}.yaml`.
   Note: manual script runs may write to `results/` but do NOT register as materialized.
   Only `lc run` produces the per-output manifests that `lc status` reads.
3. **Debug until it works.** Read tracebacks, check imports (`python -c "import module"`), verify the recipe template's `{decisions.<id>}` / `{inputs.<id>}` references match `Output.decisions` / `Output.inputs` (decision/input id, not script flag name).
4. **Commit** with a message describing what the script does.

### Adding recipes & materializing

1. **Wire the output.** On the `Output` itself, list its dependencies in `inputs: [...]` and the decisions it consumes in `decisions: [...]`. Then add the `recipe:` block with a `command` template referencing each via `{inputs.<id>}` and `{decisions.<id>}` (and `{output}` for the artifact directory). `astra validate` rejects placeholders that don't match what the Output declares.
2. **Validate:** `astra validate astra.yaml`
3. **Run it:** `lc run <OUTPUT> --universe {{UNIVERSE}}`. `lc run` is the only supported way to execute recipes — it handles container resolution, scheduling, and provenance. Run the loop on a machine that can actually execute recipes (the user's laptop with a container runtime, or a compute session they've already opened).
4. **If it fails:** Read the error output carefully and diagnose the root cause before retrying. Never re-run the same command without changing something first. Common causes:
   - Container not built → `lc build`
   - Upstream not materialized → materialize dependency first
   - Script error inside container → fix the script, then re-run
   If a second attempt also fails, note the failure in your commit message and in the build plan, then move on to other work. Come back to it in a later iteration with fresh context.
5. **If it succeeds:** Verify the result file exists at `results/{{UNIVERSE}}/<output_id>/` and looks well-formed.
6. **Commit** with a message noting what was materialized.

## Rules

**Work on 1-3 things per iteration.** Do NOT try to clear the entire queue. Exit after substantial progress so the next iteration gets fresh context.

**Exit before compaction.** Exit the iteration when ANY of these apply:
- You have materialized an output or made a failed attempt at one
- You have written or substantially modified more than 2 scripts
- A command produced more than ~200 lines of output
- You are on your 3rd or later `lc run` invocation this iteration
- You have read the same file more than once this iteration
Do not wait until context feels tight. Exit early and often — the next iteration gets fresh context and costs nothing.

**Commit messages are memory.** The next iteration discovers what you did via `git log`. Write descriptive commit messages.

**Trust the spec.** `astra.yaml` is the source of truth. Don't ask permission, don't second-guess decisions. Build what it says.

**Update the plan.** After completing work, edit `.lightcone/plans/build-plan-{{UNIVERSE}}.md` to cross off completed items and add notes about what you learned.

**Document blockers.** If you hit something you can't resolve (missing data, ambiguous spec, external dependency), add it to the Open Questions section in `CLAUDE.md` and move on to other work.

**No placeholders.** Every script must perform real computation on real data. Code that fakes results — hardcoded return values, TODO stubs, synthetic data standing in for real inputs — is worse than no code at all. If you cannot implement something fully, skip it and document why in the build plan.
