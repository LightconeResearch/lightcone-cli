"""The system layer: what a containerized project declares, and its identity.

A project escalates to containerized mode by declaring a
``[tool.lightcone.image]`` table in ``pyproject.toml`` — the table's
presence *is* the escalation, and it is the whole user-facing surface.
Nobody ever writes or sees a Containerfile: the render exists only inside
a transient build context, and everything about the image is derived from
the declaration plus this module's constants.

The surface is deliberately shaped like Modal's image builders, as TOML:
``base`` (``from_registry``), ``apt-install`` (``apt_install``),
``run-commands`` (``run_commands``) and ``env`` (``env``). There is no
``pip_install`` equivalent on purpose — the Python environment is the
lock's business, never the image's. ``run-commands`` is the bounded
escape for anything apt cannot say.

Two derived identities, for two different questions. The **identity
document** is canonical JSON of the resolved declaration plus the
generator's pinned inputs; it feeds ``env_version``, so a system-layer
change puts outputs behind. The **tag** additionally hashes the rendered
Containerfile text, so a generator change rebuilds the image even when
the document is unchanged. Code is an input to neither — the image
carries no project files, so a code edit can never trigger a build.

This module is pure: no subprocess, and no filesystem beyond reading
``pyproject.toml`` and ``.python-version``.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lightcone.engine.project import ProjectError

#: The default base image. Digest-pinned to the multi-arch manifest list,
#: so the reference is one string on every architecture and the image
#: stays a pure function of the repository plus the engine. An engine
#: release that bumps this moves both the tag and ``env_version`` for
#: containerized projects — the system layer genuinely changed.
DEFAULT_BASE = (
    "docker.io/library/debian:bookworm-slim"
    "@sha256:abd67ffcfa541b485a3dff59865ab629aa048a6c613e639d36e7456b0b229241"
)

#: Where the pinned uv binary is copied from. Manifest-list digest, so the
#: text is architecture-independent while each build gets its own arch.
UV_IMAGE = (
    "ghcr.io/astral-sh/uv:0.12.5"
    "@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1"
)

#: Where the pinned interpreter is installed inside the image.
PYTHON_INSTALL_DIR = "/opt/python"

#: The closed key set. Closed because every key is hashed into identity —
#: a key that did nothing would still move ``env_version``, and one that
#: did something unhashed would be an identity hole.
_KEYS = ("base", "apt-install", "run-commands", "env")


@dataclass(frozen=True)
class Declaration:
    """A parsed ``[tool.lightcone.image]`` table, defaults resolved."""

    #: The digest-pinned base reference — declared, or :data:`DEFAULT_BASE`.
    base: str
    apt_install: tuple[str, ...]
    run_commands: tuple[str, ...]
    env: tuple[tuple[str, str], ...]


def declaration(root: Path) -> Declaration | None:
    """Read the project's system-layer declaration.

    Args:
        root: The project root.

    Returns:
        The parsed declaration, or ``None`` for a direct-mode project.

    Raises:
        ProjectError: If the table carries an unknown key, a wrong type,
            or a ``base`` that is not digest-pinned. Raised at parse time
            so the refusal fires on every verb that reads identity, not
            just at build time.
    """
    table = _table(root)
    if table is None:
        return None
    if unknown := sorted(set(table) - set(_KEYS)):
        raise ProjectError(
            f"[tool.lightcone.image] has no key `{unknown[0]}` — the surface is "
            f"{', '.join(f'`{k}`' for k in _KEYS)}, and every key is part of the "
            "environment's identity, so nothing unrecognised can be carried along."
        )
    base = table.get("base", DEFAULT_BASE)
    if not isinstance(base, str) or not base:
        raise ProjectError("[tool.lightcone.image] `base` must be an image reference string.")
    if "@sha256:" not in base:
        raise ProjectError(
            f"[tool.lightcone.image] `base` is not digest-pinned: `{base}`. A tag can "
            "move under the project, so the image would stop being a pure function of "
            "the repository. Pin it: `base = \"<ref>@sha256:<digest>\"` "
            "(find the digest with `podman image inspect` after a pull)."
        )
    if re.search(r"\s", base):
        raise ProjectError(f"[tool.lightcone.image] `base` is not an image reference: `{base}`.")
    return Declaration(
        base=base,
        apt_install=tuple(sorted(_strings(table, "apt-install"))),
        run_commands=tuple(_strings(table, "run-commands")),
        env=tuple(sorted(_env(table).items())),
    )


def identity_document(root: Path) -> str | None:
    """Build the canonical JSON that is the image's identity.

    The resolved declaration plus the generator's pinned inputs. Every
    key is emitted whether or not the project set it — the
    install-settings discipline: a project relying on a default and one
    spelling it out are the same environment only until the default
    changes. The interpreter pin is deliberately absent: its raw bytes
    are already a frame of ``env_version``, and the rendered
    Containerfile carries it into the tag.

    Args:
        root: The project root.

    Returns:
        The document, or ``None`` for a direct-mode project.
    """
    declared = declaration(root)
    return None if declared is None else _document(declared)


def _document(declared: Declaration) -> str:
    """*declared* as its canonical JSON."""
    return json.dumps(
        {
            "apt": list(declared.apt_install),
            "base": declared.base,
            "env": dict(declared.env),
            "run": list(declared.run_commands),
            "uv": UV_IMAGE,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def containerfile(root: Path) -> str:
    """Render the Containerfile for this project's system layer.

    Generated and transient — it exists only inside a build context and
    is never written into the project. The layering is fixed here, never
    user-ordered: base, contract checks, apt, the pinned uv, the pinned
    interpreter, then the declared ``env`` and ``run-commands``. The
    contract checks turn a base that cannot work into a pointed refusal
    (via their reserved exit codes) instead of a downstream mystery.

    Args:
        root: The project root, which must be containerized.

    Returns:
        The Containerfile text.

    Raises:
        ProjectError: If the project is direct-mode, the declaration is
            invalid, or ``.python-version`` is missing.
    """
    declared = declaration(root)
    if declared is None:
        raise ProjectError(f"{root} declares no [tool.lightcone.image] — nothing to build.")
    return _render(root, declared)


def _render(root: Path, declared: Declaration) -> str:
    """The Containerfile for *declared* — one parse, however it is reached."""
    pin = root / ".python-version"
    if not pin.is_file():
        raise ProjectError(
            f"{root}: no .python-version — the image bakes the exact interpreter, "
            "so the pin is an input to it; run `lc init` to scaffold one."
        )
    version = pin.read_text().strip()
    # One version token: the pin splices into the install layer's RUN
    # line, and a multi-line or annotated file would splice instructions.
    if not re.match(r"^[A-Za-z0-9.+@-]+$", version):
        raise ProjectError(
            f"{root}/.python-version: `{version!r}` is not a single interpreter "
            "version — the image bakes exactly one; run `lc init` to repin."
        )

    lines = [
        f"FROM {declared.base}",
        # The contract checks, each a reserved exit code the builder maps
        # to a refusal naming the base: 43 musl, 44 no bash, 45 no apt.
        "RUN if ldd --version 2>&1 | grep -qi musl; then exit 43; fi",
        "RUN command -v bash >/dev/null || exit 44",
    ]
    if declared.apt_install:
        lines += [
            "RUN command -v apt-get >/dev/null || exit 45",
            "RUN apt-get update && apt-get install -y --no-install-recommends "
            + " ".join(declared.apt_install)
            + " && rm -rf /var/lib/apt/lists/*",
        ]
    lines += [
        f"COPY --from={UV_IMAGE} /uv /usr/local/bin/uv",
        # The chmod rides in the same layer as the install: rootless uid
        # mapping runs as an arbitrary user, so everything lc bakes must
        # be world-readable — and a chmod layer of its own would
        # copy-on-write every byte it touches, doubling the interpreter
        # in every archive.
        f"RUN UV_PYTHON_INSTALL_DIR={PYTHON_INSTALL_DIR} uv python install "
        + version
        + " && chmod -R a+rX /opt",
    ]
    lines += [f"ENV {key}={_quoted(value)}" for key, value in declared.env]
    lines += [f"RUN {command}" for command in declared.run_commands]
    if declared.run_commands:
        # Only when the user's own layers exist: whatever they put in
        # /opt needs the same readability, at the price of re-copying it.
        lines.append("RUN chmod -R a+rX /opt")
    lines += [
        # Set after `uv python install`, which needs the download the
        # final environment then forbids. `never` makes a missing
        # interpreter a loud error at run time instead of a silent fetch.
        f"ENV UV_PYTHON_INSTALL_DIR={PYTHON_INSTALL_DIR} UV_PYTHON_DOWNLOADS=never",
        f"LABEL io.lightcone.image={_quoted(_document(declared))}",
    ]
    return "\n".join(lines) + "\n"


def tag(root: Path) -> str:
    """Derive the image tag from everything that goes into the image.

    The rendered Containerfile *and* the identity document, framed as a
    JSON list so a boundary between them cannot shift. The render is an
    input so a generator change rebuilds; the document alone is what
    ``env_version`` sees.

    Args:
        root: The project root, which must be containerized.

    Returns:
        ``lc-env-<16 hex>``.
    """
    declared = declaration(root)
    if declared is None:
        raise ProjectError(f"{root} declares no [tool.lightcone.image] — nothing to tag.")
    framed = json.dumps([_render(root, declared), _document(declared)])
    return "lc-env-" + hashlib.sha256(framed.encode()).hexdigest()[:16]


def archive_path(root: Path, image_tag: str) -> Path:
    """Locate the committed image archive for *image_tag*.

    The one spelling of where an image lives in the dataset — the same
    layout ``datalad containers-add`` uses, so the archive is versioned
    project state that travels through the annex.

    Args:
        root: The project root.
        image_tag: The image tag, from :func:`tag`.

    Returns:
        ``<root>/.datalad/environments/<tag>/image``.
    """
    return root / ".datalad" / "environments" / image_tag / "image"


def _table(root: Path) -> dict[str, Any] | None:
    """The raw ``[tool.lightcone.image]`` table, or ``None`` if absent.

    Read from ``pyproject.toml`` only — never through the uv-config
    reader, whose "a ``uv.toml`` replaces ``[tool.uv]``" rule is about
    uv's own settings and must not reach this table.
    """
    path = root / "pyproject.toml"
    if not path.is_file():
        return None
    try:
        parsed = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ProjectError(f"{path}: invalid TOML: {e}") from e
    table = parsed.get("tool", {}).get("lightcone", {}).get("image")
    if table is None:
        return None
    if not isinstance(table, dict):
        raise ProjectError("[tool.lightcone.image] must be a table.")
    return table


#: What an apt package name may contain (Debian source/package charset).
#: Anything else is joined verbatim into a shell line by the apt layer,
#: so the closed charset is what keeps `apt-install` a list of *names*
#: rather than a second `run-commands`.
_APT_NAME = re.compile(r"^[a-z0-9][a-z0-9.+-]*$")

#: A shell-safe environment variable name. Anything else hits Docker's
#: legacy `ENV key value` parse and silently defines the wrong variable.
_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _strings(table: dict[str, Any], key: str) -> list[str]:
    """A list-of-strings key, defaulting empty. No control characters:
    every value here is interpolated into a Containerfile line, and a
    newline would splice an instruction of its own into an
    identity-hashed surface."""
    value = table.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ProjectError(f"[tool.lightcone.image] `{key}` must be a list of strings.")
    for entry in value:
        _plain(key, entry)
    if key == "apt-install":
        for name in value:
            if not _APT_NAME.match(name):
                raise ProjectError(
                    f"[tool.lightcone.image] apt-install: `{name}` is not an apt "
                    "package name — names are lowercase letters, digits, `.`, `+` "
                    "and `-`. A command belongs in `run-commands`."
                )
    return value


def _env(table: dict[str, Any]) -> dict[str, str]:
    """The ``env`` key, defaulting empty. Keys must be shell-safe names
    and values control-character-free, for the reason `_strings` gives."""
    value = table.get("env", {})
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise ProjectError("[tool.lightcone.image] `env` must be a table of strings.")
    for key, entry in value.items():
        if not _ENV_KEY.match(key):
            raise ProjectError(
                f"[tool.lightcone.image] env: `{key}` is not an environment "
                "variable name (letters, digits and `_`, not starting with a digit)."
            )
        _plain("env", entry)
    return value


def _plain(key: str, value: str) -> None:
    """Refuse control characters in a value bound for a Containerfile line."""
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        raise ProjectError(
            f"[tool.lightcone.image] `{key}` values cannot contain control "
            "characters — each renders into a single Containerfile line."
        )


def _quoted(value: str) -> str:
    """Quote a value for a Containerfile ``ENV``/``LABEL`` line.

    ``$`` is escaped along with the quoting characters, because these
    lines undergo build-time variable expansion — measured: an unescaped
    ``cost$5`` bakes as ``cost``, and a ``$`` inside the identity LABEL
    silently corrupts the document the image carries. Declared ``env``
    values are therefore *literals*, never expansions.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
    return f'"{escaped}"'
