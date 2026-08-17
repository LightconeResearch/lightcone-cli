"""The image definition — a pure value object the generator renders.

Modal-style internal API: an :class:`ImageDefinition` is assembled from
the project's declaration plus engine constants, and everything
downstream (rendered Containerfile text, tag, build) is a pure function
of it. The layering is **fixed by the generator, never user-ordered**
(spec §2): base → contract checks → apt → pinned uv → interpreter →
locked sync → extra stage → final ENV contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lightcone.engine.image import constants
from lightcone.engine.image.declaration import BaseRef, ImageDeclaration
from lightcone.engine.image.errors import DeclarationError


@dataclass(frozen=True)
class UvDistribution:
    """The pinned uv binary source (engine constant)."""

    version: str
    image: str
    image_digest: str

    @property
    def copy_from_ref(self) -> str:
        return f"{self.image}@{self.image_digest}"


UV_DIST = UvDistribution(
    version=constants.UV_VERSION,
    image=constants.UV_IMAGE,
    image_digest=constants.UV_IMAGE_DIGEST,
)

DEFAULT_BASE = BaseRef(
    name=constants.DEFAULT_BASE_NAME, digest=constants.DEFAULT_BASE_DIGEST
)


@dataclass(frozen=True)
class ImageDefinition:
    """Everything that determines the rendered Containerfile."""

    base: BaseRef
    system_packages: tuple[str, ...]
    python_version: str  # exact patch, from .python-version
    uv: UvDistribution
    extra_stage: str | None  # Containerfile.extra content, verbatim
    env_version: str  # baked as a LABEL + /opt/lc/identity.json

    @classmethod
    def from_project(
        cls,
        project: Path,
        declaration: ImageDeclaration,
        *,
        env_version: str,
    ) -> ImageDefinition:
        pv_path = project / ".python-version"
        if not pv_path.is_file():
            raise DeclarationError(
                f"{project}: no .python-version file — the image build "
                "needs the exact interpreter pin. Run `lc init` to "
                "scaffold it."
            )
        python_version = pv_path.read_text().strip()
        if not python_version:
            raise DeclarationError(f"{pv_path}: empty .python-version file.")
        return cls(
            base=declaration.base or DEFAULT_BASE,
            system_packages=declaration.system_packages,
            python_version=python_version,
            uv=UV_DIST,
            extra_stage=declaration.extra,
            env_version=env_version,
        )
