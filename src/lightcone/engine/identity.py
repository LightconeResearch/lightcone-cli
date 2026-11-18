"""The environment a result ran under, and how far its identity reaches.

``env_version`` is the environment's identity: the lock's bytes, the
interpreter pin's bytes, the settings that decide *which artifacts*
``uv sync`` materializes from that lock, and — for a containerized
project — the system layer's identity document. It is deliberately
over-sensitive — raw lock bytes, so a comment reflow moves it — because
the alternative is a parse that silently disagrees with uv about what the
lock means.

A record names the environment its recipe ran in by an IRI, and
:func:`environment` is that IRI: ``env_version`` under lc's own ``urn:``
prefix. The name is lc's, so what it holds is lc's to say, and it is the
files of the record's commit that :func:`files` lists — which is what
makes it one lc can recreate. ASTRA records the environment and never
compares it, **and neither does the rebuild rule** — that is the whole
shape of the model. An environment moves for reasons that have nothing
to do with a given result — one added dependency rewrites the lock for
the whole project — and a research result costs hours to remake and is
often already looked at. So an environment edit does not stale anything;
it makes a result *behind*, which is a fact the report states and a
rebuild the caller can ask for. Over-sensitivity is affordable exactly
because it spends no compute.

The git commit is not an input. It is tree-wide, so hashing it would
put every result behind on a README edit; a record names it instead.

The hash is length-framed. Concatenating fields raw lets a boundary shift
between them produce the same digest from different inputs, and nothing
about a content hash is worth having if it can be shifted.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lightcone.engine import image
from lightcone.engine.project import ProjectError

#: The closed, audited list of uv settings that change *which artifacts* a
#: sync materializes from an unchanged lock. Closed on purpose: anything
#: outside it is either already covered by the lock's bytes or does not
#: affect what ends up installed, and a set that grew by guesswork would
#: report every result in every project as behind each time it did.
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
    install-settings JSON ‖ canonical image identity document)``,
    length-framed. A direct-mode project hashes the literal ``null`` for
    the image term, so this stays one formula rather than two. Read fresh
    on every call: this is also the mid-run gate's baseline, and a cached
    value would check nothing.

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
    _frame(h, "image", (image.identity_document(root) or "null").encode())
    return f"sha256:{h.hexdigest()}"


def files(root: Path) -> tuple[str, ...]:
    """Name the files :func:`env_version` reads, from the project root.

    A record names its environment by these files as its commit has them,
    so they are what has to be in that commit for the environment to be
    recreated from it.

    Args:
        root: The project root.

    Returns:
        The lock, the interpreter pin, ``pyproject.toml`` — which holds
        the image declaration and, without a ``uv.toml``, uv's settings —
        and the ``uv.toml`` that replaces them where there is one.
    """
    config = ("uv.toml",) if (root / "uv.toml").is_file() else ()
    return ("uv.lock", ".python-version", "pyproject.toml", *config)


def environment(root: Path) -> str:
    """Name the project's environment as a record does.

    Args:
        root: The project root.

    Returns:
        The IRI: :func:`env_version` under lc's ``urn:`` prefix.

    Raises:
        ProjectError: If ``uv.lock`` or ``.python-version`` is missing.
    """
    return f"urn:lightcone:env:{env_version(root)}"


def _frame(h: hashlib._Hash, label: str, data: bytes) -> None:
    """Feed one labelled, length-delimited field into *h*."""
    h.update(label.encode())
    h.update(b"\0")
    h.update(str(len(data)).encode("ascii"))
    h.update(b"\0")
    h.update(data)


def _install_settings(root: Path) -> str:
    """The audited uv settings, canonically, absent ones included.

    Every key is emitted whether or not the project sets it, so adding a
    setting whose value happens to be uv's default still moves
    ``env_version`` — a project that says what it means and a project that
    relies on a default are the same environment only until uv's default
    changes.

    Only the values matter, never which file supplied them: two projects
    that install the same artifacts are one environment however they spell
    it.
    """
    return json.dumps(
        {key: _uv_config(root).get(key) for key in _INSTALL_SETTINGS},
        sort_keys=True,
        separators=(",", ":"),
    )


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
    #: Machine-level uv config files setting audited install settings.
    #: Advisory: they steer the sync underneath the project's own
    #: settings, and ``env_version`` deliberately cannot see them.
    machine_config: tuple[str, ...]


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
    default = _uv_config(root).get("default-groups", ["dev"])
    non_default = set() if default == "all" else groups - set(default)

    return LockScan(
        refusals=tuple(sorted(refusals)),
        sdist_built=tuple(sorted(sdist_built)),
        non_default_groups=tuple(sorted(non_default)),
        machine_config=_machine_config(),
    )


def _machine_config_paths() -> tuple[Path, ...]:
    """uv's user- and system-level config files, per its documented rule.

    These levels can only ever be a ``uv.toml`` — never a
    ``pyproject.toml`` — so two known paths per platform make the probe
    complete rather than a heuristic.
    """
    if os.name == "nt":
        return tuple(
            Path(os.environ[var]) / "uv" / "uv.toml"
            for var in ("APPDATA", "PROGRAMDATA")
            if var in os.environ
        )
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    config_dirs = os.environ.get("XDG_CONFIG_DIRS") or "/etc/xdg"
    return (
        config_home / "uv" / "uv.toml",
        *(Path(d) / "uv" / "uv.toml" for d in config_dirs.split(":") if d),
        Path("/etc/uv/uv.toml"),
    )


def _machine_config() -> tuple[str, ...]:
    """Name the machine-level uv config files that steer install settings.

    uv merges user- and system-level configuration *underneath* the
    project's, and list settings concatenate across levels — a user-level
    ``no-binary-package`` adds to the project's — so the test is which
    keys a file sets, never whether the file exists. Deliberately never
    hashed: machine state in ``env_version`` would make one commit answer
    differently on two hosts. A file uv itself cannot parse is skipped —
    the sync fails loudly on it without our help.
    """
    findings = []
    for path in _machine_config_paths():
        try:
            data = tomllib.loads(path.read_text())
        except (OSError, tomllib.TOMLDecodeError):
            continue
        if keys := sorted(set(data) & set(_INSTALL_SETTINGS)):
            findings.append(f"{path} sets {', '.join(keys)}")
    return tuple(findings)


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


def _uv_config(root: Path) -> dict[str, Any]:
    """uv's settings for this project, read the way uv itself reads them.

    A ``uv.toml`` beside ``pyproject.toml`` **replaces** ``[tool.uv]``
    wholesale rather than merging with it, so a project carrying both has
    settings uv ignores — and hashing those would say two environments
    differ when uv installs the same thing in each. uv warns about the
    pair itself, and convergence already lifts its warnings.

    What this cannot reach is user- and system-level configuration, which
    uv *does* merge in underneath. That is machine state rather than
    project state: hashing it would make one commit answer differently on
    two hosts, reporting every result as behind on a colleague's clone.
    :func:`_machine_config` reports it instead.
    """
    config = root / "uv.toml"
    if not config.is_file():
        return _pyproject(root).get("tool", {}).get("uv", {}) or {}
    try:
        return tomllib.loads(config.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ProjectError(f"{config}: invalid TOML: {e}") from e


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
