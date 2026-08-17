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
from lightcone.engine.image.render import RenderedContainerfile, render

__all__ = [
    "EMPTY_CANONICAL_JSON",
    "BaseContractError",
    "BaseRef",
    "DeclarationError",
    "DigestMismatchError",
    "EnvInputs",
    "ImageBuildError",
    "ImageDeclaration",
    "ImageDefinition",
    "ImageError",
    "ImageMissingError",
    "MachinePreflightError",
    "PodmanUnavailableError",
    "RenderedContainerfile",
    "compute_tag",
    "load_image_declaration",
    "render",
]
