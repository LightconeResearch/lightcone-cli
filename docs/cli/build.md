# lc build

Build container images declared in `astra.yaml` (or pre-pull registry
images so `lc run` can use `--pull=never`).

## Synopsis

```text
lc build [OPTIONS]
```

## Options

| Option | Default | Effect |
|--------|---------|--------|
| `--force` | off | Rebuild / re-pull even if the tag already exists locally. |
| `--runtime {docker,podman,podman-hpc,kubernetes}` | resolved from `~/.lightcone/config.yaml` (or the detected site) | Override the runtime for this build. |
| `--no-commit` | off | On a hub: fail instead of auto-committing environment-file changes before the image build. |

## What it does

For every distinct `container:` value found in the project (root,
sub-analysis, or recipe-level):

- **Path to a Containerfile** → compute the content-addressed tag
  `lc-<project>-<sha256[:12]>`, build the image, and (for `podman-hpc`)
  migrate it into the per-node container cache.
- **Anything else** (e.g. `python:3.12-slim`, `ghcr.io/foo/bar:tag`) →
  pull it into the local image store. This is what lets `lc run` pass
  `--pull=never` to the runtime, sidestepping `unqualified-search-registries`
  resolution issues with content-addressed tags.

If the runtime is `none` (either by config or because `auto` couldn't
find one), `lc build` prints a friendly note and exits 0. There is
nothing to build.

## On a JupyterHub deployment (`kubernetes` runtime)

There is no docker in a hub pod, so `lc build` drives the deployment's
build backend instead. Two backends exist; the deployment picks one by
what it injects into user pods:

**Cloud Build** (GCP deployments; selected by `LIGHTCONE_BUILD_BUCKET`
being set, preferred where available). Fully git-free: the *staged
build context* — the same file set the content-addressed tag hashes —
is tarred and uploaded to the build bucket, built off-cluster by GCP
Cloud Build, and pushed to `$LIGHTCONE_REGISTRY/lc-<project>:<hash>`.
No GitHub remote, commit, or push is involved; private projects build
exactly like public ones, and an unchanged environment is a single
registry HEAD (no build). Auth is the pod's Workload Identity — no
credentials anywhere. Failures surface the build-log tail.

**BinderHub service** (selected by an ambient `JUPYTERHUB_API_TOKEN` /
`LIGHTCONE_BINDER_URL`; the portable, non-GCP path). Builds from a git
ref: `lc build` commits environment-file changes (`--no-commit`
refuses instead), pushes (the project needs a clonable GitHub remote),
maintains a `Dockerfile → Containerfile` symlink for repo2docker, and
streams the build via SSE. Images are tagged by the environment
commit.

`lc run` performs the same ensure step automatically on every run, so
running `lc build` explicitly is optional on the hub — useful to
pre-build after a dependency change or to see the build log. With no
backend configured, `lc build` falls back to probing the registry and
printing off-hub publish instructions.

## Tag computation

```text
lc-<sanitized-project-name>-<sha256[:12]>
```

The hash covers the Containerfile contents plus any of these dependency
files at the project root:

- `requirements.txt`
- `requirements-dev.txt`
- `requirements-test.txt`
- `pyproject.toml`
- `setup.py`
- `setup.cfg`
- `poetry.lock`
- `Pipfile.lock`

Edit any one of those and the tag changes. That, in turn, changes
`code_version` in every recipe that uses the image, which marks all
downstream outputs `stale` in `lc status`.

## Examples

```bash
lc build                       # build / pull whatever's missing
lc build --force               # rebuild / re-pull everything
lc build --runtime podman-hpc  # force the HPC runtime
```

## Pre-staging for HPC

On a login node:

```bash
$EDITOR ~/.lightcone/config.yaml      # container.runtime: podman-hpc
lc build                              # builds + migrates everything
```

Then submit a SLURM job for `lc run`. The compute nodes will find every
image already cached.

See [api/container](../api/container.md) for the implementation and
[Architecture](../architecture.md) for why we wrap recipes ourselves
instead of using Snakemake's `container:` directive.
