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
import json
import logging
import re
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

#: Runtimes we know how to build and run with. Order is detection priority:
#: podman first (rootless, no daemon to wedge), then docker (still common on
#: laptops; we additionally probe ``docker info`` so a down daemon doesn't
#: silently win over a healthy podman), then podman-hpc on login nodes.
RUNTIMES: tuple[str, ...] = ("podman", "docker", "podman-hpc")

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
    "uv.lock",
    "conda-lock.yml",
    "environment.yml",
    "environment.yaml",
)

#: Subdirectories ignored when hashing a ``COPY .`` of the build context.
#: These won't be in the docker build context for any sane project (they're
#: either VCS, caches, results, or virtualenvs); listing them here keeps the
#: tag from churning every time someone touches ``results/`` or runs tests.
_COPY_DIR_EXCLUDE: frozenset[str] = frozenset({
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "results",
    ".lightcone",
    ".snakemake",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".eggs",
    "dist",
})

#: Matches a Dockerfile-style flag like ``--from=builder`` or ``--chown=u:g``.
_FLAG_RE = re.compile(r"^--[A-Za-z][A-Za-z0-9-]*(=\S+)?$")


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
    """Return the first usable runtime in :data:`RUNTIMES`, or ``None``.

    A runtime is "usable" when its binary is on PATH and (for docker)
    its daemon answers ``docker info``. Without the daemon probe, a
    laptop with docker installed but its daemon stopped would resolve
    to docker and every recipe would fail with a socket error — even
    when a healthy podman is sitting right next to it.
    """
    for runtime in RUNTIMES:
        if shutil.which(runtime) is None:
            continue
        if runtime == "docker" and not _docker_daemon_up():
            continue
        return runtime
    return None


def _docker_daemon_up() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return result.returncode == 0


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


def _hash_file_into(path: Path, h: hashlib._Hash) -> None:
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)


def _hash_named_file(path: Path, label: str, h: hashlib._Hash) -> None:
    """Mix *path*'s identity and contents into *h* with explicit framing.

    Path-prefix + null separators stop boundary-shifting collisions —
    e.g. moving a line from ``requirements.txt`` to ``requirements-dev.txt``
    no longer yields the same digest as keeping it in place.
    """
    h.update(label.encode("utf-8"))
    h.update(b"\0")
    h.update(path.name.encode("utf-8"))
    h.update(b"\0")
    _hash_file_into(path, h)
    h.update(b"\0")


def hash_file_contents(files: list[Path]) -> str:
    """Return a SHA-256 hex digest over the framed contents of *files*.

    The digest mixes each file's basename and a label byte in addition to
    its contents, so reordering or relabelling produces different digests.
    """
    h = hashlib.sha256()
    for f in files:
        _hash_named_file(f, "f", h)
    return h.hexdigest()


def _iter_build_context_entries(
    containerfile: Path, project_path: Path
) -> Iterator[tuple[str, Path]]:
    """Yield ``(kind, path)`` for everything that contributes to a build.

    ``kind`` is one of ``"containerfile"``, ``"dep"``, ``"copy_file"``,
    ``"copy_dir"``. Sharing this iteration between :func:`compute_image_tag`
    and :func:`_populate_build_context` guarantees by construction that
    the hashed set and the staged set cover identical files — so the tag
    can never invalidate against a stage that's missing inputs (or vice
    versa).

    Sources behind ``--from=<stage>`` and URL/git ``ADD`` arguments are
    skipped — they're not part of the host build context. ``COPY .``
    yields the project root as a single ``copy_dir`` entry.
    """
    yield "containerfile", containerfile
    for dep in find_dependency_files(project_path):
        yield "dep", dep
    text = containerfile.read_text(errors="replace")
    for src_str in _parse_copy_sources(text):
        for resolved in _expand_copy_source(src_str, project_path):
            if resolved.is_file():
                yield "copy_file", resolved
            elif resolved.is_dir():
                yield "copy_dir", resolved


def compute_image_tag(
    project_name: str,
    containerfile: Path,
    project_path: Path,
) -> str:
    """Compute a content-addressed image tag.

    The tag is ``lc-<project_name>-<12-char-sha256>``.  The hash covers
    the Containerfile, every dependency file in :data:`DEPENDENCY_FILES`,
    and the contents of every ``COPY``/``ADD`` source path referenced
    from the Containerfile (files hashed directly, directories walked
    recursively with stable ordering and :data:`_COPY_DIR_EXCLUDE`
    subtrees skipped).
    """
    h = hashlib.sha256()
    for kind, path in _iter_build_context_entries(containerfile, project_path):
        if kind == "containerfile":
            _hash_named_file(path, "containerfile", h)
        elif kind == "dep":
            _hash_named_file(path, "dep", h)
        else:
            rel = _safe_relpath(path, project_path)
            h.update(b"copy\0")
            h.update(rel.encode("utf-8"))
            h.update(b"\0")
            if kind == "copy_file":
                h.update(b"file\0")
                _hash_file_into(path, h)
            else:
                h.update(b"dir\0")
                _hash_dir_into(path, h)
            h.update(b"\0")

    digest = h.hexdigest()[:12]
    safe_name = project_name.lower().replace(" ", "-")
    return f"lc-{safe_name}-{digest}"


def _safe_relpath(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _hash_dir_into(directory: Path, h: hashlib._Hash) -> None:
    """Hash *directory* recursively, skipping :data:`_COPY_DIR_EXCLUDE` subtrees.

    Files are hashed in sorted relative-path order with path-prefix framing,
    so renames and reorderings change the digest.
    """
    files: list[Path] = []
    for p in directory.rglob("*"):
        if not p.is_file():
            continue
        if any(part in _COPY_DIR_EXCLUDE for part in p.relative_to(directory).parts):
            continue
        files.append(p)
    for p in sorted(files, key=lambda x: x.relative_to(directory).as_posix()):
        rel = p.relative_to(directory).as_posix().encode("utf-8")
        h.update(b"path\0")
        h.update(rel)
        h.update(b"\0data\0")
        _hash_file_into(p, h)
        h.update(b"\0")


def _parse_copy_sources(containerfile_text: str) -> list[str]:
    """Return raw source strings from ``COPY``/``ADD`` lines.

    Skips ``--from=<stage>`` copies (those reference another build stage,
    not the host context) and URL/git arguments (network resources, not
    part of the local context we can hash). Glob patterns and relative
    paths are returned verbatim — :func:`_expand_copy_source` resolves
    them against the project tree.

    Handles backslash line continuations and the JSON exec form
    (``COPY ["src", "dest"]``). Heredoc COPY (``COPY <<EOF``) is not
    interpreted.
    """
    sources: list[str] = []
    text = re.sub(r"\\\r?\n", "", containerfile_text)
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head, _, body = line.partition(" ")
        if head.upper() not in ("COPY", "ADD"):
            continue
        body = body.strip()
        if not body:
            continue

        if body.startswith("["):
            try:
                items = json.loads(body)
            except json.JSONDecodeError:
                continue
            if not isinstance(items, list) or len(items) < 2:
                continue
            for s in items[:-1]:
                if isinstance(s, str) and "://" not in s:
                    sources.append(s)
            continue

        try:
            tokens = shlex.split(body)
        except ValueError:
            continue
        from_stage = False
        operands: list[str] = []
        for tok in tokens:
            if _FLAG_RE.match(tok):
                if tok.startswith("--from="):
                    from_stage = True
                continue
            operands.append(tok)
        if from_stage or len(operands) < 2:
            continue
        for s in operands[:-1]:
            if "://" in s or s.startswith("git@"):
                continue
            sources.append(s)
    return sources


def _expand_copy_source(src: str, project_path: Path) -> list[Path]:
    """Resolve a ``COPY``/``ADD`` source to actual paths under *project_path*.

    Returns ``[project_path]`` for ``.`` (whole context). Globs are
    expanded against *project_path*. Paths that escape the project root
    are dropped — we don't hash arbitrary host filesystem.
    """
    src = src.lstrip("/")
    if not src or src == ".":
        return [project_path]
    if any(c in src for c in "*?["):
        return sorted(project_path.glob(src))
    candidate = (project_path / src).resolve()
    try:
        candidate.relative_to(project_path.resolve())
    except ValueError:
        return []
    if candidate.exists():
        return [candidate]
    return []


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


def _populate_build_context(
    staged: Path, containerfile: Path, source_context: Path
) -> None:
    """Mirror the Containerfile + its referenced sources into *staged*.

    Why this exists: NERSC's home and CFS filesystems are mounted via
    Cray DVS, which doesn't implement ``llistxattr`` (returns ``EPROTO``).
    Buildah's copier — used by ``podman``, ``podman-hpc``, and any other
    buildah-backed runtime — calls ``llistxattr`` unconditionally on every
    ``COPY`` source and crashes when the project lives on DVS. Staging
    the build context into ``$TMPDIR`` (tmpfs on Linux) sidesteps the
    issue entirely without forcing the user to relocate their project.

    The set of staged files is :func:`_iter_build_context_entries` — the
    same iteration :func:`compute_image_tag` hashes — so the tag can't
    invalidate against a stage that's missing files.
    """
    src_root = source_context.resolve()
    for kind, path in _iter_build_context_entries(containerfile, source_context):
        if kind in ("containerfile", "dep"):
            shutil.copy2(path, staged / path.name)
            continue
        try:
            rel = path.resolve().relative_to(src_root)
        except ValueError:
            continue
        dest = staged / rel if rel.parts else staged
        if kind == "copy_file":
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
        else:  # copy_dir
            _copy_tree_filtered(path, dest)


def _copy_tree_filtered(src: Path, dst: Path) -> None:
    """Copy *src* into *dst* recursively, skipping :data:`_COPY_DIR_EXCLUDE`."""
    def _ignore(_dir: str, names: list[str]) -> list[str]:
        return [n for n in names if n in _COPY_DIR_EXCLUDE]

    shutil.copytree(src, dst, ignore=_ignore, symlinks=True, dirs_exist_ok=True)


def build_image(
    tag: str,
    containerfile: Path,
    context: Path,
    *,
    runtime: str,
    build_args: dict[str, str] | None = None,
) -> ContainerBuildResult:
    """Build a container image with the given *runtime*.

    The build context is staged into a fresh tempdir before invocation
    (see :func:`_populate_build_context`). For ``podman-hpc``, the image
    is automatically migrated after build so compute nodes can access it.

    Raises :class:`ContainerBuildError` on failure.
    """
    if runtime not in RUNTIMES:
        raise ContainerBuildError(
            f"Unsupported build runtime {runtime!r}; expected one of {RUNTIMES}."
        )

    with tempfile.TemporaryDirectory(prefix="lc-build-") as staged_str:
        staged = Path(staged_str)
        _populate_build_context(staged, containerfile, context)
        staged_cf = staged / containerfile.name
        cmd: list[str] = [runtime, "build", "-t", tag, "-f", str(staged_cf)]
        for key, value in (build_args or {}).items():
            cmd += ["--build-arg", f"{key}={value}"]
        cmd.append(str(staged))

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


def make_image_tag_resolver(
    project_path: Path,
    project_name: str,
) -> Callable[[str | None], str | None]:
    """Memoizing wrapper around :func:`resolve_image_for_run`.

    Multiple outputs typically share the same Containerfile, and resolving
    a Containerfile path re-hashes it plus all dependency files
    (lockfiles can be MB each). The returned closure caches by spec
    string for the lifetime of the caller's loop.
    """
    cache: dict[str | None, str | None] = {}

    def resolve(spec: str | None) -> str | None:
        if spec in cache:
            return cache[spec]
        tag = resolve_image_for_run(
            spec, project_path=project_path, project_name=project_name
        )
        cache[spec] = tag
        return tag

    return resolve


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
