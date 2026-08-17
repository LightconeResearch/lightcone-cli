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

from lightcone.engine import lc_version
from lightcone.engine.image import constants
from lightcone.engine.image.definition import ImageDefinition
from lightcone.engine.image.errors import (
    DeclarationError,
    ImageError,
    ImageMissingError,
    PodmanUnavailableError,
)
from lightcone.engine.image.identity import EnvInputs, compute_tag
from lightcone.engine.image.record import (
    BuildRecord,
    read_record,
    snapshot_sha256,
    write_record,
)
from lightcone.engine.image.render import render

if TYPE_CHECKING:
    from lightcone.engine.environment import EnvironmentSpec
    from lightcone.engine.image.builder import Builder


def _definition(env: EnvironmentSpec) -> ImageDefinition:
    if env.image is None:
        raise DeclarationError(
            "direct-mode project has no image — declare "
            "[tool.lightcone.image] to containerize."
        )
    return ImageDefinition.from_declaration(
        env.image,
        env_version=env.env_version,
        python_version=env.python_version,
    )


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

    defn = _definition(env)
    rendered = render(defn)
    inputs = EnvInputs.read(project)
    tag = compute_tag(rendered, inputs)

    b = builder or PodmanBuilder()
    existing = read_record(project)
    if not force and existing and existing.tag == tag and b.exists(tag):
        return existing

    on_progress(
        f"building {tag} — first run after an environment change; ~minutes"
    )
    context = BuildContext(containerfile_text=rendered.text, inputs=inputs)
    result = b.build(context, tag=tag)
    record = BuildRecord(
        tag=result.tag,
        image_id=result.image_id,
        digest=result.digest,
        platform=result.platform,
        env_version=env.env_version,
        lc_version=lc_version(),
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
    """Resolve the current environment to its build record, verifying
    the local store still holds the recorded image *id* (execution pins
    by id, so a retagged store can never substitute — the missing-image
    error is the only failure mode). ``lc run`` never builds — the
    message embeds the exact command."""
    from lightcone.engine.image.builder_podman import PodmanBuilder

    tag = compute_tag(render(_definition(env)), EnvInputs.read(project))
    record = read_record(project)
    b = builder or PodmanBuilder()
    if record is None or record.tag != tag or not b.exists(record.image_id):
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
    tag = compute_tag(render(_definition(env)), EnvInputs.read(project))
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


__all__ = [
    "BuildRecord",
    "ImageError",
    "ImageMissingError",
    "ImageStatus",
    "PodmanUnavailableError",
    "constants",
    "ensure_image",
    "image_status",
    "read_record",
    "resolve_pinned",
]
