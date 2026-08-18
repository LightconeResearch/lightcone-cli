"""The sandbox layer's data types, and the seam every mechanism meets.

Three types, and keeping them distinct is the whole design:

- :class:`Policy` — *what we will enforce*. Mechanism-free path sets.
- :class:`Capability` — *what this host can do*. The probe's answer.
- :class:`Attestation` — *what was actually enforced*. Recorded with
  every run, and derived from the flags actually applied — never a
  paraphrase of what should have happened.

:class:`Backend` is the seam. Every mechanism reduces to one pure
function, ``wrap(policy, argv) -> argv``: turn a command into a
*different command that sandboxes itself*. Seatbelt is natively that
shape (``sandbox-exec`` is a wrapper command); Landlock is not — it is a
self-restriction — which is what
:mod:`lightcone._sandbox_exec` exists to fix. Once both are argv
rewrites, everything above the seam is mechanism-blind and every backend
is testable on any OS with no privileges, by asserting on the argv it
emits.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

#: Bumped when the meaning of the exec allowlist changes. It is recorded
#: in the attestation, so a run stays interpretable after the list
#: grows — the allowlist is a maintained policy surface.
EXEC_ALLOWLIST_VERSION = 1


@dataclass(frozen=True)
class Policy:
    """What a sandboxed command may touch — one policy, every mechanism.

    Every path is realpath'd at construction: macOS resolves ``/tmp`` to
    ``/private/tmp`` and Landlock evaluates the resolved path, so an
    unresolved path is a rule that silently matches nothing.

    ``write`` implies read. ``execute`` does not imply either beyond the
    file itself — shared libraries are covered by the read baseline.
    """

    read: tuple[Path, ...]
    write: tuple[Path, ...]
    execute: tuple[Path, ...]
    #: The fresh per-run directory that becomes ``$HOME``. Lives under the
    #: write scope; the caller owns removing it.
    tmp_home: Path
    #: Environment the boundary overlays: HOME, the XDG trio,
    #: MPLCONFIGDIR, TMPDIR, PATH.
    env: dict[str, str] = field(default_factory=dict)

    def grants(self, path: Path, roots: tuple[Path, ...]) -> bool:
        """Whether *path* lies under any of *roots*.

        The one containment predicate for the layer. ``is_relative_to`` is
        reflexive, so a root grants itself — spelling it `p == r or
        p.is_relative_to(r)` is not just redundant, it teaches the next
        reader that the stdlib does not do the obvious thing.
        """
        resolved = path.resolve()
        return any(resolved.is_relative_to(root) for root in roots)


@dataclass(frozen=True)
class Capability:
    """What enforcement this host can provide, as probed."""

    kind: Literal["landlock", "seatbelt", "none"]
    landlock_abi: int | None = None
    #: Why, when ``kind`` is ``none``. Reaches the user — a downgrade is
    #: never silent.
    detail: str = ""


@dataclass(frozen=True)
class Attestation:
    """The hermeticity record for one exec.

    Derived from the flags actually applied, never from the mechanism
    matrix's expectations. ``network`` is ``allowed`` on every path here
    by recorded decision: lc applies no network restriction, and saying
    so is the honest value.
    """

    mechanism: Literal["landlock", "seatbelt", "none"]
    fs: Literal["declared", "open"]
    network: Literal["allowed"] = "allowed"
    landlock_abi: int | None = None
    exec_allowlist_version: int | None = None


class Backend(Protocol):
    """One sandbox mechanism, reduced to an argv rewrite.

    Implementations must keep :meth:`wrap` pure — no temporary files, no
    file descriptors, no global state. That is what lets the execution
    path below it stay mechanism-blind, and what makes a backend
    testable on a host that cannot run it.
    """

    @property
    def capability(self) -> Capability:
        """What this backend probed on this host.

        A read-only property rather than a bare attribute, so a frozen
        dataclass satisfies the protocol — an immutable backend is the
        point, since ``wrap`` must be pure.
        """
        ...

    def wrap(self, policy: Policy, argv: Sequence[str]) -> list[str]:
        """*argv*, rewritten into a command that sandboxes itself."""
        ...

    def attest(self, policy: Policy) -> Attestation:
        """What :meth:`wrap`'s command will actually have enforced."""
        ...
