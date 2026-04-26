"""Container runtime layer.

We commit to **Dockerfile syntax** for ``Containerfile`` and **own** the
container invocation end-to-end — Snakemake's built-in ``container:``
directive and ``--sdm apptainer`` pipeline are deliberately not used. A
single config knob picks the OCI runtime; building and running both go
through it.

Two surfaces:

* :func:`compute_image_tag` and :func:`build_image` cover the **build**
  phase — ``lc build`` invokes them to produce ``lc-<project>-<hash>``
  in the runtime's local image store.

* :func:`wrap_recipe` covers the **run** phase — the Snakefile generator
  calls it to convert a raw recipe into a shell command that executes
  inside the configured container runtime.

Supported runtimes:
    * ``docker`` / ``podman`` — local desktop or build host
    * ``podman-hpc`` — NERSC-style login nodes; ``build`` migrates the
      image so compute-node apptainer can read it. ``run`` still uses
      ``podman-hpc`` directly.
    * ``none`` — no container; recipe runs on the host. Useful for
      development and for projects that don't need isolation.
"""
from __future__ import annotations

import hashlib
import logging
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

#: Runtimes we know how to build and run with. Order is detection priority:
#: docker first because it's the laptop default; podman as the rootless
#: equivalent; podman-hpc only relevant on login nodes.
RUNTIMES: tuple[str, ...] = ("docker", "podman", "podman-hpc")

#: Files whose contents contribute to the image tag hash.
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


@dataclass
class ContainerStatus:
    """Status information for a container spec."""

    type: str  # "none", "prebuilt", "build"
    image: str | None = None
    exists: bool | None = None
    containerfile: str | None = None


@dataclass(frozen=True)
class RuntimeChoice:
    """Result of resolving the container runtime to use.

    ``runtime`` is the resolved value (``docker | podman | podman-hpc | none``).
    ``explicit`` is ``True`` when the user pinned this value in
    ``~/.lightcone/config.yaml`` — i.e. they typed ``runtime: docker``,
    ``runtime: podman``, … or ``runtime: none``. ``False`` means
    ``runtime: auto`` (or no config), and the runtime is whatever
    detection produced — including ``none`` as a silent fallback.

    Callers use ``explicit`` to decide whether silently running without
    isolation is acceptable. When the user explicitly opted out, no
    surprise. When auto fell back to ``none`` against the spec's
    declared containers, the manifest's ``container_image`` field would
    misrepresent what actually executed — that is a provenance hazard
    and the caller should warn or refuse to proceed.
    """

    runtime: str
    explicit: bool


# ---------------------------------------------------------------------------
# Runtime detection / config
# ---------------------------------------------------------------------------


def detect_runtime() -> str | None:
    """Return the first available runtime in :data:`RUNTIMES`, or ``None``."""
    for runtime in RUNTIMES:
        if shutil.which(runtime) is not None:
            return runtime
    return None


def _global_config_path() -> Path:
    return Path.home() / ".lightcone" / "config.yaml"


def load_runtime(*, project_path: Path | None = None) -> RuntimeChoice:
    """Resolve the container runtime to use.

    Reads ``container.runtime`` from ``~/.lightcone/config.yaml`` (the
    project_path is accepted for future per-project overrides but is not
    consulted today). Values:

    * ``auto`` (default) — first available runtime in :data:`RUNTIMES`,
      else falls back to ``"none"`` with ``explicit=False``.
    * ``docker | podman | podman-hpc`` — explicit; binary must exist.
    * ``none`` — explicit opt-out; recipes run on the host.

    Raises :class:`ContainerBuildError` if an explicit runtime is
    configured but its binary is missing on PATH, or if the configured
    value is unrecognised.
    """
    cfg_path = _global_config_path()
    requested = "auto"
    if cfg_path.is_file():
        try:
            data = yaml.safe_load(cfg_path.read_text()) or {}
            requested = (data.get("container") or {}).get("runtime") or "auto"
        except yaml.YAMLError:
            logger.warning("Could not parse %s; using runtime: auto", cfg_path)
            requested = "auto"

    if requested == "auto":
        return RuntimeChoice(runtime=detect_runtime() or "none", explicit=False)
    if requested == "none":
        return RuntimeChoice(runtime="none", explicit=True)
    if requested not in RUNTIMES:
        raise ContainerBuildError(
            f"Unknown container.runtime {requested!r} in {cfg_path}. "
            f"Expected one of: auto, none, {', '.join(RUNTIMES)}."
        )
    if shutil.which(requested) is None:
        raise ContainerBuildError(
            f"Configured container.runtime {requested!r} is not on PATH. "
            f"Install {requested} or set container.runtime to a different value "
            f"in {cfg_path}."
        )
    return RuntimeChoice(runtime=requested, explicit=True)


# ---------------------------------------------------------------------------
# Image tag computation
# ---------------------------------------------------------------------------


def find_dependency_files(project_path: Path) -> list[Path]:
    """Return sorted list of dependency files found in *project_path*."""
    found = [project_path / name for name in DEPENDENCY_FILES]
    return sorted(p for p in found if p.is_file())


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

    The tag is ``lc-<project_name>-<12-char-sha256>``.  The hash covers
    the Containerfile contents plus any dependency files found in the
    project root.
    """
    digest = hash_file_contents([containerfile, *find_dependency_files(project_path)])[:12]
    safe_name = project_name.lower().replace(" ", "-")
    return f"lc-{safe_name}-{digest}"


def is_containerfile(spec: str, project_path: Path) -> bool:
    """Return ``True`` if *spec* refers to an existing file (Containerfile)."""
    return (project_path / spec).is_file()


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def image_exists_locally(tag: str, *, runtime: str) -> bool:
    """Check whether *tag* exists in the runtime's local image store."""
    if runtime == "podman-hpc":
        return image_exists_podman_hpc(tag)
    try:
        result = subprocess.run(
            [runtime, "image", "inspect", tag],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def image_exists_podman_hpc(tag: str) -> bool:
    try:
        result = subprocess.run(
            ["podman-hpc", "image", "exists", tag],
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
    *,
    runtime: str,
    build_args: dict[str, str] | None = None,
) -> ContainerBuildResult:
    """Build a container image with the given *runtime*.

    For ``podman-hpc``, the image is automatically migrated after build
    so compute nodes can access it.

    Raises :class:`ContainerBuildError` on failure.
    """
    if runtime not in RUNTIMES:
        raise ContainerBuildError(
            f"Unsupported build runtime {runtime!r}; expected one of {RUNTIMES}."
        )

    cmd: list[str] = [runtime, "build", "-t", tag, "-f", str(containerfile)]
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

    if runtime == "podman-hpc":
        _podman_hpc_migrate(tag)

    return ContainerBuildResult(
        tag=tag,
        already_existed=False,
        exit_code=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def pull_image(image: str, *, runtime: str) -> None:
    """Pull *image* into the runtime's local image store.

    Used by ``lc build`` so that pre-built registry images (e.g.
    ``python:3.12-slim``) are present before ``lc run`` invokes the
    runtime with ``--pull=never``.

    Raises :class:`ContainerBuildError` on failure or if *runtime* isn't
    on PATH.
    """
    if runtime not in RUNTIMES:
        raise ContainerBuildError(
            f"Unsupported runtime {runtime!r}; expected one of {RUNTIMES}."
        )
    try:
        proc = subprocess.run(
            [runtime, "pull", image],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        raise ContainerBuildError(
            f"{runtime} is not installed or not on PATH."
        )
    if proc.returncode != 0:
        raise ContainerBuildError(
            f"{runtime} pull {image} failed (exit code {proc.returncode}):\n"
            f"{proc.stderr}"
        )
    if runtime == "podman-hpc":
        _podman_hpc_migrate(image)


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


# ---------------------------------------------------------------------------
# Run-time recipe wrap
# ---------------------------------------------------------------------------


def resolve_image_for_run(
    spec: str | None,
    *,
    project_path: Path,
    project_name: str,
) -> str | None:
    """Translate an astra.yaml ``container:`` value into the image tag
    that the runtime will execute.

    * ``None`` / empty → ``None`` (no container).
    * Path to a Containerfile in the project → the content-addressed tag
      that ``lc build`` would have produced (``lc-<name>-<hash>``).
    * Anything else (registry image, e.g. ``python:3.12-slim``, or a
      pre-namespaced ``ghcr.io/foo/bar:tag``) → returned as-is for the
      runtime to pull.
    """
    if not spec:
        return None
    if is_containerfile(spec, project_path):
        return compute_image_tag(project_name, project_path / spec, project_path)
    return spec


def wrap_recipe(
    recipe: str,
    *,
    image: str | None,
    runtime: str,
) -> str:
    """Wrap *recipe* so it executes inside *image* under *runtime*.

    Returns a shell-command string suitable for Snakemake's ``shell()``.
    Snakemake's ``{output[0]}`` / ``{input.X}`` / ``{wildcards.universe}``
    placeholders inside *recipe* are preserved — they substitute through
    Python's ``str.format`` at execution time, after wrapping.

    No-op cases:
        * *image* is ``None`` → recipe returned unchanged
        * *runtime* is ``"none"`` → recipe returned unchanged

    The recipe is shell-quoted with :func:`shlex.quote` and passed as the
    argument to ``bash -c`` inside the container, which keeps single
    quotes, dollar signs, and other shell metacharacters intact across
    the host bash → runtime CLI → container bash boundaries.
    """
    if image is None or runtime == "none":
        return recipe
    if runtime not in RUNTIMES:
        raise ContainerBuildError(
            f"Unsupported run runtime {runtime!r}; expected one of {RUNTIMES} or 'none'."
        )
    inner = shlex.quote(recipe)
    # ``--pull=never`` is critical for podman, which by default does
    # short-name resolution against ``unqualified-search-registries``
    # in registries.conf — that fails for ``lc-<project>-<hash>`` tags
    # produced by ``lc build`` even though the image sits in local
    # storage. Telling the runtime not to fetch sidesteps the issue and
    # is the same semantics on docker and podman-hpc. Registry images
    # (``python:3.12-slim``, ``ghcr.io/...``) must be pulled in advance
    # by ``lc build``.
    #
    # Bind-mount and chdir to $PWD so recipes that write to relative
    # paths land in the project tree. Snakemake invokes us with
    # cwd=project, so $PWD is the project root.
    return (
        f'{runtime} run --rm --pull=never '
        f'-v "$PWD":"$PWD" -w "$PWD" '
        f'{image} bash -c {inner}'
    )


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def get_container_status(
    spec: str | None,
    project_path: Path,
    project_name: str,
    *,
    runtime: str,
) -> ContainerStatus:
    """Return status information for a container spec without building."""
    if spec is None:
        return ContainerStatus(type="none")

    if not is_containerfile(spec, project_path):
        return ContainerStatus(type="prebuilt", image=spec)

    containerfile = project_path / spec
    tag = compute_image_tag(project_name, containerfile, project_path)
    exists = image_exists_locally(tag, runtime=runtime) if runtime != "none" else None
    return ContainerStatus(
        type="build",
        image=tag,
        exists=exists,
        containerfile=spec,
    )
