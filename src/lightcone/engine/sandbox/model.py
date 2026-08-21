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
    #: MPLCONFIGDIR, PYTHONPYCACHEPREFIX, TMPDIR, PATH.
    env: dict[str, str] = field(default_factory=dict)

    def grants(self, path: Path, roots: tuple[Path, ...]) -> bool:
        """Test whether a path lies under any of a set of roots.

        The one containment predicate for the layer.

        Args:
            path: The path to test, resolved before comparison.
            roots: One of the policy's path tiers.

        Returns:
            True if *path* is under a root. ``is_relative_to`` is
            reflexive, so a root grants itself.
        """
        resolved = path.resolve()
        return any(resolved.is_relative_to(root) for root in roots)


@dataclass(frozen=True)
class Capability:
    """What enforcement this host can provide, as probed."""

    kind: Literal["landlock", "seatbelt", "podman", "docker", "podman-hpc", "none"]
    landlock_abi: int | None = None
    #: Why, when ``kind`` is ``none``. Reaches the user — a downgrade is
    #: never silent.
    detail: str = ""


@dataclass(frozen=True)
class Attestation:
    """The hermeticity record for one exec.

    Derived from the flags actually applied, never from the mechanism
    matrix's expectations. ``network`` is ``allowed`` everywhere today —
    lc controls the filesystem, not the network, and every mechanism says
    so identically. ``denied`` stays in the type for a mechanism that
    genuinely emits a denial flag; nothing may attest it without one.
    """

    mechanism: Literal["landlock", "seatbelt", "podman", "docker", "podman-hpc", "none"]
    fs: Literal["declared", "open"]
    network: Literal["allowed", "denied"] = "allowed"
    landlock_abi: int | None = None
    exec_allowlist_version: int | None = None
    #: Site container modules the runtime applied on top of the mount
    #: table, named by the gates that enabled them. A module widens the
    #: world by more than lc declared — NERSC's ``ENABLE_CVMFS`` binds
    #: the whole ``/cvmfs`` hierarchy, ``ENABLE_MPICH_SS`` adds
    #: ``--privileged`` and the host's network, pid and ipc namespaces —
    #: so ``fs: declared`` alone would overstate what was enforced.
    #: Naming them is what keeps the record honest while leaving the
    #: site's own GPU and MPI mechanisms working.
    site_modules: tuple[str, ...] = ()


class Backend(Protocol):
    """One sandbox mechanism, reduced to an argv rewrite.

    Implementations must keep :meth:`wrap` pure — no temporary files, no
    file descriptors, no global state. That is what lets the execution
    path below it stay mechanism-blind, and what makes a backend
    testable on a host that cannot run it.
    """

    @property
    def contains_prefix(self) -> bool:
        """Whether the wrap owns the whole command line, prefix included.

        A host mechanism restricts the command and leaves the ``uv run``
        hop outside as trusted host plumbing; a backend that is itself a
        *world* (a container) has no trusted host plumbing inside it —
        uv is part of what is being entered — so the seam hands it the
        prefix too, and the env overlay becomes the backend's to apply
        natively. Declared on every backend rather than defaulted at the
        call site, so a new mechanism must answer the question.
        """
        ...

    @property
    def capability(self) -> Capability:
        """What this backend probed on this host.

        A read-only property rather than a bare attribute, so a frozen
        dataclass satisfies the protocol — an immutable backend is the
        point, since :meth:`wrap` must be pure.
        """
        ...

    def wrap(self, policy: Policy, argv: Sequence[str]) -> list[str]:
        """Rewrite a command into one that sandboxes itself.

        Must be pure: no temporary files, no file descriptors, no global
        state. That is what makes a backend testable on a host that
        cannot run it.

        Args:
            policy: What the command may touch.
            argv: The command.

        Returns:
            The rewritten command.
        """
        ...

    def attest(self, policy: Policy) -> Attestation:
        """Report what :meth:`wrap`'s command will actually have enforced.

        Derived from the flags applied, never from what the mechanism
        matrix says should have happened.

        Args:
            policy: The policy being wrapped.

        Returns:
            The record written with every output.
        """
        ...
