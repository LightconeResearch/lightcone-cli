"""What a materialized output is identified by.

Two hashes, and they answer different questions on purpose.

``definition_version`` is what the spec says an output *is*: its rendered
recipe and the decisions it was made under. It is the rebuild trigger —
when it moves, the artifact on disk is no longer an instance of the thing
the spec describes, so keeping it would be mislabelling it.

``env_version`` is the environment's identity: the lock's bytes, the
interpreter pin's bytes, and the settings that decide *which artifacts*
``uv sync`` materializes from that lock. It is deliberately
over-sensitive — raw lock bytes, so a comment reflow moves it — because
the alternative is a parse that silently disagrees with uv about what the
lock means.

**``env_version`` is not part of ``definition_version``**, and that is the
whole shape of the model. An environment moves for reasons that have
nothing to do with a given output — one added dependency rewrites the lock
for the whole project — and a research artifact costs hours to remake and
is often already looked at. So an environment edit does not stale
anything; it makes an artifact *behind*, which is a fact the report states
and a rebuild the caller can ask for. Over-sensitivity is affordable
exactly because it no longer spends compute.

What makes that safe is that nothing is lost: the environment an output
was made under is recorded in its manifest, and the commit alongside it
reconstructs that environment from the lock of the day.

The git commit is not an input to either hash. It is tree-wide, so
hashing it would stale every output in the repository on a README edit.

Both hashes are length-framed. Concatenating fields raw lets a boundary
shift between them produce the same digest from different inputs — a
recipe ending in a character the decisions begin with, say — and nothing
about a content hash is worth having if it can be shifted.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lightcone.engine.project import ProjectError

#: The closed, audited list of uv settings that change *which artifacts* a
#: sync materializes from an unchanged lock. Closed on purpose: anything
#: outside it is either already covered by the lock's bytes or does not
#: affect what ends up installed, and a set that grew by guesswork would
#: report every output in every project as behind each time it did.
_INSTALL_SETTINGS = (
    "default-groups",
    "no-binary",
    "no-binary-package",
    "no-build",
    "no-build-package",
    "config-settings",
    "no-build-isolation",
    "no-build-isolation-package",
)


# =============================================================================
# The hashes
# =============================================================================


def env_version(root: Path) -> str:
    """Compute the environment identity of a project.

    ``sha256(uv.lock bytes ‖ .python-version bytes ‖ canonical
    install-settings JSON)``, length-framed. Read fresh on every call: this
    is also the mid-run gate's baseline, and a cached value would check
    nothing.

    Args:
        root: The project root.

    Returns:
        The digest, as ``sha256:<hex>``.

    Raises:
        ProjectError: If ``uv.lock`` or ``.python-version`` is missing.
    """
    lock = _required(root, "uv.lock", "run `uv lock` (or `lc init`) to lock the environment")
    pin = _required(
        root,
        ".python-version",
        "the exact interpreter pin is part of the environment's identity; "
        "run `lc init` to scaffold it",
    )

    h = hashlib.sha256()
    _frame(h, "uv.lock", lock.read_bytes())
    _frame(h, "python-version", pin.read_bytes())
    _frame(h, "install-settings", _install_settings(root).encode())
    return f"sha256:{h.hexdigest()}"


def definition_version(*, recipe: str, decisions: Mapping[str, str]) -> str:
    """Compute what the spec says one output is.

    ``sha256(recipe ‖ canonical decisions)``, length-framed. The
    environment is deliberately absent: see this module's docstring.

    Args:
        recipe: The rendered recipe command.
        decisions: The decisions this output declares, as id → option.

    Returns:
        The digest, as ``sha256:<hex>``.
    """
    h = hashlib.sha256()
    _frame(h, "recipe", recipe.encode())
    _frame(h, "decisions", _canonical(dict(decisions)).encode())
    return f"sha256:{h.hexdigest()}"


def _frame(h: hashlib._Hash, label: str, data: bytes) -> None:
    """Feed one labelled, length-delimited field into *h*."""
    h.update(label.encode())
    h.update(b"\0")
    h.update(str(len(data)).encode("ascii"))
    h.update(b"\0")
    h.update(data)


def _canonical(value: Any) -> str:
    """JSON with a single spelling per value — sorted keys, no padding."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _install_settings(root: Path) -> str:
    """The audited ``[tool.uv]`` settings, canonically, absent ones included.

    Every key is emitted whether or not the project sets it, so adding a
    setting whose value happens to be uv's default still moves
    ``env_version`` — a project that says what it means and a project that
    relies on a default are the same environment only until uv's default
    changes.
    """
    tool_uv = _pyproject(root).get("tool", {}).get("uv", {}) or {}
    return _canonical({key: tool_uv.get(key) for key in _INSTALL_SETTINGS})


# =============================================================================
# The lock scan
# =============================================================================


@dataclass(frozen=True)
class LockScan:
    """What the lock says about how far identity actually reaches."""

    #: Dependencies whose bytes the lock does not pin. A refusal.
    refusals: tuple[str, ...]
    #: Registry packages shipping no wheel, so the sdist is built at sync
    #: time. Reported: identity covers the sdist, not the build of it.
    sdist_built: tuple[str, ...]
    #: Groups outside uv's default set. Advisory: they are installable
    #: states `env_version` does not distinguish.
    non_default_groups: tuple[str, ...]


def scan_lock(root: Path) -> LockScan:
    """Scan the lock for dependencies that weaken an output's identity.

    A path, directory or editable dependency records *where* it was rather
    than what was in it, so two syncs of one lock can install different
    code while every hash agrees they are identical. That is the refusal.
    The project's own package is exempt: the repository records its bytes.

    Args:
        root: The project root.

    Returns:
        The refusals, the registry packages built from sdist, and the
        dependency groups outside uv's default set.

    Raises:
        ProjectError: If ``uv.lock`` is missing or is not valid TOML.
    """
    lock_path = _required(root, "uv.lock", "run `uv lock` (or `lc init`)")
    try:
        lock = tomllib.loads(lock_path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ProjectError(f"{lock_path}: invalid TOML: {e}") from e

    pyproject = _pyproject(root)
    own = _canonical_name(pyproject.get("project", {}).get("name") or "")

    refusals: list[str] = []
    sdist_built: list[str] = []
    for package in lock.get("package", []):
        name = package.get("name", "?")
        source = package.get("source", {}) or {}
        if kind := next((k for k in ("path", "directory", "editable") if k in source), None):
            if _canonical_name(name) != own:
                refusals.append(
                    f"{name}: {kind} dependency — the lock records where it was, "
                    "not what was in it, so two syncs can install different code"
                )
        elif "registry" in source and "sdist" in package and not package.get("wheels"):
            sdist_built.append(name)

    groups = set(pyproject.get("dependency-groups", {}) or {})
    default = (pyproject.get("tool", {}).get("uv", {}) or {}).get("default-groups", ["dev"])
    non_default = set() if default == "all" else groups - set(default)

    return LockScan(
        refusals=tuple(sorted(refusals)),
        sdist_built=tuple(sorted(sdist_built)),
        non_default_groups=tuple(sorted(non_default)),
    )


# =============================================================================
# Reading the project's files
# =============================================================================


def _canonical_name(name: str) -> str:
    """A distribution name in PEP 503 form.

    Both sides need it: uv writes the normalized name into ``uv.lock``,
    while ``pyproject.toml`` carries whatever the author wrote — and
    ``project_name()`` keeps ``_`` and ``.``. Comparing them raw makes a
    packaged project called ``my_project`` fail to recognise *itself*, and
    the lock scan then refuses the whole run over the project's own code.
    """
    return re.sub(r"[-_.]+", "-", name).lower()


def _required(root: Path, name: str, remedy: str) -> Path:
    """*root*/*name*, or a refusal naming what to do about its absence."""
    path = root / name
    if not path.is_file():
        raise ProjectError(f"{root}: no {name} — {remedy}.")
    return path


def _pyproject(root: Path) -> dict[str, Any]:
    path = _required(
        root,
        "pyproject.toml",
        "the environment is pyproject.toml + uv.lock + .python-version; run `lc init`",
    )
    try:
        return tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ProjectError(f"{path}: invalid TOML: {e}") from e
