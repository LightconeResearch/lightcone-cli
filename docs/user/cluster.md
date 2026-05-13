# Running on a Cluster

When local laptop time isn't enough, you can take the same project to
a SLURM HPC system. There's no separate configuration to learn — the
same `lc run` command works inside an allocation, just with more
hardware to spread across.

## The big picture

`lc run` always dispatches through a Dask cluster. Three branches:

1. On your laptop → a `LocalCluster` sized to the machine.
2. **Inside a SLURM allocation** → an in-process scheduler bound to
   the driver's hostname, with one `dask worker` per allocated node
   launched via `srun`.
3. With `DASK_SCHEDULER_ADDRESS` set → connect to whatever scheduler
   you've pointed at.

You don't pick — `lc run` detects which case applies. The only thing
you do differently on a cluster is request the nodes.

## Pre-flight: pick the right container runtime

On most HPC sites, docker isn't available on compute nodes. Most
SLURM systems (including NERSC Perlmutter) provide `podman-hpc`. On a
login node:

```bash
$EDITOR ~/.lightcone/config.yaml
```

```yaml
container:
  runtime: podman-hpc
```

Then build and migrate the images for your project:

```bash
cd my-analysis
lc build
```

`lc build` runs `podman-hpc build` and then `podman-hpc migrate`,
which copies the image into the per-node container cache. Compute
nodes can read it without registry access.

If your site has only `apptainer` / `singularity`, the Lightcone
toolchain doesn't ship explicit support for those today — you can run
without containers (`runtime: none`) for the moment, with the caveat
that the manifest's `container_image` field will record what was
declared, not what executed. (See [`lc run`](../cli/run.md) for the
provenance warning.)

## A typical SLURM workflow

### 1. Get an allocation

```bash
salloc -N 4 -t 02:00:00 -C gpu                       # interactive
# or
sbatch run.sbatch                                    # batch
```

`run.sbatch` looks like:

=== "Generic"
    ```bash
    #!/bin/bash
    #SBATCH -N 4
    #SBATCH -t 02:00:00
    #SBATCH -C gpu

    cd $HOME/my-analysis
    source .venv/bin/activate
    lc run -j 16
    ```

=== "NERSC Perlmutter"
    ```bash
    #!/bin/bash
    #SBATCH -A <your_project>
    #SBATCH -q regular
    #SBATCH -C gpu
    #SBATCH -N 4
    #SBATCH -t 04:00:00

    cd $SCRATCH/your-analysis

    # make `lc` available — pick the line that matches your install:
    export PATH=$HOME/.local/bin:$PATH                # uv tool install
    # source ~/.conda/envs/your-env-name/bin/activate # conda env

    lc run -j 16
    ```

### 2. `lc run` inside the allocation

Once `SLURM_JOB_ID` is set in your environment, `lc run` does the rest:

- Starts an in-process Dask scheduler bound to the SLURM node hostname.
- Launches one `dask worker` per node via `srun`.
- Each worker advertises the node's CPU, memory, and GPU resources.
- Snakemake submits each rule via the Dask executor; rules with
  per-recipe `resources:` constraints land on workers that can hold
  them.

### 3. Per-recipe resource hints

Add resource hints in your `astra.yaml` recipe blocks:

```yaml
outputs:
  - id: heavy_fit
    type: metric
    recipe:
      command: python scripts/fit.py --output {output[0]}
      resources:
        cpus_per_task: 32
        mem_mb: 64000
        gpus_per_task: 1
```

The Snakemake-via-Dask executor maps these to per-task resource
requests, so a rule that needs a GPU only schedules on nodes that
advertise one.

## Interactive: agent-driven runs

During development you're usually iterating — ask the agent to build
something, check the result, adjust the spec, repeat. For that loop
you want to run the agent itself from inside a SLURM allocation so
that `lc run` executes on the compute node rather than the login node.

```bash
salloc -A <your_project> -q interactive -C gpu --nodes=1 -t 00:30:00
# salloc drops you onto a compute node; from there:
cd /path/to/your-analysis
claude                   # or whichever agent CLI you prefer
```

Everything the agent triggers (`lc run`, scripts, etc.) now executes
on the allocated node. When you're done iterating and want a
hands-off sweep of all universes, submit `lc run` as a batch job
instead (the sbatch template above).

## What about login-node-only operations?

Build images, dry-run, look at status — all fine on a login node
without an allocation:

```bash
lc build                       # build images (uses podman-hpc on login node)
lc status                      # offline; reads only manifests
```

The actual `lc run` should happen inside an allocation, since that's
where the worker nodes are.

## External Dask schedulers

If you have a long-lived Dask cluster (Slurm jobqueue, k8s, etc.)
that you'd rather attach to:

```bash
export DASK_SCHEDULER_ADDRESS=tcp://my-scheduler:8786
lc run
```

`lc run` notices the env var and connects rather than starting its
own scheduler. It does *not* tear the scheduler down on exit.

## NERSC Perlmutter: site-specific notes

!!! note "Setting up on Perlmutter for the first time?"
    The [Install](install.md) page has NERSC-specific tabs for Python
    (uv vs `module load python`, conda env storage), lightcone-cli, and
    the agent CLI. Come back here once `lc --version` works.

### Storage: keep Snakemake state on `$SCRATCH`

!!! danger "DVS silently ignores `flock()`"
    `$HOME` and `/global/cfs/` are mounted on compute nodes via DVS,
    which silently ignores `flock()`. Snakemake relies on `flock` for
    locking, so its `.snakemake/` directory and Dask spill files
    **must** live on Lustre (`$SCRATCH`), which honors `flock`.
    Otherwise you get intermittent silent rule-rerun loops or hangs.

`lc` redirects state automatically when it detects Perlmutter, so
this usually just works. To pin explicitly at project creation:

```bash
lc init your-analysis --scratch '$SCRATCH'   # kept verbatim, expanded at run time
```

Or, after the fact, edit `<project>/.lightcone/lightcone.yaml`:

```yaml
scratch_root: $SCRATCH
```

!!! warning "12-week purge on `$SCRATCH`"
    Perlmutter purges `$SCRATCH` on a rolling 12-week window. For
    outputs you need to keep, copy or symlink to
    `/global/cfs/cdirs/<project>/`.

### Further reading

- [NERSC interactive jobs](https://docs.nersc.gov/jobs/interactive/)
  — `salloc` patterns and reservation queues
- [Perlmutter system overview](https://docs.nersc.gov/systems/perlmutter/)
  — node types and partitions
- [NERSC queue policy](https://docs.nersc.gov/jobs/policy/)
  — QoS options for GPU and CPU partitions

## Troubleshooting

- `dask CLI is not on PATH inside the SLURM allocation`. Install
  `lightcone-cli` into the venv that your sbatch script activates;
  `dask` ships with `distributed`, which is a transitive dep.
- Workers never register. Usually means the SLURM node hostnames
  aren't resolvable from each other; check `SLURMD_NODENAME` /
  `gethostname()` and confirm the workers can reach the scheduler.
- Image not found on compute nodes. Re-run `lc build` on the login
  node — the migrate step is the one that actually publishes the
  image to the per-node cache.
- Snakemake locking errors or silent rule-rerun loops on Perlmutter.
  `.snakemake/` ended up on DVS-mounted storage — set
  `scratch_root: $SCRATCH` in the project's `.lightcone/lightcone.yaml`.
- `pip install` hangs or times out. Compute nodes have no public
  internet — always install from a login node.
- `PermissionError` reading another user's symlinked `results/`.
  Cross-user scratch path without group ACLs — request access from
  the data owner, or copy the manifests into your own scratch.

For the wiring detail, see
[engine/dask_cluster](../api/dask_cluster.md) in the maintainer docs.
