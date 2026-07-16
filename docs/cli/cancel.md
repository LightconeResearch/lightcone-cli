# lc cancel

Cancel a recorded asynchronous SLURM job.

## Synopsis

```text
lc cancel <JOB-ID | OUTPUT-ID>
```

`lc cancel` refreshes recorded state, resolves the reference to one active
job, calls `scancel`, and stores `CANCELLED` in the job record. An output id
may refer to either an explicitly requested target or one of its resolved
upstream dependencies. If it matches multiple active jobs, cancel by job id
to disambiguate.

```bash
lc cancel 1234567
lc cancel heavy_fit
```

Only jobs recorded under the current project's `.lightcone/jobs/` directory
are managed.
