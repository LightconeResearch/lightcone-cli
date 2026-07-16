# Running on a Cluster

When local laptop time isn't enough, you can take the same project to
a SLURM HPC system or a lightcone JupyterHub deployment. The same
execution path supports local and interactive work, while queued SLURM
work can be submitted with `lc run --async`.

## The big picture

`lc run` always dispatches through a Dask cluster. Four branches:

1. On your laptop → a `LocalCluster` sized to the machine.
2. **On a JupyterHub deployment** (Dask Gateway detected) → a
   run-scoped Gateway cluster created with your project's container
   image and shut down when the run finishes.
3. **Inside a SLURM allocation** → an in-process scheduler bound to
   the driver's hostname, with one `dask worker` per allocated node
   launched via `srun`.
4. With `DASK_SCHEDULER_ADDRESS` set → connect to whatever scheduler
   you've pointed at.

You don't pick — `lc run` detects which case applies. The only thing
you do differently on a cluster is request the nodes (and on
JupyterHub, not even that).

## JupyterHub deployments (Kubernetes + Dask Gateway)

On a lightcone JupyterHub (GKE with Dask Gateway and Cloud Build),
everything is zero-configuration — the deployment injects the whole
contract into your session (`DASK_GATEWAY__*`, `LIGHTCONE_REGISTRY`,
`LIGHTCONE_BUILD_BUCKET`), and `lc` picks it up:

- The scaffolded project image doubles as the Dask worker pod image
  with no hub-specific content: `lc init` pins `lightcone-cli` in
  `requirements.txt`, which brings the whole execution stack
  (snakemake, dask, distributed, dask-gateway) on top of your own
  dependencies — the same image runs anywhere.
- `lc build` builds through the deployment's **GCP Cloud Build**
  service (there's no docker in your session) and pushes
  `<registry>/lc-<project>:<content-hash>` to the hub's Artifact
  Registry. Unchanged files never rebuild — freshness is one registry
  check.
- `lc run` makes sure the image is up to date, **creates a Dask
  Gateway cluster with that image**, runs the pipeline in worker pods
  (recipes execute directly in the image — no nested containers), and
  **culls the cluster when the run finishes**. Your NFS home is
  mounted in every worker pod at the same path, so outputs land in
  the project tree exactly as they do locally.

A cluster's image is fixed at creation, so create-per-run is also what
keeps the environment fresh: edit the Containerfile, `lc run`, and the
next cluster runs the rebuilt image.

For longer work, `lc run --async` is an outer submission layer. It asks
SLURM for an allocation and then executes ordinary `lc run` inside it, so
recipe behavior and provenance stay identical.

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
slurm:
  account: m1234
  time_padding: 1.5
```

`slurm.account` is required the first time you use `--async`; alternatively,
pass `--account m1234` for one submission. `time_padding` multiplies the
serial sum of recipe time estimates and defaults to `1.5`.

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

### 1. Choose interactive or queued execution

For short, agent-in-the-loop work, get an interactive allocation and run
synchronously:

```bash
salloc -A m1234 -N 1 -t 02:00:00 -C gpu -q interactive
lc run heavy_fit -u baseline
```

For long or unattended work on Perlmutter, submit it from either a login or
compute node:

```bash
lc run --async heavy_fit -u baseline
lc status
```

Lightcone chooses `shared` or `regular`, writes the script under
`.lightcone/jobs/`, stores logs in scratch, and records the returned job id.

### 2. `lc run` inside the allocation

Once `SLURM_JOB_ID` is set in your environment, `lc run` does the rest:

- Starts an in-process Dask scheduler bound to the SLURM node hostname.
- Launches one `dask worker` per node via `srun`.
- Each worker advertises the node's CPU, memory, and GPU resources.
- Snakemake submits each rule via the Dask executor; rules with
  per-recipe `resources:` constraints land on workers that can hold
  them.

### 3. Per-recipe resources

Add resource hints in your `astra.yaml` recipe blocks:

```yaml
outputs:
  - id: heavy_fit
    type: metric
    recipe:
      command: python scripts/fit.py --output {output}
      resources:
        cpus: 16
        memory: 64GB
        gpus: 1
        time_limit: 2h
```

Lightcone maps these portable ASTRA names to Snakemake/Dask resource
requests for synchronous execution and uses the same declaration to size
asynchronous jobs. Every recipe in an asynchronously submitted sub-DAG needs
`time_limit`; the other fields have conservative zero/one defaults.

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
hands-off work, use `lc run --async`. This is also valid while the agent is
still on the compute node; `sbatch` queues an independent allocation.

## What about login-node-only operations?

Build images, submit queued work, and look at status are all fine on a login
node without an allocation:

```bash
lc build                       # build images (uses podman-hpc on login node)
lc run --async heavy_fit       # submits; does not execute on the login node
lc status                      # manifests + on-demand Slurm polling
```

Plain `lc run` must happen inside an allocation. On Perlmutter login nodes it
fails with an allocation example and a hint to try `--async`.

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
