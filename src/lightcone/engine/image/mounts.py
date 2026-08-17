"""The engine container's mount set — mounts bound the world (spec §7).

The container sees: the project tree at its **identical absolute path**
(RW for materialize — the engine writes results and manifests; RO for
probes), each declared external input RO, tmpfs ``/tmp`` and
``/dev/shm`` — and nothing else from the host. The environment is baked
(``/opt/venv``); no env mounts exist.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from lightcone.engine.image.errors import DeclarationError


@dataclass(frozen=True)
class MountSet:
    project: Path  # realpath'd
    external_inputs: tuple[Path, ...]  # realpath'd, deduped, RO
    readonly_project: bool = False  # probe posture

    def to_podman_args(self) -> list[str]:
        project_mode = "ro" if self.readonly_project else "rw"
        args = [
            "-v", f"{self.project}:{self.project}:{project_mode}",
        ]
        for inp in self.external_inputs:
            args += ["-v", f"{inp}:{inp}:ro"]
        args += [
            "--tmpfs", "/tmp:rw,exec",
            # podman's default /dev/shm is 64MB — a classic scientific-
            # workload footgun (multiprocessing, torch DataLoader).
            "--shm-size", "1g",
        ]
        return args

    def sources(self) -> list[Path]:
        """Every host path the container mounts — the macOS machine
        share-preflight checks each one."""
        return [self.project, *self.external_inputs]


def compute_mount_set(
    project: Path,
    *,
    external_inputs: Iterable[Path] = (),
    readonly_project: bool = False,
) -> MountSet:
    project = project.resolve()
    deduped: list[Path] = []
    for raw in external_inputs:
        p = Path(raw).resolve()
        if not p.exists():
            continue
        if p == project or p.is_relative_to(project):
            continue  # in-tree: the project mount covers it
        if project.is_relative_to(p):
            raise DeclarationError(
                f"declared input {p} is a parent of the project root — "
                "mounting it would silently widen the container's world. "
                "Declare the specific files/directories instead."
            )
        if any(p == d or p.is_relative_to(d) for d in deduped):
            continue
        deduped = [d for d in deduped if not d.is_relative_to(p)]
        deduped.append(p)
    return MountSet(
        project=project,
        external_inputs=tuple(sorted(deduped)),
        readonly_project=readonly_project,
    )


def external_input_paths(
    project: Path, spec: dict[str, object] | None = None
) -> tuple[Path, ...]:
    """Union of resolved external input paths across every output's
    declared inputs — what the container must mount RO and a probe may
    read. Resolution goes through
    :func:`lightcone.engine.tree.resolve_external_input`, the single
    home for input-source semantics (it follows ``from:`` alias hops a
    raw ``source:`` walk would miss). In-tree paths are covered by the
    project mount/grant and skipped here.
    """
    from astra.helpers import load_yaml, resolve_analysis_tree

    from lightcone.engine.tree import (
        collect_tree_outputs,
        find_upstream_output,
        resolve_external_input,
    )

    if spec is None:
        spec = resolve_analysis_tree(load_yaml(project / "astra.yaml"), project)
    tree_outputs = collect_tree_outputs(spec)
    paths: list[Path] = []
    for to in tree_outputs:
        for inp_id in to.output_def.get("inputs") or []:
            if find_upstream_output(to, inp_id, tree_outputs) is not None:
                continue  # sibling output — mounted with the project
            source = resolve_external_input(to, inp_id, spec)
            if not source:
                continue
            p = Path(source)
            if not p.is_absolute():
                p = project / p
            if p.exists():
                paths.append(p.resolve())
    return tuple(dict.fromkeys(paths))
