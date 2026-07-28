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
| `--runtime {docker,podman,podman-hpc,kubernetes}` | resolved from `~/.lightcone/config.yaml` | Override the runtime for this build. |

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

On the `kubernetes` runtime (a lightcone JupyterHub deployment, where
no local OCI runtime exists) the same command builds through the
deployment's **GCP Cloud Build** service instead: the staged build
context is uploaded to the deployment's build bucket and the resulting
image is pushed as `$LIGHTCONE_REGISTRY/lc-<project>:<sha256[:12]>` —
the same content-addressed identity, so an unchanged environment is a
single registry check and no build at all. Pre-built registry images
are left alone (worker pods pull them directly). Auth is the pod's
Workload Identity; nothing to configure.

If the runtime is `none` (either by config or because `auto` couldn't
find one), `lc build` prints a friendly note and exits 0. There is
nothing to build.

## Tag computation

```text
lc-<sanitized-project-name>-<sha256[:12]>
```

(On the `kubernetes` runtime the same identity is spelled
`<registry>/lc-<sanitized-project-name>:<sha256[:12]>` — the digest is
identical, it just moves into the tag position.)

The hash covers three things:

1. The **Containerfile** contents.
2. Any of these **dependency files** at the project root:
   `requirements.txt`, `requirements-dev.txt`, `requirements-test.txt`,
   `pyproject.toml`, `setup.py`, `setup.cfg`, `poetry.lock`,
   `Pipfile.lock`, `uv.lock`, `conda-lock.yml`, `environment.yml`,
   `environment.yaml`.
3. Every **`COPY` / `ADD` source** referenced from the Containerfile:
   files are hashed directly, directories are walked recursively.
   `COPY .` therefore covers the whole project tree, minus a fixed
   exclude list (`.git`, `.venv`, `venv`, `__pycache__`, `results`,
   `.lightcone`, `.snakemake`, `node_modules`, and the usual tool
   caches) so the tag doesn't churn every time you run the pipeline.

Sources behind `--from=<stage>`, and URL / git `ADD` arguments, are
skipped — they aren't part of the host build context.

Edit any of the above and the tag changes. That, in turn, changes
`code_version` in every recipe that uses the image, which marks all
downstream outputs `stale` in `lc status`.

!!! note "New in v0.4.0"
    Point 3 is new. Projects whose Containerfile copies source code
    (`COPY src/ /app/src`, `COPY . /app`) will see one extra rebuild
    the first time they run under v0.4.0, and will now correctly
    rebuild when that copied code changes — which previously went
    unnoticed.

## Examples

```bash
lc build                       # build / pull whatever's missing
lc build --force               # rebuild / re-pull everything
lc build --runtime podman-hpc  # force the HPC runtime
lc build --runtime kubernetes  # force the Cloud Build path (JupyterHub)
```

`--runtime kubernetes` is normally unnecessary: on a lightcone
JupyterHub the site is detected from `DASK_GATEWAY__ADDRESS` and the
runtime resolves to `kubernetes` on its own. Pass it explicitly to pin
the behaviour, or to build against the deployment registry from a
session where detection would have picked something else. It requires
the Cloud Build contract in the environment — `LIGHTCONE_REGISTRY` and
`LIGHTCONE_BUILD_BUCKET` (plus the optional
`LIGHTCONE_BUILD_SERVICE_ACCOUNT`) — and a metadata-served GCP identity;
without those `lc build` errors with *"No image build backend on this
host"*.

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
