"""Container image building from Containerfiles.

Resolves container specs in astra.yaml — either pre-built image strings
or build specs that point to a Containerfile. Build specs produce
content-addressed image tags for automatic caching.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Files whose contents contribute to the image tag hash.
DEPENDENCY_FILES = (
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-test.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "poetry.lock",
    "Pipfile.lock",
)


def detect_container_runtime() -> str | None:
    """Detect which container runtime is available locally.

    Checks for Docker first, then Podman.  Returns the binary name
    (``"docker"`` or ``"podman"``) or ``None`` if neither is found.

    This does **not** check for ``podman-hpc``, which is only relevant
    for SLURM targets and handled separately.
    """
    for runtime in ("docker", "podman"):
        if shutil.which(runtime) is not None:
            return runtime
    return None


class ContainerBuildError(Exception):
    """Raised when a container image build fails."""


@dataclass
class ContainerBuildResult:
    """Result of building a container image."""

    tag: str
    already_existed: bool
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""


def find_dependency_files(project_path: Path) -> list[Path]:
    """Return sorted list of dependency files found in *project_path*."""
    found: list[Path] = []
    for name in DEPENDENCY_FILES:
        p = project_path / name
        if p.is_file():
            found.append(p)
    return sorted(found)


def hash_file_contents(files: list[Path]) -> str:
    """Return a SHA-256 hex digest of the concatenated contents of *files*."""
    h = hashlib.sha256()
    for f in files:
        h.update(f.read_bytes())
    return h.hexdigest()


def compute_image_tag(
    project_name: str,
    containerfile: Path,
    project_path: Path,
) -> str:
    """Compute a content-addressed image tag.

    The tag is ``prism-<project_name>-<12-char-sha256>``.  The hash covers
    the Containerfile contents plus any dependency files found in the
    project root.
    """
    digest = hash_file_contents([containerfile, *find_dependency_files(project_path)])[:12]
    # Sanitise project name for use as a Docker tag component.
    safe_name = project_name.lower().replace(" ", "-")
    return f"prism-{safe_name}-{digest}"


def image_exists_locally(tag: str, runtime: str = "docker") -> bool:
    """Check whether *tag* exists in the local container image store."""
    try:
        result = subprocess.run(
            [runtime, "image", "inspect", tag],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def build_image(
    tag: str,
    containerfile: Path,
    context: Path,
    build_args: dict[str, str] | None = None,
    runtime: str = "docker",
) -> ContainerBuildResult:
    """Build a container image with the specified runtime (Docker or Podman).

    Raises :class:`ContainerBuildError` on failure.
    """
    cmd: list[str] = [
        runtime, "build",
        "-t", tag,
        "-f", str(containerfile),
    ]
    for key, value in (build_args or {}).items():
        cmd += ["--build-arg", f"{key}={value}"]
    cmd.append(str(context))

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise ContainerBuildError(
            f"{runtime} is not installed or not on PATH. "
            f"Install {runtime} to build container images."
        )

    if proc.returncode != 0:
        raise ContainerBuildError(
            f"{runtime} build failed (exit code {proc.returncode}):\n{proc.stderr}"
        )

    return ContainerBuildResult(
        tag=tag,
        already_existed=False,
        exit_code=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def resolve_container_spec(
    spec: str | dict[str, Any] | None,
    project_path: Path,
    project_name: str,
    *,
    force: bool = False,
    dry_run: bool = False,
    runtime: str = "docker",
) -> str | None:
    """Resolve a container spec to an image tag string.

    * ``None`` -> ``None``
    * ``str`` -> returned as-is (pre-built image)
    * ``dict`` with ``build`` key -> build (or skip if cached) and return tag

    If *dry_run* is ``True``, returns the tag that *would* be used without
    actually building.
    """
    if spec is None:
        return None
    if isinstance(spec, str):
        return spec

    # Must be a build spec dict.
    build_path = spec.get("build")
    if not build_path:
        raise ContainerBuildError(
            "Container build spec must have a 'build' key pointing to a Containerfile."
        )

    containerfile = project_path / build_path
    if not containerfile.is_file():
        raise ContainerBuildError(
            f"Containerfile not found: {containerfile}"
        )

    tag = compute_image_tag(project_name, containerfile, project_path)

    if dry_run:
        return tag

    if not force and image_exists_locally(tag, runtime=runtime):
        logger.info("Image %s already exists, skipping build.", tag)
        return tag

    context_dir = project_path
    if spec.get("context"):
        context_dir = project_path / spec["context"]

    logger.info("Building image %s from %s ...", tag, containerfile)
    build_image(tag, containerfile, context_dir, build_args=spec.get("args"), runtime=runtime)
    return tag


@dataclass
class ContainerStatus:
    """Status information for a container spec."""

    type: str  # "none", "prebuilt", "build"
    image: str | None = None
    exists: bool | None = None
    containerfile: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# HPC container runtimes (podman-hpc)
# ---------------------------------------------------------------------------


def build_image_podman_hpc(
    tag: str,
    containerfile: Path,
    context: Path,
    build_args: dict[str, str] | None = None,
) -> ContainerBuildResult:
    """Build a container image with ``podman-hpc build``.

    Runs on NERSC login nodes.  After building, the image is automatically
    migrated so it is available on compute nodes.

    Raises :class:`ContainerBuildError` on failure.
    """
    cmd: list[str] = [
        "podman-hpc", "build",
        "-t", tag,
        "-f", str(containerfile),
    ]
    for key, value in (build_args or {}).items():
        cmd += ["--build-arg", f"{key}={value}"]
    cmd.append(str(context))

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise ContainerBuildError(
            "podman-hpc is not installed or not on PATH. "
            "Are you running on a NERSC login node?"
        )

    if proc.returncode != 0:
        raise ContainerBuildError(
            f"podman-hpc build failed (exit code {proc.returncode}):\n{proc.stderr}"
        )

    # Migrate the image so compute nodes can access it.
    _podman_hpc_migrate(tag)

    return ContainerBuildResult(
        tag=tag,
        already_existed=False,
        exit_code=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def _podman_hpc_migrate(tag: str) -> None:
    """Run ``podman-hpc migrate <tag>`` to make image available on compute nodes."""
    try:
        proc = subprocess.run(
            ["podman-hpc", "migrate", tag],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        raise ContainerBuildError("podman-hpc not found — cannot migrate image.")
    if proc.returncode != 0:
        raise ContainerBuildError(
            f"podman-hpc migrate failed (exit code {proc.returncode}):\n{proc.stderr}"
        )
    logger.info("podman-hpc migrate %s succeeded.", tag)


def image_exists_podman_hpc(tag: str) -> bool:
    """Check whether *tag* exists in the local podman-hpc image store."""
    try:
        result = subprocess.run(
            ["podman-hpc", "image", "exists", tag],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def resolve_container_for_slurm(
    spec: str | dict[str, Any] | None,
    project_path: Path,
    project_name: str,
    container_runtime: str,
    *,
    force: bool = False,
) -> str | None:
    """Resolve a container spec for SLURM execution, building if needed.

    Build specs (dict with ``build`` key) are built with ``podman-hpc build``
    and migrated automatically.  Pre-built image strings are migrated if not
    already available.

    Returns the image tag string to use, or ``None`` if no container.
    """
    if spec is None:
        return None

    if isinstance(spec, str):
        # Pre-built image reference
        if not force and image_exists_podman_hpc(spec):
            logger.info("Image %s already available in podman-hpc, skipping migrate.", spec)
        else:
            logger.info("Migrating %s for podman-hpc compute nodes...", spec)
            _podman_hpc_migrate(spec)
        return spec

    # Must be a build spec dict.
    build_path = spec.get("build")
    if not build_path:
        raise ContainerBuildError(
            "Container build spec must have a 'build' key pointing to a Containerfile."
        )

    containerfile = project_path / build_path
    if not containerfile.is_file():
        raise ContainerBuildError(f"Containerfile not found: {containerfile}")

    tag = compute_image_tag(project_name, containerfile, project_path)

    if not force and image_exists_podman_hpc(tag):
        logger.info("Image %s already exists in podman-hpc, skipping build.", tag)
        return tag

    context_dir = project_path
    if spec.get("context"):
        context_dir = project_path / spec["context"]

    logger.info("Building image %s with podman-hpc from %s ...", tag, containerfile)
    build_image_podman_hpc(tag, containerfile, context_dir, build_args=spec.get("args"))
    return tag


def get_container_status(
    spec: str | dict[str, Any] | None,
    project_path: Path,
    project_name: str,
    runtime: str = "docker",
) -> ContainerStatus:
    """Return status information for a container spec without building."""
    if spec is None:
        return ContainerStatus(type="none")

    if isinstance(spec, str):
        return ContainerStatus(type="prebuilt", image=spec)

    build_path = spec.get("build")
    if not build_path:
        return ContainerStatus(type="build", extra={"error": "missing 'build' key"})

    containerfile = project_path / build_path
    if not containerfile.is_file():
        return ContainerStatus(
            type="build",
            containerfile=build_path,
            extra={"error": f"Containerfile not found: {containerfile}"},
        )

    tag = compute_image_tag(project_name, containerfile, project_path)
    exists = image_exists_locally(tag, runtime=runtime)
    return ContainerStatus(
        type="build",
        image=tag,
        exists=exists,
        containerfile=build_path,
    )
