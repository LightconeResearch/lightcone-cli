"""Data model for the sandbox layer."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class SandboxPolicy:
    """A mechanism-neutral, fully realpath'd enforcement policy.

    Built once per exec by :func:`~lightcone.engine.sandbox.policy.build_policy`;
    consumed by the Landlock ruleset builder and the Seatbelt profile
    generator alike.
    """

    read: tuple[Path, ...]
    write: tuple[Path, ...]
    #: Paths granted EXECUTE: the env's bin directory (a dir grant), the
    #: enumerated utility binaries, and the realpath'd ELF loaders.
    execute: tuple[Path, ...]
    tmp_home: Path  # fresh per-recipe HOME under the writable tmp scope
    env: dict[str, str]  # HOME/XDG/MPLCONFIGDIR/PYTHONPYCACHEPREFIX
    fs_scope: Literal["declared", "project-rw"]
    exec_allowlist_version: int
    #: Allowlist names that did not resolve on this host (surfaced by
    #: --sandbox-debug; never fatal).
    unresolved_utilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class SandboxCapability:
    """What enforcement this host can provide (probed per job)."""

    kind: Literal["landlock", "seatbelt", "none"]
    landlock_abi: int | None = None
    detail: str = ""


@dataclass(frozen=True)
class WrappedCommand:
    """A recipe exec, wrapped for enforcement."""

    argv: tuple[str, ...]
    pass_fds: tuple[int, ...]
    env: dict[str, str]  # additions/overrides for the subprocess env
    close_after_spawn: tuple[int, ...] = field(default=())
