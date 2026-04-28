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
    """Return the path to ``claude/lightcone/containers/`` in the package tree."""
    candidates = [
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


def _render_containerfile(target: LaunchTarget, project_root: Path) -> Path:
    """Write a rendered copy of the target's Containerfile to .lightcone/containers/.

    Substitutes ``ARG LIGHTCONE_VERSION`` (bare or with a default) with
    ``ARG LIGHTCONE_VERSION=<version>`` so the content hash — and therefore
    the image tag — changes when ``lc`` is upgraded.
    """
    dest_dir = project_root / ".lightcone" / "containers"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{target.name}.Containerfile"

    version = _lc_version()
    content = target.containerfile.read_text()
    content = _ARG_VERSION_RE.sub(f"ARG LIGHTCONE_VERSION={version}\n", content)
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
