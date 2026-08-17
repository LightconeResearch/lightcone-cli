"""The project environment model: mode, identity, and the lock scan.

The environment is ``pyproject.toml`` + ``uv.lock`` + ``.python-version``
— uv is the only substrate. Everything here is derived from those repo
files plus the closed ``[tool.lightcone]`` surface:

* **Mode** is derived, never configured: declaring
  ``[tool.lightcone.image]`` (or shipping ``Containerfile.extra``) *is*
  the escalation into containerized mode.
* **``env_version``** is the environment identity — one formula for
  both modes (direct mode hashes empty image fields). It sits inside
  every output's ``code_version``, so an environment edit stales
  exactly the outputs whose semantics it could change: all of them.
* **The lock scan** refuses what cannot be audited (path/editable
  dependencies other than the project's own package) and reports what
  weakens identity (registry sdists built locally).
"""
from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from lightcone.engine.image.declaration import (
    EMPTY_CANONICAL_JSON,
    ImageDeclaration,
    load_image_declaration,
)


class ProjectEnvironmentError(Exception):
    """A project-environment problem the user must fix (rendered as a
    clean CLI error, never a traceback)."""


class Mode(StrEnum):
    DIRECT = "direct"
    CONTAINERIZED = "containerized"


#: The closed, audited list of uv install-selection settings that flow
#: into ``env_version`` (v4's list, carried by spec §3): anything that
#: changes *which artifacts* ``uv sync`` materializes from the same
#: lock.
_INSTALL_SETTING_KEYS = (
    "default-groups",
    "no-binary",
    "no-binary-package",
    "no-build",
    "no-build-package",
    "config-settings",
    "no-build-isolation",
    "no-build-isolation-package",
)

#: Valid sub-tables of ``[tool.lightcone]`` — a closed surface, like the
#: image table itself.
_LIGHTCONE_KEYS = {"image", "sandbox"}
_SANDBOX_KEYS = {"writable-project"}


@dataclass(frozen=True)
class InstallSettings:
    """Normalized install-selection settings from ``[tool.uv]``."""

    values: tuple[tuple[str, str], ...]  # (key, canonical-JSON value) pairs

    @classmethod
    def from_tool_uv(cls, tool_uv: dict[str, Any]) -> InstallSettings:
        pairs = []
        for key in _INSTALL_SETTING_KEYS:
            value = tool_uv.get(key)
            pairs.append((key, json.dumps(value, sort_keys=True, separators=(",", ":"))))
        return cls(values=tuple(pairs))

    def canonical_json(self) -> str:
        return json.dumps(dict(self.values), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class EnvironmentSpec:
    """The loaded, validated project environment."""

    root: Path
    mode: Mode
    python_version: str
    packaged: bool  # [build-system] present in pyproject
    image: ImageDeclaration | None  # None ⇔ direct mode
    install_settings: InstallSettings
    env_version: str  # "sha256:<hex>"
    writable_project_outputs: frozenset[str]

    @property
    def venv(self) -> Path:
        return self.root / ".venv"


def load_environment(root: Path) -> EnvironmentSpec:
    """Single parse point for the project environment.

    Raises :class:`ProjectEnvironmentError` on missing environment
    files or banned states (authored root Containerfile; packaged
    project in containerized mode).
    """
    root = root.resolve()

    if (root / "Containerfile").is_file():
        raise ProjectEnvironmentError(
            f"{root}/Containerfile: v6 generates images from the lock — "
            "an authored root Containerfile is not consumed by anything "
            "and would mislead readers. Delete or rename it; declare "
            "system dependencies in [tool.lightcone.image] instead."
        )

    pyproject_path = root / "pyproject.toml"
    if not pyproject_path.is_file():
        raise ProjectEnvironmentError(
            f"{root}: no pyproject.toml — the environment is "
            "pyproject.toml + uv.lock + .python-version. Run `lc init` "
            "to scaffold it."
        )
    try:
        pyproject = tomllib.loads(pyproject_path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ProjectEnvironmentError(f"{pyproject_path}: invalid TOML: {e}") from e

    pv_path = root / ".python-version"
    if not pv_path.is_file():
        raise ProjectEnvironmentError(
            f"{root}: no .python-version — the exact interpreter pin is "
            "part of the environment identity. Run `lc init` to scaffold it."
        )
    python_version = pv_path.read_text().strip()

    if not (root / "uv.lock").is_file():
        raise ProjectEnvironmentError(
            f"{root}: no uv.lock — run `uv lock` (or `lc init`) to lock "
            "the environment."
        )

    tool_lightcone = pyproject.get("tool", {}).get("lightcone", {})
    if not isinstance(tool_lightcone, dict):
        raise ProjectEnvironmentError("[tool.lightcone] must be a table.")
    if unknown := set(tool_lightcone) - _LIGHTCONE_KEYS:
        raise ProjectEnvironmentError(
            f"[tool.lightcone]: unknown key(s) "
            f"{', '.join(sorted(repr(k) for k in unknown))}; valid: "
            f"{', '.join(sorted(_LIGHTCONE_KEYS))}."
        )

    image = load_image_declaration(root)
    mode = Mode.CONTAINERIZED if image is not None else Mode.DIRECT
    packaged = "build-system" in pyproject

    if packaged and mode is Mode.CONTAINERIZED:
        raise ProjectEnvironmentError(
            "containerized mode requires a virtual project (no "
            "[build-system] in pyproject.toml): the image is built "
            "--no-install-project — code never enters an image — so a "
            "packaged project's own import would fail inside its "
            "container. Restructure as a virtual project."
        )

    install_settings = InstallSettings.from_tool_uv(
        pyproject.get("tool", {}).get("uv", {}) or {}
    )

    env_version = compute_env_version(
        uv_lock_bytes=(root / "uv.lock").read_bytes(),
        python_version_bytes=pv_path.read_bytes(),
        install_settings=install_settings,
        image=image,
    )

    return EnvironmentSpec(
        root=root,
        mode=mode,
        python_version=python_version,
        packaged=packaged,
        image=image,
        install_settings=install_settings,
        env_version=env_version,
        writable_project_outputs=_writable_project_outputs(tool_lightcone),
    )


def _writable_project_outputs(tool_lightcone: dict[str, Any]) -> frozenset[str]:
    sandbox = tool_lightcone.get("sandbox", {})
    if not isinstance(sandbox, dict):
        raise ProjectEnvironmentError("[tool.lightcone.sandbox] must be a table.")
    if unknown := set(sandbox) - _SANDBOX_KEYS:
        raise ProjectEnvironmentError(
            f"[tool.lightcone.sandbox]: unknown key(s) "
            f"{', '.join(sorted(repr(k) for k in unknown))}; valid: "
            f"{', '.join(sorted(_SANDBOX_KEYS))}."
        )
    raw = sandbox.get("writable-project", [])
    if not isinstance(raw, list) or not all(isinstance(o, str) for o in raw):
        raise ProjectEnvironmentError(
            "[tool.lightcone.sandbox] writable-project must be a list of "
            "output ids."
        )
    return frozenset(raw)


def _frame(h: hashlib._Hash, label: str, data: bytes) -> None:
    """Length-framed update — prevents boundary-shifting collisions."""
    h.update(label.encode("utf-8"))
    h.update(b"\0")
    h.update(str(len(data)).encode("ascii"))
    h.update(b"\0")
    h.update(data)


def compute_env_version(
    *,
    uv_lock_bytes: bytes,
    python_version_bytes: bytes,
    install_settings: InstallSettings,
    image: ImageDeclaration | None,
) -> str:
    """The environment identity (spec §3) — one formula for both modes.

    ``sha256(uv.lock bytes ‖ .python-version bytes ‖ canonical
    install-settings JSON ‖ canonical image-declaration JSON ‖
    Containerfile.extra sha-or-null)``, length-framed. Direct mode
    hashes the empty image shape and a null extra.
    """
    h = hashlib.sha256()
    _frame(h, "uv.lock", uv_lock_bytes)
    _frame(h, "python-version", python_version_bytes)
    _frame(h, "install-settings", install_settings.canonical_json().encode("utf-8"))
    image_json = image.canonical_json() if image else EMPTY_CANONICAL_JSON
    _frame(h, "image", image_json.encode("utf-8"))
    extra = image.extra_sha256 if image and image.extra_sha256 else "null"
    _frame(h, "containerfile-extra", extra.encode("utf-8"))
    return f"sha256:{h.hexdigest()}"


# ---------------------------------------------------------------------------
# Lock scan
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LockScan:
    """What the lock says about auditability."""

    refusals: tuple[str, ...]  # path/directory/editable deps ≠ own package
    sdist_built: tuple[str, ...]  # registry packages with no wheel at all
    non_default_groups: tuple[str, ...]  # advisory: outside lc's guarantees


def scan_lock(root: Path) -> LockScan:
    """Scan ``uv.lock`` + ``pyproject.toml`` for identity hazards.

    * **Refusal**: path / directory / editable dependencies other than
      the project's own package — unauditable inputs (their bytes are
      not pinned by the lock).
    * **Report**: registry packages shipping no wheel (the sdist is
      built locally at sync time — identity covers the sdist, not the
      build).
    * **Advisory**: dependency groups beyond uv's default — installable
      states the identity does not cover.
    """
    lock_path = root / "uv.lock"
    try:
        lock = tomllib.loads(lock_path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ProjectEnvironmentError(f"{lock_path}: unreadable: {e}") from e

    pyproject = tomllib.loads((root / "pyproject.toml").read_text())
    own_name = pyproject.get("project", {}).get("name")

    refusals: list[str] = []
    sdist_built: list[str] = []
    for pkg in lock.get("package", []):
        name = pkg.get("name", "?")
        source = pkg.get("source", {}) or {}
        if any(k in source for k in ("path", "directory", "editable")):
            if name != own_name:
                refusals.append(
                    f"{name}: {next(k for k in ('path', 'directory', 'editable') if k in source)} "
                    "dependency — unauditable (bytes not pinned by the lock)"
                )
            continue
        if "virtual" in source:
            continue
        if "registry" in source and "sdist" in pkg and not pkg.get("wheels"):
            sdist_built.append(name)

    groups = set(pyproject.get("dependency-groups", {}) or {})
    tool_uv = pyproject.get("tool", {}).get("uv", {}) or {}
    default_groups = tool_uv.get("default-groups", ["dev"])
    if default_groups == "all":
        non_default: set[str] = set()
    else:
        non_default = groups - set(default_groups)

    return LockScan(
        refusals=tuple(sorted(refusals)),
        sdist_built=tuple(sorted(sdist_built)),
        non_default_groups=tuple(sorted(non_default)),
    )
