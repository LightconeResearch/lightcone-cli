# lightcone.engine.async_jobs

Coarse-grained SLURM submission, polling, and cancellation for
`lc run --async`.

Source: `src/lightcone/engine/async_jobs.py`.

The module resolves a requested ASTRA sub-DAG, aggregates its recipe
resources, selects a site policy, and renders one sbatch script. The script
re-enters ordinary `lc run`, so the async layer never duplicates recipe,
container, Dask, manifest, or validation logic.

Important entry points:

- `resolve_subdag_outputs()` — requested materializable outputs plus upstream
  recipe dependencies.
- `aggregate_job_resources()` — element-wise maximum node shape and padded
  serial walltime.
- `select_slurm_policy()` — deterministic site profile to `shared` or
  `regular`.
- `submit_job()` — render, `sbatch --parsable`, and persist a `JobRecord`.
- `refresh_job_records()` — batch-query `squeue`, then `sacct`.
- `cancel_job()` — resolve an active record, call `scancel`, and update it.

Project records live in `.lightcone/jobs/<job-id>.json`; logs live under the
resolved scratch root.
