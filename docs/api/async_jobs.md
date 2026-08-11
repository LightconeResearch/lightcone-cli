# lightcone.engine.async_jobs

Coarse-grained SLURM submission, polling, and cancellation for
`lc run --async`.

Source: `src/lightcone/engine/async_jobs.py`.

The module resolves an explicitly requested ASTRA sub-DAG, filters it to work
whose manifests or inputs are missing or stale, aggregates those recipes'
resources, selects a site policy, and renders one sbatch script. The script
re-enters ordinary `lc run`, so the async layer never duplicates recipe,
container, Dask, manifest, or validation logic.

Important entry points:

- `resolve_subdag_outputs()` — requested materializable outputs plus upstream
  recipe dependencies.
- `pending_subdag_outputs()` — propagate missing/stale work through that
  sub-DAG and omit already-current dependencies from allocation sizing.
- `aggregate_job_resources()` — element-wise maximum node shape and padded
  serial walltime.
- `select_slurm_policy()` — deterministic site profile to `shared` or
  `regular`.
- `submit_job()` — require explicit targets and a configured async site before
  reading submission configuration or recipe resources. Unknown sites are
  probed with `sinfo` to distinguish unavailable SLURM from a reachable but
  unsupported scheduler; then render, call `sbatch --parsable` without inherited
  parent-allocation variables, and persist a `JobRecord`.
- `refresh_job_records()` — batch-query `squeue`, then `sacct`.
- `cancel_job()` — resolve an active record, call `scancel`, and update it.

Project records live in `.lightcone/jobs/<job-id>.json`; logs live under the
resolved scratch root.
