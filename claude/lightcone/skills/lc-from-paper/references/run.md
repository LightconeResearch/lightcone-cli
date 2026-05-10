# RUN — execute the recipes

Materialize every output in `astra.yaml` for the requested universe. RUN is mostly mechanical — `lc run --universe <id>` does the heavy lifting. The phase exists as a discrete step so failures get diagnosed and re-run before COMPARE.

This phase runs as the orchestrator-spawned `run` sub-agent. The user can drop into its chat if execution failures want diagnosis support; otherwise it logs failures, attempts targeted fixes within scope, and reports back. Universe defaults to `baseline` unless the orchestrator passes a different one when spawning.

## Inputs

- `astra.yaml` with recipes (from IMPLEMENT)
- `universes/<universe_id>.yaml` — defaults to `baseline`

## Outputs

- `results/<universe_id>/<output_id>/` for every output declared in `astra.yaml`

## Task

Execute all recipes:

```bash
lc run --universe baseline
```

(Use whatever universe the orchestrator passed when spawning; `baseline` is the default.)

Check status:

```bash
lc status --universe baseline
```

Status states are `ok` (materialized), `pending` (has recipe, not run), `no_recipe` (declared, no recipe — bug). Every output declared in `astra.yaml` must reach `ok`.

If outputs fail:

1. **Read the script's error.** `results/<universe>/<output>/.log` (or wherever the runner emits stderr) usually has the message.
2. **Diagnose.** Common failures: missing data dependency (a referenced URL changed; the data archive moved), missing Python package (`requirements.txt` was incomplete), spec / script mismatch (the recipe's `inputs:` does not match what the script reads).
3. **Fix.** Edit the script or `requirements.txt` or the spec, whichever applies.
4. **Re-run.** `lc run --universe baseline` resumes from where things failed; it does not re-execute already-materialized outputs.
5. **Repeat** until all outputs are `ok`.

## Rules

- **Always use `lc run`** — do not run scripts directly. The runner manages dependencies, environments, and artifact paths; bypassing it produces inconsistent results.
- **Re-runs are idempotent.** `lc run` skips outputs that are already materialized. To force re-execution, the runner has a flag for that — check `lc run --help`.
- **Failures stay failures until fixed.** Do not "move on" past a failed output by editing it out of `astra.yaml`. Either fix the script, ask the user in prose if reachable, or log the failure to `open-questions.md` and stop.

## Survey signals (entry into RUN)

- `astra.yaml` has recipes and validates ⇒ ready to run
- `lc status --universe baseline` returns all `ok` ⇒ RUN done; orchestrator proceeds to COMPARE

## Notes

- The runner backend (Docker / local / SLURM) comes from the project's target configuration — `~/.lightcone/config.yaml` and `.lightcone/lightcone.yaml`. RUN does not need to choose; the runner picks based on config.
- For long-running computations, the script's stdout / stderr stream into the result directory's log file. The run sub-agent should `tail` the log file to monitor progress, not poll `lc status` repeatedly.
- **Commit the materialized results' state when RUN settles.** The actual `results/` artifacts are gitignored heavy data, but the run-level outcome (which outputs reached `ok`, any failures logged) is worth a commit so the orchestrator can read `git log` to know RUN landed.
