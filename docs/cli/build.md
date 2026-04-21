# lc build

Build container images from `Containerfile` specs in `astra.yaml`.

## Synopsis

```
lc build [OPTIONS]
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
lc-{project_name}-{sha256[:12]}
```

The hash covers:
- The `Containerfile` contents
- All dependency files found in the project root: `requirements.txt`, `pyproject.toml`, `poetry.lock`, `Pipfile.lock`, etc.

## Pre-built image behaviour

If the `container:` field in `astra.yaml` is a pre-built image name (not a file path), `lc build` with a non-Docker runtime (e.g. `podman-hpc`) will call `resolve_container_for_slurm()` to migrate it to the site container cache.

## Examples

```bash
lc build                      # auto-detect runtime from target
lc build --runtime podman-hpc # force podman-hpc
lc build --runtime docker     # force docker
lc build --force              # rebuild all images
```

## Integration with lc run

`lc run` calls the same build logic internally unless `--no-build` is passed. Use `lc build` separately to pre-stage images before an HPC session where network access may be limited.
