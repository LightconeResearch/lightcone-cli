"""Content-addressed container images for containerized-mode projects.

The internal API is Modal-inspired: a typed, immutable
:class:`~lightcone.engine.image.definition.ImageDefinition` assembled
from the one-TOML-table user surface, rendered deterministically to
Containerfile text, with identity a pure function of the definition and
pluggable builder/runtime backends (podman today).

This module is the only import surface for the CLI, launcher, and
status layers.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from lightcone.engine.image.declaration import (
    EMPTY_CANONICAL_JSON,
    BaseRef,
    ImageDeclaration,
    load_image_declaration,
)
from lightcone.engine.image.definition import ImageDefinition
from lightcone.engine.image.errors import (
    BaseContractError,
    DeclarationError,
    DigestMismatchError,
    ImageBuildError,
    ImageError,
    ImageMissingError,
    MachinePreflightError,
    PodmanUnavailableError,
)
from lightcone.engine.image.identity import EnvInputs, compute_tag
from lightcone.engine.image.record import (
    BuildRecord,
    read_record,
    snapshot_sha256,
    write_record,
)
from lightcone.engine.image.render import RenderedContainerfile, render

if TYPE_CHECKING:
    from lightcone.engine.environment import EnvironmentSpec
    from lightcone.engine.image.builder import Builder


def _project_tag(project: Path, env: EnvironmentSpec) -> str:
    if env.image is None:
        raise DeclarationError(
            "direct-mode project has no image — declare "
            "[tool.lightcone.image] to containerize."
        )
    defn = ImageDefinition.from_project(project, env.image, env_version=env.env_version)
    return compute_tag(render(defn), EnvInputs.read(project))


def ensure_image(
    project: Path,
    env: EnvironmentSpec,
    *,
    force: bool = False,
    builder: Builder | None = None,
    on_progress: Callable[[str], None] = lambda _: None,
) -> BuildRecord:
    """Declaration → definition → render → tag → (build if absent) →
    record. Idempotent: a tag hit is a no-op (spec §3)."""
    from lightcone.engine.image.builder import BuildContext
    from lightcone.engine.image.builder_podman import PodmanBuilder

    assert env.image is not None
    defn = ImageDefinition.from_project(project, env.image, env_version=env.env_version)
    rendered = render(defn)
    tag = compute_tag(rendered, EnvInputs.read(project))

    b = builder or PodmanBuilder()
    existing = read_record(project)
    if not force and existing and existing.tag == tag and b.exists(tag):
        return existing

    on_progress(
        f"building {tag} — first run after an environment change; ~minutes"
    )
    context = BuildContext.from_project(project, rendered.text)
    result = b.build(context, tag=tag)
    record = BuildRecord(
        tag=result.tag,
        image_id=result.image_id,
        digest=result.digest,
        platform=result.platform,
        env_version=env.env_version,
        lc_version=_lc_version(),
        base=str(defn.base),
        built_at=datetime.now(UTC).isoformat(timespec="seconds"),
        dpkg_snapshot_sha256=snapshot_sha256(result.dpkg_snapshot_text),
    )
    write_record(project, record, result.dpkg_snapshot_text)
    return record


def resolve_pinned(
    project: Path,
    env: EnvironmentSpec,
    *,
    builder: Builder | None = None,
) -> BuildRecord:
    """Resolve tag → build record for execution, verifying the local
    store still holds the recorded image. ``lc run`` never builds — a
    missing image errors with the exact command; a tag that resolves
    differently than the record is a loud error, never a silent
    substitution."""
    from lightcone.engine.image.builder_podman import PodmanBuilder

    tag = _project_tag(project, env)
    record = read_record(project)
    b = builder or PodmanBuilder()
    if record is None or record.tag != tag or not b.exists(tag):
        raise ImageMissingError(
            f"the environment image {tag} is not built — run: lc build"
        )
    return record


@dataclass(frozen=True)
class ImageStatus:
    tag: str
    built: bool
    image_id: str | None


def image_status(
    project: Path,
    env: EnvironmentSpec,
    *,
    builder: Builder | None = None,
) -> ImageStatus:
    """The ``lc status`` header's image line — offline and local-only
    (reads the build record and the local image store, never the
    network)."""
    tag = _project_tag(project, env)
    record = read_record(project)
    built = False
    if record is not None and record.tag == tag:
        try:
            from lightcone.engine.image.builder_podman import PodmanBuilder

            b = builder or PodmanBuilder()
            built = b.exists(tag)
        except PodmanUnavailableError:
            built = False
    return ImageStatus(
        tag=tag,
        built=built,
        image_id=record.image_id if built and record else None,
    )


def _lc_version() -> str:
    try:
        from importlib.metadata import version

        return version("lightcone-cli")
    except Exception:
        return "unknown"


__all__ = [
    "EMPTY_CANONICAL_JSON",
    "BaseContractError",
    "BaseRef",
    "BuildRecord",
    "DeclarationError",
    "DigestMismatchError",
    "EnvInputs",
    "ImageBuildError",
    "ImageDeclaration",
    "ImageDefinition",
    "ImageError",
    "ImageMissingError",
    "ImageStatus",
    "MachinePreflightError",
    "PodmanUnavailableError",
    "RenderedContainerfile",
    "compute_tag",
    "ensure_image",
    "image_status",
    "load_image_declaration",
    "read_record",
    "render",
    "resolve_pinned",
    "write_record",
]
