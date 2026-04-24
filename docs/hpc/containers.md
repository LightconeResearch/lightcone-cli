# Container Builds for HPC

HPC sites typically do not support Docker. lightcone-cli handles two container runtimes for SLURM targets:

- `podman-hpc` — for NERSC Perlmutter and sites running the `podman-hpc` distribution
- `singularity` / `apptainer` — for other HPC sites (community-maintained)

## podman-hpc workflow

`podman-hpc` is a rootless container runtime for HPC used at NERSC. lightcone-cli's `resolve_container_for_slurm()` implements a two-step workflow:

### Step 1: Build (if Containerfile)

```bash
podman build -t lc-{name}-{hash} -f Containerfile .
```

### Step 2: Migrate

```bash
podman-hpc migrate lc-{name}-{hash}
```

Migration copies the image to the site-local container cache at a path the batch nodes can access without a registry.

If the spec is a pre-built image (not a Containerfile), only the migrate step runs.

### Parsl task integration

The migrated image name is passed to `podman-hpc run` when the Parsl worker executes a recipe task on the compute node:

```bash
podman-hpc run --rm \
  -v /path/to/project:/workspace \
  -w /workspace \
  lc-myproject-a1b2c3d \
  sh -c "python scripts/compute.py --universe baseline ..."
```

The `--gpu` container flag is injected automatically for GPU node types. The command is assembled by `_podman_hpc_run_command_inline()` in the runner and handed to Parsl for dispatch — there is no `sbatch` script generated per recipe. See [Parsl pilot model](parsl-pilot.md) for how tasks are routed to the right pilot.

## Content-addressed tags

Tags are deterministic so builds are skipped when nothing has changed:

```
lc-{sanitised-project-name}-{sha256[:12]}
```

The hash covers the Containerfile and all dependency files (`requirements.txt`, `pyproject.toml`, etc.).

## Pre-building before a session

To avoid network/build time during an HPC session:

```bash
# On a login node or locally
lc build --runtime podman-hpc
```

This stages all required images before submitting jobs.
