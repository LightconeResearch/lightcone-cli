"""Error taxonomy for the image layer.

Every refusal in the container hatch is a distinct exception type with a
pointed, actionable message — a build or declaration problem must never
surface as a downstream mystery (the Modal lesson from the design
record: base flexibility only with a published contract, each violation
a refusal at the earliest possible moment).
"""
from __future__ import annotations


class ImageError(Exception):
    """Base for every image-layer failure."""


class DeclarationError(ImageError):
    """A static ``[tool.lightcone.image]`` / ``Containerfile.extra``
    problem, detectable at parse time: tag-only base, unknown key, bad
    apt package name, ``FROM`` inside the extra stage."""


class BaseContractError(ImageError):
    """The declared base image violates the base contract (musl-based,
    apt-less with system-packages declared, unsupported platform).
    Detected at build time — the contract-check layer inside the
    generated Containerfile fails with a distinct exit code that the
    builder maps back to this error."""


class AptPackageNotFoundError(ImageError):
    """apt could not locate a declared system package."""

    def __init__(self, package: str) -> None:
        self.package = package
        super().__init__(
            f"no apt package named `{package}` — search with "
            f"`apt-cache search {package}`"
        )


class ImageBuildError(ImageError):
    """The image build failed for a reason no more specific error
    covers; carries a bounded log tail."""


class PodmanUnavailableError(ImageError):
    """podman is not on PATH."""


class MachinePreflightError(ImageError):
    """macOS: the podman machine VM is missing, stopped, or does not
    share a required mount source."""


class ImageMissingError(ImageError):
    """The pinned image tag is absent from the local store. The message
    embeds the exact ``lc build`` command — ``lc run`` never builds."""


class DigestMismatchError(ImageError):
    """The tag resolves to a different image than the build record —
    a loud error, never a silent substitution."""
