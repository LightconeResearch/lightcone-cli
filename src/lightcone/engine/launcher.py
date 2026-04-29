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

from lightcone.engine.container import (
    ContainerBuildError,
    RuntimeChoice,
    build_image,
    compute_image_tag,
    image_exists_locally,
    load_image_from_tarball,
    save_image_as_tarball,
    tarball_path_for_tag,
)
from lightcone.engine.manifest import lc_version as _lc_version


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


def _make_builtin_targets() -> dict[str, LaunchTarget]:
    try:
        containers_dir = _package_containers_dir()
    except ContainerBuildError:
        return {}
    return {
        "claude": LaunchTarget(
            name="claude",
            containerfile=containers_dir / "claude-env.Containerfile",
            entrypoint=["claude"],
            env_passthrough=["ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "HOME", "TERM"],
            devices=["/dev/fuse"],
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
    raise ContainerBuildError(
        f"Unknown launch target {name!r}. "
        f"Available: {', '.join(BUILTIN_TARGETS) or '(none)'}"
    )


# Matches ``ARG LIGHTCONE_VERSION`` with or without a trailing ``=<value>``,
# so we handle both the bare form and an existing default.
_ARG_VERSION_RE = re.compile(r"^ARG LIGHTCONE_VERSION(=[^\n]*)?\n", re.MULTILINE)

# Matches the comment + RUN block that handles lightcone-cli installation.
# Present only in Containerfiles that use the dev-wheel fallback pattern.
_LIGHTCONE_INSTALL_RE = re.compile(r"# Dev/local builds.*?esac\n", re.DOTALL)


def _is_dev_version(version: str) -> bool:
    """Return True if *version* is a dev/local build not published to PyPI."""
    return version == "dev" or ".dev" in version or "+" in version


def _find_source_root() -> Path | None:
    """Return the lightcone-cli project root for editable installs, or None.

    For an editable install the layout is::

        <project_root>/src/lightcone/engine/launcher.py

    so ``Path(__file__).parents[3]`` is the project root.  For a regular
    installed package the same path resolves to a site-packages parent, which
    won't contain ``pyproject.toml`` — in that case we return ``None``.
    """
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

    Returns the wheel :class:`Path`, or ``None`` if the source root cannot be
    located (non-editable install) or the build fails.
    """
    src_root = _find_source_root()
    if src_root is None:
        return None
    # Remove stale wheels from previous builds to avoid accumulation.
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
    dest = dest_dir / f"{target.name}.Containerfile"

    version = _lc_version()
    content = target.containerfile.read_text()
    content = _ARG_VERSION_RE.sub(f"ARG LIGHTCONE_VERSION={version}\n", content)

    if _is_dev_version(version) and _LIGHTCONE_INSTALL_RE.search(content):
        wheel = _build_dev_wheel(dest_dir)
        if wheel is not None:
            # Two-step install: resolve all deps via the latest stable release
            # from PyPI first (avoiding resolution failures caused by transitive
            # deps that were added to pyproject.toml after the last release but
            # have not yet been published), then replace just the lightcone-cli
            # package itself with the local dev wheel.  --no-deps on the second
            # step keeps the already-resolved dep set intact.
            wheel_block = (
                f"COPY {wheel.name} /tmp/{wheel.name}\n"
                f"RUN uv pip install --system lightcone-cli"
                f" && uv pip install --system --no-deps /tmp/{wheel.name}\n"
            )
            content = _LIGHTCONE_INSTALL_RE.sub(wheel_block, content)

    dest.write_text(content)
    return dest


def launch_target(
    name: str,
    *,
    choice: RuntimeChoice,
    project_root: Path,
) -> None:
    """Build (if needed) and exec the named launch target interactively.

    Replaces the current process via ``os.execvp`` — this function does not
    return on success.
    """
    target = resolve_launch_target(name, project_root)

    rendered_cf = _render_containerfile(target, project_root)
    tag = compute_image_tag(target.name, rendered_cf, rendered_cf.parent)
    tarball = tarball_path_for_tag(tag, project_root)

    if not tarball.exists():
        _print(f"Building {name} container (first run — this may take a few minutes)…")
        build_image(tag, rendered_cf, rendered_cf.parent, runtime=choice.runtime)
        save_image_as_tarball(tag, tarball, runtime=choice.runtime)

    if not image_exists_locally(tag, runtime=choice.runtime):
        load_image_from_tarball(tarball, runtime=choice.runtime)

    _exec_interactive(target, tag, choice, project_root)


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
        val = os.environ.get(var)
        if val is not None:
            cmd += ["-e", f"{var}={val}"]

    for device in target.devices:
        if Path(device).exists():
            cmd += ["--device", device]

    if choice.runtime == "podman-hpc":
        cmd.append("--no-setns")

    cmd.append(tag)
    cmd.extend(target.entrypoint)

    os.execvp(cmd[0], cmd)


def _print(msg: str) -> None:
    print(msg, file=sys.stderr)
