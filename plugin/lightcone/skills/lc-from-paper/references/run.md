# RUN — execute the recipes

Materialize every output in `astra.yaml` for the requested universe. RUN is mostly mechanical — `lc run --universe <id>` does the heavy lifting. The phase exists as a discrete step so failures get diagnosed and re-run before COMPARE.

RUN is what a ralph iteration does when the workdir signals "recipes present in `astra.yaml` + `scripts/` committed + `results/<universe>/<output>/` absent for any output." The iteration runs the recipes, diagnoses failures, attempts targeted fixes, and exits. Universe defaults to `baseline`.

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

(Universe defaults to `baseline`; iterations override if the constitution scopes a different universe.)

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
- `lc status --universe baseline` returns all `ok` ⇒ RUN done; the next iteration surveys and advances to COMPARE

## Notes

- The runner backend (Docker / local / SLURM) comes from the project's target configuration — `~/.lightcone/config.yaml` and `.lightcone/lightcone.yaml`. RUN does not need to choose; the runner picks based on config.
- For long-running computations, the script's stdout / stderr stream into the result directory's log file. The iteration should use the Monitor tool on the log file to stream events (each stdout line surfaces as a notification), not poll `lc status` repeatedly. For one-shot waits, Bash with `run_in_background` notifies on completion.
- **Commit the materialized results' state when RUN settles.** The actual `results/` artifacts are gitignored heavy data, but the run-level outcome (which outputs reached `ok`, any failures logged) is worth a commit so the next iteration can read `git log` to know RUN landed.
