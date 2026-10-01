# lightcone.engine.venue

Where a run executes. A venue is host state, never project state —
nothing here reads the project or enters any identity. The one venue
beyond the local machine is a SLURM allocation, detected rather than
configured: the user already answered every resource question at
`salloc`, so the allocation *is* the declaration and lc's job is to
span it.

Source: `src/lightcone/engine/venue.py` (consumed by
`materialize.cluster_for_run`).

## Key symbols

| Symbol | Role |
|---|---|
| `slurm_client()` | The allocation branch: a scheduler in the driver process bound to `SLURMD_NODENAME`, one `srun --overlap` launching a worker per node on `sys.executable`. |
| `require_compute_node(command)` | The login guard: refuses iff a known center's marker is set and `SLURM_JOB_ID` is not, printing that center's own `salloc`/`sbatch` spellings. |
| `allocation_nodes()` | How many nodes the allocation holds; 0 outside one. |
| `_SITES` | One row per known center — name, marker, remedies, **verified against the center's documentation, never guessed**. NERSC is the seeded row. |

## What must stay true

- **The detection ladder lives in `cluster_for_run()` alone.** Nothing
  else asks where a run executes; a future submission-model venue is
  one more branch there plus only the config it genuinely needs.
- **Workers run the driver's own interpreter** (`sys.executable -m
  distributed.cli.dask_worker`) — on HPC that is the tool env on the
  shared filesystem, so driver and workers are the identical
  installation and version skew is structurally out. Workers need no
  git and no annex.
- **The worker flags are each load-bearing**: `--nthreads=<cpus>`
  (tasks block in `subprocess.wait()` with the GIL released),
  `--no-nanny` (srun won't relaunch either), `--memory-limit 0` (the
  real work is behind the exec boundary; Dask would pause workers over
  phantom numbers), `--death-timeout 60` (a worker whose driver died
  exits instead of holding the node), `--local-directory /tmp`
  **literal** (a site prolog can scope `TMPDIR` per node or step, so a
  driver-resolved path can be absent elsewhere).
- **The srun child is the one documented exception to `project._run`**
  — it lives as long as the run and its stderr must reach the terminal
  live. Teardown retires workers first, then wait → terminate → kill,
  bounded; connection is a poll loop so a dead srun reports *its exit
  code* now, not a timeout later.
- **A leak refuses loudly, never falls back silently**: `SLURM_JOB_ID`
  with no srun on PATH, a non-integer count variable, an unresolvable
  `SLURMD_NODENAME` — each is a named refusal.
- **The guard is materialize-scoped** (plus the rerun entry point —
  the record's `cmd` is how recipes reach login nodes without `lc` in
  the command line). `check`, `status` and `lc run` never call it: a
  login node is exactly where "where does this stand" gets asked.
- **A containerized multi-node run requires a shared image store** —
  `_SHARED_STORE_RUNTIMES` (podman-hpc), asked positively, checked in
  `materialize()` before the runtime resolves so the refusal costs no
  build.

## Tests

`tests/test_venue.py` — fakes the *host*, never the code: SLURM
variables set deliberately, a bash stub standing in for srun, and the
end-to-end tests run a real graph through the real bind/launch/teardown
on any machine. The `venue_env` autouse fixture scrubs venue variables
suite-wide (derived from `_SITES`, so a new center is one row).
