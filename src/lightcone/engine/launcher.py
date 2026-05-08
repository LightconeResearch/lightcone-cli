"""Backend for `lc launch <target>` — interactive containerized environments.

``lc launch claude`` detects the host runtime, builds (or reuses) a cached
Claude Code environment container, and exec's into it interactively with
the project directory mounted at the same absolute path.

Inside the container:
  - ``lc build`` uses ``buildah`` to produce OCI tarballs.
  - ``lc run`` wraps recipes with ``apptainer exec oci-archive:``.
  - ``LIGHTCONE_CONTAINER=1`` is set so commands know they're sandboxed.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from lightcone.engine.container import (
    _DAEMONLESS_RUNTIMES,
    ContainerBuildError,
    RuntimeChoice,
    build_image,
    compute_image_tag,
    image_exists_locally,
    load_image_from_tarball,
    pull_image,
    save_image_as_tarball,
    tarball_path_for_tag,
)
from lightcone.engine.manifest import lc_version as _lc_version

# Registry where pre-built release images are published.
_GHCR_PREFIX = "ghcr.io/lightconeresearch"

# Local image name for the shared sandbox base image.
_SANDBOX_IMAGE_NAME = "lightcone-sandbox"


def _package_containers_dir() -> Path:
    """Return the path to the bundled ``containers/`` directory.

    Two layouts are supported:

    * **Editable install** (``uv sync`` / ``pip install -e .``): the package
      source lives at ``src/lightcone/engine/launcher.py`` inside the project
      tree, so climbing four parents reaches the project root, then
      ``claude/lightcone/containers/`` is a sibling directory.

    * **Wheel install** (``pip install lightcone-cli`` or any regular install):
      ``pyproject.toml`` uses ``force-include`` to bundle the plugin directory
      as ``lightcone/cli/claude/lightcone/``, so ``containers/`` sits two
      parents above this file inside site-packages.
    """
    candidates = [
        # Wheel install: site-packages/lightcone/engine/launcher.py
        #   → site-packages/lightcone/cli/claude/lightcone/containers/
        Path(__file__).parent.parent / "cli" / "claude" / "lightcone" / "containers",
        # Editable install: src/lightcone/engine/launcher.py
        #   → <project_root>/claude/lightcone/containers/
        Path(__file__).parent.parent.parent.parent / "claude" / "lightcone" / "containers",
    ]
    for c in candidates:
        if c.is_dir():
            return c
    raise ContainerBuildError(
        "Could not locate the bundled containers directory. "
        "Is lightcone-cli installed correctly?"
    )


@dataclass(frozen=True)
class LaunchTarget:
    """Descriptor for a named interactive container environment."""

    name: str
    containerfile: Path
    entrypoint: list[str]
    env_passthrough: list[str] = field(default_factory=list)
    devices: list[str] = field(default_factory=list)
    #: Sub-paths of ``$HOME`` to bind-mount at the same absolute path inside
    #: the container.  Entries ending with ``/`` are directories; others are
    #: files.  Missing paths are created automatically before mounting.
    home_mounts: list[str] = field(default_factory=list)
    #: When True, pass ``--user <uid>:<gid>`` so the container process runs as
    #: the calling user rather than root.  Required for tools (e.g. Claude Code)
    #: that refuse ``--dangerously-skip-permissions`` under root.
    run_as_host_user: bool = False
    #: Override the GHCR image name used for pull-first.  Defaults to ``name``
    #: when None.  Needed when the published image name differs from the target
    #: name (e.g. all harnesses share the base ``"lightcone-sandbox"`` image).
    registry_name: str | None = None
    #: Shell commands run inside the base image to install the harness.
    #: Joined with `` && `` and passed to ``sh -c``.
    install_cmds: list[str] = field(default_factory=list)
    #: Prefix for the local committed image tag, e.g. ``"lightcone-claude"``.
    #: Full tag: ``<committed_tag_prefix>:<lc_version>``.
    committed_tag_prefix: str = ""


#: Set when _make_builtin_targets() catches a ContainerBuildError so
#: resolve_launch_target can surface a helpful installation error instead of
#: the misleading "Available: (none)" message.
_builtin_targets_error: str | None = None


def _make_builtin_targets() -> dict[str, LaunchTarget]:
    global _builtin_targets_error
    try:
        containers_dir = _package_containers_dir()
    except ContainerBuildError as e:
        _builtin_targets_error = str(e)
        return {}
    return {
        "claude": LaunchTarget(
            name="claude",
            containerfile=containers_dir / f"{_SANDBOX_IMAGE_NAME}.Containerfile",
            entrypoint=["claude", "--dangerously-skip-permissions"],
            env_passthrough=[
                "ANTHROPIC_API_KEY",
                "ANTHROPIC_BASE_URL",
                "CLAUDE_CODE_OAUTH_TOKEN",
                "HOME",
                "TERM",
            ],
            devices=["/dev/fuse"],
            home_mounts=[
                ".claude.json",
                ".claude/settings.json",
                ".claude/settings.local.json",
                ".claude/keybindings.json",
                ".claude/session-env/",
            ],
            # Claude Code refuses --dangerously-skip-permissions as root;
            # running as the host UID/GID also ensures correct ownership on
            # the mounted project directory.
            run_as_host_user=True,
            registry_name=_SANDBOX_IMAGE_NAME,
            install_cmds=[
                "npm install -g @anthropic-ai/claude-code --loglevel=error --no-update-notifier"
            ],
            committed_tag_prefix="lightcone-claude",
        ),
        "mistral-vibe": LaunchTarget(
            name="mistral-vibe",
            containerfile=containers_dir / f"{_SANDBOX_IMAGE_NAME}.Containerfile",
            entrypoint=["vibe"],
            env_passthrough=["MISTRAL_API_KEY"],
            home_mounts=[
                ".vibe/config.toml",
                ".vibe/agents/",
                ".vibe/prompts/",
                ".vibe/skills/",
                ".vibe/tools/",
            ],
            registry_name=_SANDBOX_IMAGE_NAME,
            install_cmds=["uv tool install mistral-vibe"],
            committed_tag_prefix="lightcone-mistral-vibe",
        ),
        "opencode": LaunchTarget(
            name="opencode",
            containerfile=containers_dir / f"{_SANDBOX_IMAGE_NAME}.Containerfile",
            entrypoint=["opencode"],
            env_passthrough=[
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
                "MISTRAL_API_KEY",
                "GEMINI_API_KEY",
                "GROQ_API_KEY",
            ],
            home_mounts=[
                ".config/opencode/opencode.json",
                ".config/opencode/tui.json",
                ".config/opencode/agents/",
                ".config/opencode/commands/",
                ".config/opencode/modes/",
                ".config/opencode/plugins/",
                ".config/opencode/themes/",
                ".config/opencode/AGENTS.md",
            ],
            registry_name=_SANDBOX_IMAGE_NAME,
            install_cmds=["npm install -g opencode-ai --loglevel=error --no-update-notifier"],
            committed_tag_prefix="lightcone-opencode",
        ),
    }


BUILTIN_TARGETS: dict[str, LaunchTarget] = _make_builtin_targets()


def resolve_launch_target(name: str, project_root: Path | None = None) -> LaunchTarget:
    """Return the :class:`LaunchTarget` for *name*.

    Checks built-in targets first, then ``.lightcone/launch/<name>.yaml``
    for future project-local targets (not yet implemented; *project_root*
    is accepted now so call sites don't need updating when it is).

    Raises :class:`ContainerBuildError` if the target is unknown.
    """
    if name in BUILTIN_TARGETS:
        return BUILTIN_TARGETS[name]
    if not BUILTIN_TARGETS and _builtin_targets_error:
        raise ContainerBuildError(
            f"Unknown launch target {name!r}: built-in targets are unavailable. "
            f"{_builtin_targets_error}"
        )
    raise ContainerBuildError(
        f"Unknown launch target {name!r}. "
        f"Available: {', '.join(BUILTIN_TARGETS) or '(none)'}"
    )


# Matches ``ARG LIGHTCONE_VERSION`` with or without a trailing ``=<value>``,
# so we handle both the bare form and an existing default.
_ARG_VERSION_RE = re.compile(r"^ARG LIGHTCONE_VERSION(=[^\n]*)?\n", re.MULTILINE)

# Matches the comment + RUN block that handles lightcone-cli installation.
# Present only in Containerfiles that use the dev-wheel fallback pattern.
# Intentionally coupled to lightcone-sandbox.Containerfile's specific install block
# shape: the pattern ends at the first `esac\n`, so inserting a second case
# statement before this block would truncate the match — update the anchor if
# the Containerfile layout changes.
_LIGHTCONE_INSTALL_RE = re.compile(r"# Dev/local builds.*?esac\n", re.DOTALL)


def _is_dev_version(version: str) -> bool:
    """Return True if *version* is a dev/local build not published to PyPI."""
    return version == "dev" or ".dev" in version or "+" in version


def _find_source_root() -> Path | None:
    """Return the lightcone-cli project root for editable installs, or None.

    Two discovery strategies are tried in order:

    1. **``LIGHTCONE_SRC`` environment variable** — set this to the project
       root when running ``lc`` from a ``uv tool install`` (non-editable).
       Must contain ``pyproject.toml``; silently ignored otherwise.

    2. **Editable-install heuristic** — for ``uv sync`` / ``pip install -e``
       the layout is ``<project_root>/src/lightcone/engine/launcher.py`` so
       ``Path(__file__).parents[3]`` is the project root.  For a regular
       installed package the same path resolves to a site-packages parent,
       which won't contain ``pyproject.toml`` — in that case we return
       ``None``.
    """
    env_src = os.environ.get("LIGHTCONE_SRC")
    if env_src:
        candidate = Path(env_src)
        if (candidate / "pyproject.toml").exists():
            return candidate

    candidate = Path(__file__).parents[3]
    if (candidate / "pyproject.toml").exists():
        return candidate
    return None


def _build_dev_wheel(dest_dir: Path) -> Path | None:
    """Build a wheel from the current source tree into *dest_dir*.

    Used when the running version is a dev/PR build that is not on PyPI so
    ``uv pip install lightcone-cli==<dev-version>`` would fail.  The wheel
    is placed in the Containerfile build context so the ``COPY`` step can
    pick it up, and its contents feed into the image tag hash — meaning the
    container auto-rebuilds whenever the local source changes.

    If a wheel for the *current* version already exists in *dest_dir* it is
    returned immediately without rebuilding.  This is important: wheel builds
    embed zip timestamps, so running ``uv build`` twice on unchanged source
    produces different bytes → a different context hash → a different image
    tag → a spurious full container rebuild on every ``lc launch``.

    Returns the wheel :class:`Path`, or ``None`` if the source root cannot be
    located (non-editable install) or the build fails.
    """
    src_root = _find_source_root()
    if src_root is None:
        return None

    version = _lc_version()
    # Reuse a wheel that was already built for this exact version.
    existing = sorted(dest_dir.glob(f"lightcone_cli-{version}-*.whl"))
    if existing:
        return existing[-1]

    # No matching wheel — remove stale wheels from other versions, then build.
    for old in dest_dir.glob("lightcone_cli-*.whl"):
        old.unlink(missing_ok=True)
    try:
        subprocess.run(
            ["uv", "build", "--wheel", "--out-dir", str(dest_dir)],
            cwd=src_root,
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    wheels = sorted(dest_dir.glob("lightcone_cli-*.whl"))
    return wheels[-1] if wheels else None


def _render_containerfile(target: LaunchTarget, project_root: Path) -> Path:
    """Write a rendered copy of the target's Containerfile to .lightcone/containers/.

    Substitutes ``ARG LIGHTCONE_VERSION`` (bare or with a default) with
    ``ARG LIGHTCONE_VERSION=<version>`` so the content hash — and therefore
    the image tag — changes when ``lc`` is upgraded.

    For dev/PR builds (version not on PyPI), if the Containerfile contains the
    lightcone install block and the source tree is reachable, a wheel is built
    from the local source and the install block is replaced with a direct
    ``COPY <wheel> /tmp/ + uv pip install`` so the exact in-development code
    ends up inside the container.  If the wheel build fails the existing
    case-based fallback (latest stable from PyPI) is preserved.
    """
    dest_dir = project_root / ".lightcone" / "containers"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / target.containerfile.name

    version = _lc_version()
    content = target.containerfile.read_text()
    content = _ARG_VERSION_RE.sub(f"ARG LIGHTCONE_VERSION={version}\n", content)

    if _is_dev_version(version) and _LIGHTCONE_INSTALL_RE.search(content):
        wheel = _build_dev_wheel(dest_dir)
        if wheel is not None:
            # Install the dev wheel directly.  uv resolves all current deps
            # from PyPI; the system Python (apt-installed python3) is the
            # target, so pre-built manylinux_2_17 wheels are accepted without
            # any source compilation.
            wheel_block = (
                f"COPY {wheel.name} /tmp/{wheel.name}\n"
                f"RUN uv pip install --system --break-system-packages /tmp/{wheel.name}\n"
            )
            content = _LIGHTCONE_INSTALL_RE.sub(wheel_block, content)

    dest.write_text(content)
    return dest


def _registry_image_ref(target_name: str, version: str) -> str:
    """Return the GHCR image reference for a published release of *target_name*.

    Pattern: ``ghcr.io/lightconeresearch/<target_name>:<version>``
    """
    return f"{_GHCR_PREFIX}/{target_name}:{version}"


def _tracking_image_ref(project_root: Path, version: str) -> str:
    """Return the human-readable image ref for ``docker/podman images`` visibility.

    Format: ``lightcone-<project_dir_name>:<lc_version>``
    """
    return f"lightcone-{project_root.name}:{version}"


def _apply_tracking_tag(content_tag: str, tracking_ref: str, runtime: str) -> None:
    """Tag *content_tag* with *tracking_ref* for human-readable image listings.

    Failure is silently swallowed — the tracking tag is cosmetic and must not
    block the launch.
    """
    try:
        subprocess.run(
            [runtime, "tag", content_tag, tracking_ref],
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, OSError):
        pass


def _try_pull_and_cache(
    tag: str,
    registry_ref: str,
    tarball: Path,
    *,
    runtime: str,
) -> bool:
    """Attempt to pull *registry_ref* from GHCR and cache it locally.

    On success the pulled image is retagged to the content-addressed *tag*,
    saved as *tarball*, and ``True`` is returned.  Any failure (network
    unavailable, image not published, unsupported runtime) silently returns
    ``False`` so the caller can fall back to a local build.

    Daemonless runtimes (apptainer, singularity) cannot pull registry images
    directly; this function returns ``False`` immediately for them.
    """
    if runtime in _DAEMONLESS_RUNTIMES:
        return False
    try:
        _print(f"Pulling {registry_ref} from registry…")
        pull_image(registry_ref, runtime=runtime)
        # Retag to the content-addressed local tag so the rest of the launch
        # pipeline (image_exists_locally, _exec_interactive) works unchanged.
        subprocess.run(
            [runtime, "tag", registry_ref, tag],
            check=True,
            capture_output=True,
        )
        save_image_as_tarball(tag, tarball, runtime=runtime)
        return True
    except (ContainerBuildError, subprocess.CalledProcessError, OSError):
        _print("Registry pull failed — falling back to local build.")
        return False


def _image_exists(tag: str, runtime: str) -> bool:
    """Return True if *tag* exists in the local image store."""
    try:
        result = subprocess.run(
            [runtime, "image", "inspect", tag],
            capture_output=True,
        )
        return result.returncode == 0
    except OSError:
        return False


def _ensure_harness_image(
    target: LaunchTarget,
    base_image: str,
    runtime: str,
    lc_version: str,
    reinstall: bool = False,
) -> str:
    """Return the local committed image tag for *target*, installing if absent.

    On first call (or when *reinstall* is True): spins up *base_image* in a
    temporary container, runs ``target.install_cmds`` inside it, commits the
    result as ``<committed_tag_prefix>:<lc_version>``, and removes the temp
    container.  On subsequent calls the existing committed image is reused.
    """
    # OCI tags allow [a-zA-Z0-9_.-] only — replace '+' from PEP 440 local versions.
    safe_version = lc_version.replace("+", "-")
    committed_tag = f"{target.committed_tag_prefix}:{safe_version}"

    if not reinstall and _image_exists(committed_tag, runtime):
        return committed_tag

    if reinstall:
        # Remove the old committed image so docker commit doesn't leave a dangling layer.
        subprocess.run([runtime, "rmi", committed_tag], check=False, capture_output=True)

    tmp_name = f"lc-install-{target.name}-{uuid4().hex[:8]}"
    install_cmd = " && ".join(target.install_cmds)
    _print(f"Installing {target.name} harness (first run — this may take a few minutes)…")
    try:
        subprocess.run(
            [runtime, "run", "--entrypoint", "sh", "--name", tmp_name,
             base_image, "-c", install_cmd],
            check=True,
        )
        subprocess.run(
            [runtime, "commit", tmp_name, committed_tag],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        raise ContainerBuildError(f"Harness install failed for {target.name}: {exc}") from exc
    finally:
        subprocess.run([runtime, "rm", "-f", tmp_name], check=False, capture_output=True)

    return committed_tag


def launch_target(
    name: str,
    *,
    choice: RuntimeChoice,
    project_root: Path,
    reinstall: bool = False,
) -> None:
    """Build (if needed) and exec the named launch target interactively.

    For non-dev versions, tries to pull the pre-built image from GHCR before
    falling back to a local build.  Replaces the current process via
    ``os.execvp`` — this function does not return on success.
    """
    target = resolve_launch_target(name, project_root)

    rendered_cf = _render_containerfile(target, project_root)
    tag = compute_image_tag(target.name, rendered_cf, rendered_cf.parent)
    tarball = tarball_path_for_tag(tag, project_root)

    if not tarball.exists():
        version = _lc_version()
        pulled = False
        if not _is_dev_version(version):
            registry_ref = _registry_image_ref(target.registry_name or target.name, version)
            pulled = _try_pull_and_cache(tag, registry_ref, tarball, runtime=choice.runtime)
        if not pulled:
            _print(f"Building {name} container (first run — this may take a few minutes)…")
            build_image(tag, rendered_cf, rendered_cf.parent, runtime=choice.runtime)
            save_image_as_tarball(tag, tarball, runtime=choice.runtime)

    if not image_exists_locally(tag, runtime=choice.runtime, project_path=project_root):
        load_image_from_tarball(tarball, runtime=choice.runtime)

    if target.committed_tag_prefix:
        tag = _ensure_harness_image(
            target,
            base_image=tag,
            runtime=choice.runtime,
            lc_version=_lc_version(),
            reinstall=reinstall,
        )

    _apply_tracking_tag(tag, _tracking_image_ref(project_root, _lc_version()), choice.runtime)

    _exec_interactive(target, tag, choice, project_root)


def _ensure_host_path(path: Path, *, is_dir: bool) -> None:
    """Create *path* on the host if it does not exist.

    Entries in ``home_mounts`` ending with ``/`` are directories; all others
    are files.  Creating the path before bind-mounting prevents Docker/Podman
    from silently creating a directory when the intended target is a file.
    """
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if is_dir:
        path.mkdir(parents=True, exist_ok=True)
    else:
        path.touch()


def _exec_interactive(
    target: LaunchTarget,
    tag: str,
    choice: RuntimeChoice,
    project_root: Path,
) -> None:
    """Build the docker/podman run command and exec it, replacing this process."""
    project_abs = str(project_root.resolve())

    cmd: list[str] = [choice.runtime, "run", "--rm", "-it"]
    cmd += ["-v", f"{project_abs}:{project_abs}", "-w", project_abs]

    for var in target.env_passthrough:
        if var in os.environ:
            # Pass only the name so docker/podman inherit the value from the
            # current process environment — avoids embedding secrets in the
            # argv list where /proc/<pid>/cmdline is world-readable.
            cmd += ["-e", var]

    home = os.environ.get("HOME")
    if home:
        for subpath in target.home_mounts:
            is_dir = subpath.endswith("/")
            host_path = Path(home) / subpath.rstrip("/")
            _ensure_host_path(host_path, is_dir=is_dir)
            path_str = str(host_path)
            cmd += ["-v", f"{path_str}:{path_str}"]

    for device in target.devices:
        if Path(device).exists():
            cmd += ["--device", device]

    if target.run_as_host_user:
        # Map the container process to the host UID/GID so files written to
        # the bind-mounted project belong to the user (not to a remapped
        # subuid) and so Claude Code doesn't see itself running as root.
        #
        # docker (rootful, the common case) accepts --user directly.
        # podman (rootless) needs --userns=keep-id, the rootless equivalent
        # that builds an idmap preserving the host UID inside the container.
        # podman-hpc (NERSC) cannot use --userns=keep-id with a real TTY:
        # the combination triggers
        #   crun: open .../merged: Permission denied
        # during rootfs setup (TTY device creation in the fuse-overlayfs
        # remapped merged dir fails). podman-hpc is rootless and already
        # runs as the host UID externally, so for our purposes we leave the
        # container's internal UID at 0 and rely on rootless mapping for
        # bind-mount ownership.
        if choice.runtime == "podman":
            cmd += ["--userns=keep-id"]
        elif choice.runtime == "podman-hpc":
            pass  # see comment above
        else:
            cmd += ["--user", f"{os.getuid()}:{os.getgid()}"]

    if target.entrypoint:
        cmd += ["--entrypoint", target.entrypoint[0]]

    cmd.append(tag)

    # On podman-hpc we cannot use --userns=keep-id (see above), so the
    # container runs as UID 0. Claude Code rejects
    # --dangerously-skip-permissions when invoked as root, so drop the
    # flag — the user will see the folder-trust prompt once and accept
    # it manually.
    entrypoint_args = target.entrypoint[1:]
    if choice.runtime == "podman-hpc":
        entrypoint_args = [
            a for a in entrypoint_args if a != "--dangerously-skip-permissions"
        ]
    cmd.extend(entrypoint_args)

    if os.environ.get("LIGHTCONE_LAUNCH_DEBUG"):
        _print(f"[lc launch debug] {' '.join(cmd)}")

    os.execvp(cmd[0], cmd)


def _print(msg: str) -> None:
    print(msg, file=sys.stderr)
