# prism build

Build container images from `Containerfile` specs in `astra.yaml`.

## Synopsis

```
prism build [OPTIONS]
```

## Description

Scans the analysis specification for container build specs (both analysis-level and per-recipe) and builds any missing images. Images are content-addressed — a rebuild only happens when the `Containerfile` or dependency files actually change.

The container runtime is auto-detected from the project's target config. Use `--runtime` to override.

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--force` | false | Rebuild images even if they already exist |
| `--runtime`, `-r` | auto-detect | Container runtime (`docker`, `podman`, `podman-hpc`) |

## Image tagging

Tags are deterministic and content-addressed:

```
prism-{project_name}-{sha256[:12]}
```

The hash covers:
- The `Containerfile` contents
- All dependency files found in the project root: `requirements.txt`, `pyproject.toml`, `poetry.lock`, `Pipfile.lock`, etc.

## Pre-built image behaviour

If the `container:` field in `astra.yaml` is a pre-built image name (not a file path), `prism build` with a non-Docker runtime (e.g. `podman-hpc`) will call `resolve_container_for_slurm()` to migrate it to the site container cache.

## Examples

```bash
prism build                      # auto-detect runtime from target
prism build --runtime podman-hpc # force podman-hpc
prism build --runtime docker     # force docker
prism build --force              # rebuild all images
```

## Integration with prism run

`prism run` calls the same build logic internally unless `--no-build` is passed. Use `prism build` separately to pre-stage images before an HPC session where network access may be limited.
