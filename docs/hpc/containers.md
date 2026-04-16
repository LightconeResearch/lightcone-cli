# Container Builds for HPC

HPC sites typically do not support Docker. Prism handles two container runtimes for SLURM targets:

- `podman-hpc` — for NERSC Perlmutter and sites running the `podman-hpc` distribution
- `singularity` / `apptainer` — for other HPC sites (community-maintained)

## podman-hpc workflow

`podman-hpc` is a rootless container runtime for HPC used at NERSC. Prism's `resolve_container_for_slurm()` implements a two-step workflow:

### Step 1: Build (if Containerfile)

```bash
podman build -t prism-{name}-{hash} -f Containerfile .
```

### Step 2: Migrate

```bash
podman-hpc migrate prism-{name}-{hash}
```

Migration copies the image to the site-local container cache at a path the batch nodes can access without a registry.

If the spec is a pre-built image (not a Containerfile), only the migrate step runs.

### sbatch integration

The migrated image name is passed directly to `podman-hpc run` in the sbatch script:

```bash
podman-hpc run --rm \
  -v /path/to/project:/workspace \
  -w /workspace \
  prism-myproject-a1b2c3d \
  sh -c "python scripts/compute.py --universe baseline ..."
```

The `--gpu` container flag is injected automatically for GPU node types.

## Content-addressed tags

Tags are deterministic so builds are skipped when nothing has changed:

```
prism-{sanitised-project-name}-{sha256[:12]}
```

The hash covers the Containerfile and all dependency files (`requirements.txt`, `pyproject.toml`, etc.).

## Pre-building before a session

To avoid network/build time during an HPC session:

```bash
# On a login node or locally
prism build --runtime podman-hpc
```

This stages all required images before submitting jobs.
