# Adding a Site

A *site* is a machine or deployment lightcone-cli can recognise and adapt
to. Sites are declared in `SITE_DEFAULTS` in
`src/lightcone/engine/site_registry.py` — see
[api/site_registry](../api/site_registry.md) for the full field
reference. Three consumers read them today: `lc init` (surfacing the
scratch root), [`engine.scratch`](../api/scratch.md) (resolving it), and
`engine.container` (picking the runtime).

## 1. Declare the site

Append an entry to `SITE_DEFAULTS`. How it gets *detected* is the first
decision:

- **A machine you can recognise by name** → `hostname_patterns`,
  substring-matched against `socket.gethostname()`. Perlmutter uses
  `["perlmutter", "saul"]`.
- **A deployment you can only recognise by its environment** →
  `env_markers`, a list of env var names that must all be set. The
  `jupyterhub` site uses `["DASK_GATEWAY__ADDRESS"]`, because pod
  hostnames carry no information.

Env markers win over hostname patterns in `detect_current_site()`.

## 2. Pick a container runtime

`container_runtime` is moved to the front of the detection order, so it
wins on this site whenever the binary exists (it's a hint — a missing
binary falls through). `podman-hpc` is the supported HPC case: rootless,
and its `migrate` step makes images readable from compute nodes without a
registry. A Kubernetes deployment with a remote builder declares
`kubernetes` — see [Adding an Execution Backend](backends.md) for why
that one is not a normal runtime. Users can still override in
`~/.lightcone/config.yaml`.

## 3. Declare a scratch root

`scratch_root` is where `lc run` keeps Snakemake metadata, Dask spill,
and the run lock. Store it as a **shell expression** (`$SCRATCH`,
`$HOME`): it is expanded with `os.path.expandvars` at run time, and if
the variable isn't set, resolution falls through rather than writing to a
literal path named `$SCRATCH`.

What it must be is a filesystem that honours `flock` and is visible to
every process in the run. On NERSC that rules out DVS-mounted `$HOME` and
CFS, leaving Lustre `$SCRATCH`. On a JupyterHub deployment it rules out
the pod-local `/tmp`, leaving the shared NFS `$HOME`.

## 4. Guard shared filesystems from the agent

`scratch_paths` feeds `get_site_scratch_deny_rules()`, which isn't wired
into `lc init` today — the equivalent patterns are listed inline in
`PERMISSION_TIERS` in `src/lightcone/cli/commands.py`. Note they sit
under `ask` (prompt before writing), not `deny`: projects on HPC often
legitimately live in `$SCRATCH`, so an outright block would be wrong.

```python
"ask": [
    "Edit(//scratch/**)",
    "Edit(//pscratch/**)",
    "Write(//scratch/**)",
    "Write(//pscratch/**)",
],
```

Add your site's paths there — or wire `get_site_scratch_deny_rules()`
into `_install_claude_plugin()` and merge the result, which is the
version that scales past two sites.

## 5. Check the cluster shape

`lc run` already does the right thing inside an `salloc`/`sbatch`
allocation: the cluster manager binds the scheduler to the SLURM
canonical hostname and launches one worker per node via `srun`. See
[api/dask_cluster](../api/dask_cluster.md). If your site needs a shape
that isn't one of the four existing branches, see
[Adding an Execution Backend](backends.md).

## 6. Test it

`tests/test_site_registry.py` covers detection precedence and the
declared-field lookups; add a case for the new entry.
