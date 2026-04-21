# lightcone.engine.container

Content-addressed container image building from Containerfiles.

## Overview

A container spec in `astra.yaml` is a single string. lightcone-cli distinguishes two cases by checking whether the string resolves to an existing file:

- **Pre-built image** (e.g. `python:3.9`) — pulled on demand.
- **Containerfile build** (e.g. `Containerfile`, `containers/gpu.Dockerfile`) — built locally and tagged deterministically.

---

## `detect_container_runtime() → str | None`

Checks for `docker` then `podman` on `PATH`. Returns the binary name or `None`.

Does **not** check for `podman-hpc` (handled separately in SLURM targets).

---

## `is_containerfile(spec, project_path) → bool`

Returns `True` if `spec` resolves to an existing file in `project_path`.

---

## `compute_image_tag(project_name, containerfile_path, project_path) → str`

Returns a deterministic tag `lc-{name}-{sha256[:12]}` where the hash covers the Containerfile and all dependency files.

---

## `resolve_container_spec(spec, project_path, project_name, ...) → str`

Resolves a container spec to a usable image tag:

- If `spec` is a pre-built image name → returns it as-is (Docker will pull).
- If `spec` is a Containerfile path → builds the image and returns the tag.
- If the image already exists (content hash matches) → skips the build.

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `spec` | `str` | — | Container spec from `astra.yaml` |
| `project_path` | `Path` | — | Project root |
| `project_name` | `str` | — | Used as image name prefix |
| `force` | `bool` | `False` | Rebuild even if image exists |
| `runtime` | `str` | `"docker"` | Container runtime binary name |

**Raises:** `ContainerBuildError` if the build fails.

---

## `resolve_container_for_slurm(spec, project_path, project_name, runtime, ...) → str`

Like `resolve_container_spec()` but for HPC targets. Builds (if Containerfile) then runs `{runtime} migrate` to stage the image in the site container cache.

---

## `get_container_status(spec, project_path, project_name, runtime) → ContainerStatus`

Returns a `ContainerStatus` describing whether an image exists, its tag, and its type.

---

## `find_dependency_files(project_path) → list[Path]`

Returns sorted list of dependency files found in `project_path`. Checks for:

```python
DEPENDENCY_FILES = (
    "requirements.txt", "requirements-dev.txt", "requirements-test.txt",
    "pyproject.toml", "setup.py", "setup.cfg", "poetry.lock", "Pipfile.lock",
)
```

---

## `hash_file_contents(files) → str`

Returns a SHA-256 hex digest of the concatenated contents of the given files.

---

## Data classes

### `ContainerBuildResult`

```python
@dataclass
class ContainerBuildResult:
    tag: str
    already_existed: bool
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
```

### `ContainerStatus`

Describes the current state of a container spec:

| Field | Description |
|-------|-------------|
| `type` | `"prebuilt"` or `"build"` |
| `image` | Resolved image tag |
| `containerfile` | Path to Containerfile (if `type == "build"`) |
| `exists` | Whether the image is present locally |

---

## Exceptions

### `ContainerBuildError`

Raised when a container image build fails. Message includes the build command and stderr output.

---

## Image tag format

```
lc-{sanitised-project-name}-{sha256[:12]}
```

Sanitisation: lowercase, non-alphanumeric characters replaced with `-`.
