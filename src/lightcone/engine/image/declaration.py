"""Parse and validate the container-hatch declaration surface.

The *entire* user-facing surface of the container hatch is one TOML
table in ``pyproject.toml`` plus an optional ``Containerfile.extra``::

    [tool.lightcone.image]
    base = "nvcr.io/nvidia/cuda:12.4.1-runtime-ubuntu22.04@sha256:9f2c…"
    system-packages = ["texlive-latex-base", "r-base-core"]

Its presence (or the extra file's) IS the escalation into containerized
mode — there is no separate switch. Every key is hashed into
``env_version`` and the image tag, which is why the surface is closed:
an unknown key is a refusal, never silently ignored.

Static refusals live here (parse time — they fire on every verb, long
before podman is involved). Contract properties that depend on the
base image's *contents* (musl, apt presence) are build-time checks in
the generated Containerfile.
"""
from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from lightcone.engine.image.errors import DeclarationError
from lightcone.engine.manifest import canonical_json

EXTRA_FILENAME = "Containerfile.extra"

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
#: Debian package-name grammar (policy §5.6.1): lowercase alphanumerics
#: plus ``+ - .``, at least two characters, starting alphanumeric.
_APT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]+$")
_VALID_KEYS = {"base", "system-packages"}


@dataclass(frozen=True)
class BaseRef:
    """A digest-pinned OCI reference."""

    name: str  # e.g. "nvcr.io/nvidia/cuda:12.4.1-runtime-ubuntu22.04"
    digest: str  # "sha256:<64 hex>"

    def __str__(self) -> str:
        return f"{self.name}@{self.digest}"

    @classmethod
    def parse(cls, ref: str) -> BaseRef:
        name, sep, digest = ref.partition("@")
        if not sep or not name:
            raise DeclarationError(
                f"[tool.lightcone.image] base = {ref!r}: pin the digest — "
                "`base` must be a digest-pinned OCI ref (`name@sha256:…`); "
                "a tag-only ref makes the image identity a function of "
                "registry state instead of the repo."
            )
        if not _DIGEST_RE.match(digest):
            raise DeclarationError(
                f"[tool.lightcone.image] base = {ref!r}: {digest!r} is not "
                "a valid digest (expected `sha256:` + 64 hex characters)."
            )
        return cls(name=name, digest=digest)


@dataclass(frozen=True)
class ImageDeclaration:
    """The parsed, validated container-hatch declaration."""

    base: BaseRef | None  # None ⇒ the engine's default base
    system_packages: tuple[str, ...]  # sorted, deduped
    extra: str | None  # Containerfile.extra content, verbatim
    extra_sha256: str | None

    def canonical_json(self) -> str:
        """Canonical serialization hashed into ``env_version``.

        Direct-mode projects hash the same shape with empty fields, so
        the ``env_version`` formula stays one formula (spec §3) —
        callers with no declaration use :data:`EMPTY_CANONICAL_JSON`.
        """
        return canonical_json(
            {
                "base": str(self.base) if self.base else None,
                "system-packages": list(self.system_packages),
            }
        )


#: What a direct-mode project hashes in the image-declaration slot.
EMPTY_CANONICAL_JSON = canonical_json({"base": None, "system-packages": []})


def load_image_declaration(
    project: Path, pyproject: dict[str, object] | None = None
) -> ImageDeclaration | None:
    """Parse the project's image declaration; ``None`` ⇔ direct mode.

    Containerized mode is derived, never configured: the presence of
    the ``[tool.lightcone.image]`` table (even empty) OR a
    ``Containerfile.extra`` file is the escalation. *pyproject* accepts
    an already-parsed ``pyproject.toml`` dict (the environment loader
    passes its own parse through).

    Raises :class:`DeclarationError` on any static violation.
    """
    table: dict[str, object] | None = None
    if pyproject is None:
        pyproject_path = project / "pyproject.toml"
        if pyproject_path.is_file():
            try:
                pyproject = tomllib.loads(pyproject_path.read_text())
            except tomllib.TOMLDecodeError as e:
                raise DeclarationError(
                    f"{pyproject_path}: invalid TOML: {e}"
                ) from e
    if pyproject is not None:
        tool = pyproject.get("tool", {})
        raw = tool.get("lightcone", {}).get("image") if isinstance(tool, dict) else None
        if raw is not None:
            if not isinstance(raw, dict):
                raise DeclarationError(
                    "[tool.lightcone.image] must be a table."
                )
            table = raw

    extra_path = project / EXTRA_FILENAME
    extra: str | None = None
    if extra_path.is_file():
        extra = extra_path.read_text()
        _validate_extra(extra)

    if table is None and extra is None:
        return None

    table = table or {}
    if unknown := set(table) - _VALID_KEYS:
        raise DeclarationError(
            f"[tool.lightcone.image]: unknown key(s) "
            f"{', '.join(sorted(repr(k) for k in unknown))}. The surface "
            f"is closed (every key is hashed into the environment "
            f"identity); valid keys: base, system-packages."
        )

    base: BaseRef | None = None
    if (base_raw := table.get("base")) is not None:
        if not isinstance(base_raw, str):
            raise DeclarationError("[tool.lightcone.image] base must be a string.")
        base = BaseRef.parse(base_raw)

    packages_raw = table.get("system-packages", [])
    if not isinstance(packages_raw, list) or not all(
        isinstance(p, str) for p in packages_raw
    ):
        raise DeclarationError(
            "[tool.lightcone.image] system-packages must be a list of "
            "apt package names."
        )
    for pkg in packages_raw:
        if not _APT_NAME_RE.match(pkg):
            raise DeclarationError(
                f"[tool.lightcone.image] system-packages: {pkg!r} is not a "
                "valid apt package name (lowercase alphanumerics plus "
                "'+', '-', '.'; unsure of the name? try: "
                f"`apt-cache search {pkg.lower()}`)."
            )
    system_packages = tuple(sorted(set(packages_raw)))

    return ImageDeclaration(
        base=base,
        system_packages=system_packages,
        extra=extra,
        extra_sha256=(
            hashlib.sha256(extra.encode("utf-8")).hexdigest()
            if extra is not None
            else None
        ),
    )


def _validate_extra(extra: str) -> None:
    for line in extra.splitlines():
        if line.strip().upper().startswith("FROM "):
            raise DeclarationError(
                f"{EXTRA_FILENAME}: contains a FROM line. The extra stage "
                "is generated `FROM` the derived environment image — "
                "write build instructions only (RUN, ENV, COPY …); the "
                "generator owns the stage structure."
            )
