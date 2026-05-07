# lc build

Build container images declared in `astra.yaml` (or pre-pull registry
images so `lc run` can use `--pull=never`).

## Synopsis

```
lc build [OPTIONS]
```

## Options

| Option | Default | Effect |
|--------|---------|--------|
| `--force` | off | Rebuild / re-pull even if the tag already exists locally. |
| `--runtime {docker,podman,podman-hpc}` | resolved from `~/.lightcone/config.yaml` | Override the runtime for this build. |

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

## Tag computation

```
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
