# Running on a Cluster

When local laptop time isn't enough, the same project runs on a SLURM
HPC system. There is no separate configuration to learn and no flag to
pass — `lc materialize` detects where it is running, and the allocation
you request *is* the resource declaration.

## The big picture

`lc materialize` runs its tasks through a scheduler, and picks the venue
by looking at the environment:

1. **Inside a SLURM allocation** (`SLURM_JOB_ID` is set) → the run
   spans every node the allocation holds: one worker per node, launched
   via `srun`, using every core it was granted.
2. **Anywhere else** → the local machine, using every core.

You already answered every sizing question at `salloc` / `sbatch` —
how many nodes, which constraint, how long — so `lc` asks none of its
own. There is no `--jobs`, no worker count, no venue config file.

## A typical SLURM workflow

### 1. Prepare on the login node

Everything except executing recipes works on a login node — and one
verb is *for* it:

```bash
cd $SCRATCH/my-analysis
lc materialize --check     # what would run, and why
lc status                  # where every output stands
lc build                   # containerized projects: build + commit the image
```

### 2. Get an allocation and materialize inside it

=== "Interactive"
    ```bash
    salloc --nodes=1 --constraint=cpu --qos=interactive --time=02:00:00
    # salloc drops you onto a compute node; from there:
    cd $SCRATCH/my-analysis
    lc materialize
    ```

=== "Batch"
    ```bash
    cd $SCRATCH/my-analysis
    sbatch --nodes=1 --constraint=cpu --qos=regular --time=02:00:00 \
        --wrap 'lc materialize'
    ```

    (Make sure `lc` is on `PATH` in the batch environment — with a
    `uv tool install`, that's `export PATH=$HOME/.local/bin:$PATH` in
    the script if your shell profile doesn't already do it.)

Ask for more nodes and the run uses them — independent outputs and
universes spread across the allocation with nothing else to say.

### 3. Guard rails on known centers

On centers `lc` knows (NERSC today), running `lc materialize` on a
login node refuses with the center's own allocation spellings rather
than quietly hammering a shared node:

```
Error: lc materialize executes recipes on compute nodes, and this is a
NERSC login node (NERSC_HOST is set with no SLURM allocation active).

Get an allocation and run it there:

  interactive:
      salloc --nodes=1 --constraint=cpu --qos=interactive --time=02:00:00
      lc materialize

  batch (from the project root):
      sbatch --nodes=1 --constraint=cpu --qos=regular --time=02:00:00 \
          --wrap 'lc materialize'

lc materialize --check, lc status and lc run work anywhere.
```

The read-only verbs are exempt on purpose — a login node is exactly
where "where does this project stand?" gets asked.

## Containers on HPC

A containerized project (one with `[tool.lightcone.image]` in its
`pyproject.toml`) works the same way, with three site realities to
know:

- **`podman-hpc` is detected first.** Sites install it precisely
  because plain podman's image store is invisible to compute nodes;
  where both exist, `lc` prefers the wrapper and runs its extra
  `migrate` step automatically, so the image is readable from every
  node.
- **Build on a login node, once.** `lc build` builds the image and
  commits it into the repository as versioned content — compute nodes
  never build and need no registry access; an unfetched image arrives
  through the annex like any other data. The archive records the
  architecture it was built for, and a mismatched host is refused
  before anything runs — so build where the architecture matches the
  compute nodes (on NERSC, a login node).
- **Multi-node runs require a shared image store.** With plain podman
  or docker the image exists only on the driver's node, so `lc`
  refuses a multi-node containerized run unless the runtime is
  `podman-hpc`. Single-node allocations work with any runtime.

## NERSC (Perlmutter) specifics

Facts of the site that shape how `lc` behaves there — all verified on
Perlmutter itself:

- **uv's cache and interpreters are put on scratch for you.** The
  home filesystem is unusable from compute nodes in two separate ways:
  it cannot take uv's cache lock at all (`flock` fails with os error
  524), and an interpreter stored there starts in 0.4-9 s instead of
  0.05 s, slow enough under load to exceed uv's own 60 s startup
  ceiling and fail a sync mid-run. So on NERSC machines `lc` runs uv
  with `UV_CACHE_DIR=$SCRATCH/.lightcone/uv-cache` and
  `UV_PYTHON_INSTALL_DIR=$SCRATCH/.lightcone/uv-python` — each only
  where you have not set that variable yourself:

  ```bash
  export UV_CACHE_DIR=$SCRATCH/my-uv-cache   # optional; this wins
  ```

  Both are disposable by design, so scratch purge policies cost a
  re-download rather than a result.

- **The sandbox does not enforce here.** Perlmutter's kernels boot
  without Landlock in the LSM list, so direct-mode recipes run
  unconfined and every run says so:

  ```text
  not sandboxed on this host — landlock unavailable (not in the kernel's boot-time LSM list, kernel < 5.13, blocked by seccomp, or unsupported arch); recorded as `fs: open`
  ```

  Manifests record `fs: open` honestly. A containerized project is
  the real boundary on this site: recipes see only the declared mount
  table, attested as `fs: declared`.

- **`MOUNT_*` is ignored; `ENABLE_*` passes through.** podman-hpc's
  site modules are switched by environment variables. The mount gates
  (`MOUNT_HOME`, `MOUNT_SCRATCH`, `MOUNT_CFS`) would bind-mount those
  filesystems into every recipe container — undeclared inputs under a
  manifest attesting `fs: declared` — so `lc` drops them and says so:

  ```text
  ignored ambient MOUNT_HOME — a site container module would bind-mount undeclared host directories into recipe containers
  ```

  The library gates (`ENABLE_GPU`, `ENABLE_MPICH_SS`, …) survive: a
  GPU recipe gets CUDA by exporting `ENABLE_GPU=1`, the site's own
  mechanism.

- **BLAS may be silently throttled.** NERSC's allocation environment
  exports `OMP_NUM_THREADS` (often `2`), and `srun` propagates it into
  every step — so numpy or BLAS inside a recipe uses 2 of the node's
  256 hardware threads unless you say otherwise. `lc` does not touch
  the variable; set it yourself before materializing:

  ```bash
  export OMP_NUM_THREADS=32   # or whatever your recipe's math wants
  ```

## Data on parallel filesystems

Keep active projects on the filesystem your center recommends for job
I/O (`$SCRATCH` on NERSC), and remember scratch purge policies — the
project is a git repository, so `git push` to a remote (and
`git annex copy --to` for the bytes) is the durable copy.

!!! warning "Early days"
    HPC support is the youngest part of lightcone-cli and has not yet
    been broadly validated on production systems. If something refuses,
    hangs, or surprises you on your center, please
    [open an issue](https://github.com/LightconeResearch/lightcone-cli/issues)
    — site reports are exactly what this layer needs right now.

## Where to next

- [Core Concepts](concepts.md) — the model all of this rests on.
- [Troubleshooting](troubleshooting.md) — the refusals, quoted, with
  remedies.
