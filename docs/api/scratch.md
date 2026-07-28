# lightcone.engine.scratch

One concept: **where lightcone keeps its operational state** — Snakemake
metadata, Dask worker spill, the run-exclusion lock. Resolved at the
start of every `lc run`.

Source: `src/lightcone/engine/scratch.py`.

## Why it exists

On NERSC, `$HOME` and `/global/cfs` are mounted on compute nodes via Cray
DVS, which [does not support file
locking](https://docs.nersc.gov/performance/io/dvs/). Snakemake's
workflow lock, our run-exclusion lock, and any future coordination
primitive silently no-op there, and small-file I/O is slow. `$SCRATCH` is
Lustre, which works correctly. On a JupyterHub deployment the problem is
different but the answer is the same shape: a pod's `/tmp` is pod-local,
so the state has to live on the NFS `$HOME` every worker pod mounts.

## `resolve_scratch_root(project_path) → Path`

First hit wins:

1. **`LIGHTCONE_SCRATCH`** env var (`LIGHTCONE_SCRATCH_ENV`) — the escape
   hatch / CI override.
2. **`scratch_root`** in `<project>/.lightcone/lightcone.yaml` — the
   per-project pin, written by `lc init --scratch`. An unparseable YAML
   file is treated as empty rather than raising.
3. **`scratch_root`** from the detected site
   ([`site_registry.detect_current_site()`](site_registry.md)), stored as
   a shell expression (`$SCRATCH`, `$HOME`) and expanded with
   `os.path.expandvars`. A `$VAR` that survives expansion means the env
   wasn't set — that falls through rather than writing to a literal path
   named `$SCRATCH`.
4. **`tempfile.gettempdir()`** — single-node fallback.

Always returns a `Path`; never raises. The path itself is not created —
use `prepare_run_dirs()` for that.

## `prepare_run_dirs(project_path, *, run_id=None) → RunDirs`

Creates and returns the per-run sub-directories. `run_id` defaults to the
current PID — unique per `lc run`, easily mapped back to a process.

```python
@dataclass(frozen=True)
class RunDirs:
    root: Path             # <scratch>/.lightcone
    snakemake_state: Path  # <scratch>/.lightcone/snakemake/<project-hash>/.snakemake
    dask_local: Path       # <scratch>/.lightcone/dask/<run-id>
    run_lock_path: Path    # <scratch>/.lightcone/locks/<project-hash>.run-lock
```

Snakemake state is **project-scoped** (persistent across invocations);
the dask-local dir and the lock are **run-scoped**. The lockfile is
touched on creation so `flock` never races on `O_CREAT`.

### `project_hash(project_path) → str`

12 hex chars of `sha256(absolute project path)`. Namespaces state under a
shared scratch root: two projects on one `$SCRATCH` get separate
`.snakemake/` dirs, and the same project moved to a different machine
gets fresh state.

## `ensure_snakemake_symlink(project_path, snakemake_state) → None`

Repoints `<project>/.snakemake` at `snakemake_state`, so Snakemake finds
its state at the canonical path while the bytes live on a filesystem that
honours `flock`.

- Symlink already pointing there → no-op.
- Symlink pointing elsewhere → replaced.
- A **real** directory (from a prior direct `snakemake` invocation) →
  renamed to `.snakemake.legacy` rather than deleted. Losing somebody
  else's job metadata silently is a worse failure than leaving a backup.
  If a backup already exists it is kept, and the current directory is
  removed instead.

## `acquire_run_lock(rundirs)` (context manager)

Holds an exclusive, **non-blocking** `flock` on
`<scratch>/.lightcone/locks/<project-hash>.run-lock` for the duration of
the run. A concurrent `lc run` on the same project raises
`RunLockBusyError` rather than queueing silently; `lc run` surfaces it as
a `ClickException`.

The kernel releases the lock when the holding process exits — clean
shutdown, crash, or SIGKILL — so a previously killed run can't deadlock
the next one. Once the lock is held, any leftover sentinel files under
`<snakemake_state>/locks/` are cleared: those aren't tied to a process
and would otherwise refuse the next workflow start. Safe to do here
precisely because we've just proven we're alone on the project.

### `RunLockBusyError`

Subclass of `RuntimeError`. Message names the lockfile path.

## Tests

`tests/test_scratch.py` walks the resolution chain (env var wins,
project config, site default including the fall-through when `$SCRATCH`
isn't set, tempdir fallback), the `RunDirs` layout and its per-project
separation, the three `ensure_snakemake_symlink` cases including the
`.snakemake.legacy` backup, and the run lock — stale-lock clearing and
rejection of a concurrent holder.

## Where it's called

`lc run` (`cli/commands.py`) resolves scratch before anything else:
`prepare_run_dirs()` → `ensure_snakemake_symlink()` → … →
`acquire_run_lock()` around the cluster context. `rundirs.dask_local`
becomes `cluster_for_run(local_directory=...)`, and `rundirs.root` is
where a failed run's snakemake stderr log is written.
