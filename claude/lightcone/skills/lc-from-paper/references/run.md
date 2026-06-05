# RUN — materialize the DAG

The RUN phase of `reproduce_workflow.js`. One agent drives every recipe in `astra.yaml` to a materialized output for the `baseline` universe, shepherds the run to completion, and clears any failures before VERIFY. It is mostly mechanical — `lc run` does the heavy lifting over the Snakemake DAG — but it is a discrete phase so failures get diagnosed and fixed in a bounded context, not folded into verification.

By now IMPLEMENT's merge step has written recipes into `astra.yaml` and `scripts/<output>.py` for every output, and reconciled `requirements.txt`. Your job is to make the DAG green.

## Contract

- **Read:** `astra.yaml` (recipes), `universes/baseline.yaml`, `CLAUDE.md` (runner backend / container).
- **Do:** run the DAG, iterate on failures, commit.
- **Gate (deterministic):** `lc status --universe baseline` reports every output `ok`.
- **Report back:** the final `lc status` — which outputs reached `ok`, and any failure you logged rather than fixed.

You do not edit `astra.yaml`'s structure. You may edit `scripts/<output>.py`, `requirements.txt`, or a recipe's `inputs:`/`command:` when a run-failure demands it — those are the implementation, and fixing them is what RUN is for.

## Task

Run the recipes over the DAG:

```bash
lc run --universe baseline
```

`lc run` invokes Snakemake — it resolves the DAG, runs outputs in dependency order with whatever parallelism the profile allows, and skips outputs already materialized. Re-runs are idempotent: a second `lc run` re-executes nothing that is already `ok`.

Check the gate:

```bash
lc status --universe baseline
```

Status states: `ok` (materialized), `stale` (inputs or recipe changed since the last run — re-run it), `missing` (declared but not yet materialized — run it; a `missing` output that has *no recipe at all* is an IMPLEMENT bug, send it back), `alias` (a re-export pointer to another output). Every output must reach `ok`.

## Shepherding long / cluster jobs

Do **not** sit in a `lc status` poll loop. The recipe streams stdout/stderr into the result directory's `.log`; use the **Monitor** tool on that log to stream events — each line surfaces as a notification, and Monitor re-invokes you when the job exits. For a one-shot wait on a backgrounded `lc run`, `Bash` with `run_in_background` notifies on completion. Cluster runs (SLURM backend) can take a while; the backend comes from `~/.lightcone/config.yaml` + `.lightcone/lightcone.yaml`, not from you.

## The fix loop

When an output fails:

1. **Read the error.** `results/baseline/<output>/.log` (Snakemake's per-rule log) carries the message — start there, not from a guess.
2. **Diagnose.** Recurring causes: a missing data dependency (a URL moved, an archive relocated — real data only, so chase the live source); a missing package (`requirements.txt` was incomplete); a spec/script mismatch (the recipe's `inputs:` doesn't match what the script reads); a container missing a system lib.
3. **Fix** the script, `requirements.txt`, or the recipe — whichever the `.log` indicts.
4. **Re-run** `lc run --universe baseline`. It resumes from the failure; already-materialized outputs are untouched.
5. **Repeat** until `lc status` is all-`ok`.

When the gate is green, commit (`"run: materialize baseline outputs"`) and report the final status. The `results/` artifacts are gitignored heavy data, but the commit records that RUN landed so a resuming session reads it from `git log`.

## Rules

- **Always go through `lc run`** — never invoke a `scripts/<output>.py` directly. The runner owns dependency order, the container/SLURM environment, and the result paths and manifests; bypassing it produces outputs VERIFY can't trust.
- **Real data only.** A failing data dependency gets chased to its real source — never stubbed, sampled to a toy, or synthesized to make the recipe pass (unless the paper itself uses synthetic input).
- **Failures stay failures until fixed.** Do not "move on" by deleting an output from `astra.yaml`. Fix the implementation, or — if the failure is a genuine paper/data gap you can't resolve — log it to `open-questions.md` with the `.log` excerpt and leave the output `missing`. VERIFY and the human see it at close-out; you do not silently drop it.
- **Don't widen scope.** RUN materializes what IMPLEMENT wrote. A recipe that's missing or wrong is an IMPLEMENT bug — fix the minimal implementation detail, don't re-architect the output.
