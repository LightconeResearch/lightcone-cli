"""Builder protocol + the three-file build context.

:class:`BuildContext` is the structural guarantee behind G5: there is
no code path that can put project code into an image — the context is
*exactly* the rendered Containerfile, ``pyproject.toml``, and
``uv.lock``, staged world-readable into a fresh directory.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from lightcone.engine.image.errors import DeclarationError


@dataclass(frozen=True)
class BuildContext:
    containerfile_text: str
    pyproject: Path
    uv_lock: Path

    @classmethod
    def from_project(cls, project: Path, containerfile_text: str) -> BuildContext:
        pyproject = project / "pyproject.toml"
        uv_lock = project / "uv.lock"
        for p in (pyproject, uv_lock):
            if not p.is_file():
                raise DeclarationError(f"{p} is missing — run `uv lock` first.")
        return cls(
            containerfile_text=containerfile_text,
            pyproject=pyproject,
            uv_lock=uv_lock,
        )

    def stage(self, into: Path) -> Path:
        """Write the three files into *into*; returns the Containerfile
        path. World-readable: the build may run in a user namespace with
        a different effective uid."""
        into.mkdir(parents=True, exist_ok=True)
        containerfile = into / "Containerfile"
        containerfile.write_text(self.containerfile_text)
        (into / "pyproject.toml").write_bytes(self.pyproject.read_bytes())
        (into / "uv.lock").write_bytes(self.uv_lock.read_bytes())
        for p in into.iterdir():
            p.chmod(0o644)
        return containerfile


@dataclass(frozen=True)
class BuildResult:
    tag: str
    image_id: str
    digest: str | None
    platform: str
    dpkg_snapshot_text: str


class Builder(Protocol):
    """A backend that can materialize a rendered image (podman today;
    remote builders return behind this same protocol)."""

    def exists(self, tag: str) -> bool: ...

    def build(self, context: BuildContext, *, tag: str) -> BuildResult: ...
