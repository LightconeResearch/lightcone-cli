"""The macOS backend: a generated Seatbelt profile.

``sandbox-exec`` is already the shape the seam wants — a wrapper command
that confines everything it execs — so there is no shim on this side.
The work is generating the SBPL, which is ordinary string building and
therefore testable (and golden-tested) on any OS.

**Most of the profile is not ours.** ``profiles/base.sbpl`` and
``profiles/platform-defaults.sbpl`` are adapted from the codex CLI
(itself derived from Chrome's macOS sandbox policy), because the macOS
read baseline is not something to derive from first principles — it is a
list of things that break, discovered one production failure at a time.
It carries entries no one would guess: ``/dev/dtracehelper``, the
``/dev/fd`` and pty regexes, firmlink-parent traversal under
``/System/Volumes/Data``, the ``opendirectoryd.libinfo`` lookup without
which ``getpwuid()`` raises ``KeyError``, ``cfprefsd``, and
``/opt/homebrew/lib``. They are kept near-verbatim so they can be diffed
against upstream; the single local delta is marked in the file.

A third fragment, ``profiles/network.sbpl``, is the mach half of *not*
controlling the network: lc restricts none, and on macOS saying so takes
more than opening the socket families.

What *is* ours is the three-tier policy on top: the project and declared
inputs readable, the private scope writable, and the environment plus the
utility allowlist executable. Two rules, both learned from shipped
implementations:

- **Paths never go into the profile text.** They are referenced as
  ``(param "READ_0")`` and supplied as ``-DREAD_0=<path>`` on argv, so
  no path can ever be quoted wrong or close a form early.
- **Realpath everything** before emitting, which :class:`Policy`
  guarantees: ``/tmp`` is a symlink to ``/private/tmp`` on macOS, and a
  rule naming the symlink matches nothing at all.
"""

from __future__ import annotations

import functools
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from importlib import resources

from lightcone.engine.sandbox.model import (
    EXEC_ALLOWLIST_VERSION,
    Attestation,
    Capability,
    Policy,
)

SANDBOX_EXEC = "/usr/bin/sandbox-exec"

#: The adapted upstream fragments, in the order they are concatenated.
BASE_PROFILE = "base.sbpl"
NETWORK = "network.sbpl"
PLATFORM_DEFAULTS = "platform-defaults.sbpl"


@functools.cache
def read_profile(name: str) -> str:
    """The raw text of an SBPL fragment, cached — it is immutable
    package data read on every ``wrap``."""
    if name not in (BASE_PROFILE, NETWORK, PLATFORM_DEFAULTS):
        raise KeyError(f"unknown profile fragment: {name!r}")
    return (resources.files(__package__) / "profiles" / name).read_text(encoding="utf-8")


@functools.cache
def capability() -> Capability:
    """Whether ``sandbox-exec`` is present and usable on this host.

    Seatbelt has been "deprecated" since 2012 and has never been given a
    removal date, but the profile dialect does drift across releases —
    so this runs a live canary rather than trusting the file's presence.

    Cached like its Landlock twin: the canary is a subprocess, and the
    answer cannot change inside one process.
    """
    if os.name != "posix" or not os.path.isfile(SANDBOX_EXEC):
        return Capability(kind="none", detail=f"{SANDBOX_EXEC} not present")
    import subprocess

    try:
        canary = subprocess.run(
            [SANDBOX_EXEC, "-p", "(version 1)(allow default)", "/usr/bin/true"],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return Capability(kind="none", detail=f"sandbox-exec canary failed: {e}")
    if canary.returncode != 0:
        return Capability(kind="none", detail=f"sandbox-exec canary exited {canary.returncode}")
    return Capability(kind="seatbelt")


@dataclass(frozen=True)
class SeatbeltBackend:
    """Seatbelt, expressed as an argv rewrite."""

    capability: Capability = field(default_factory=lambda: Capability(kind="seatbelt"))

    def wrap(self, policy: Policy, argv: Sequence[str]) -> list[str]:
        return [
            SANDBOX_EXEC,
            "-p",
            generate_profile(policy),
            *(f"-D{name}={value}" for name, value in profile_params(policy)),
            "--",
            *argv,
        ]

    def attest(self, policy: Policy) -> Attestation:
        return Attestation(
            mechanism="seatbelt",
            fs="declared",
            exec_allowlist_version=EXEC_ALLOWLIST_VERSION,
        )


def profile_params(policy: Policy) -> list[tuple[str, str]]:
    """The ``-D`` bindings the generated profile refers to, in order."""
    return [
        (f"{prefix}_{index}", str(path))
        for prefix, paths in (
            ("READ", policy.read),
            ("WRITE", policy.write),
            ("EXEC", policy.execute),
        )
        for index, path in enumerate(paths)
    ]


def generate_profile(policy: Policy) -> str:
    """The SBPL for *policy*: upstream base, our tiers, upstream defaults.

    Order is load-bearing, because **SBPL is last-match-wins**: it is
    what lets ``(deny default)`` lead the base and still be overridden,
    what lets :func:`_read_only_guard` take a write back, and why the
    write tier is restated *after* that guard.

    That last one is not cosmetic. Landlock unions rights, so a narrower
    grant only ever widens: a writable directory nested inside a readable
    tree works there for free. Reproducing that here means the write set
    must have the final word, or the guard's ``(deny file-write* …)``
    over the read roots would revoke a writable path nested under one —
    Linux would allow and macOS would refuse the same policy.
    """
    return "\n".join(
        [
            read_profile(BASE_PROFILE),
            _tier(
                # `file-map-executable` rides with read, not with exec.
                # A venv's compiled extension modules (`.so`/`.dylib`)
                # live under site-packages — inside the *project*, so in
                # the read tier — and macOS gates `dlopen` on mapping
                # rather than on exec. Without this, `import numpy`
                # fails on macOS alone. Landlock does not gate mmap at
                # all, so read already implies it there: granting it here
                # is what makes the two platforms mean the same thing.
                "read: the project, the declared inputs, and the OS baseline",
                "(allow file-read* file-test-existence file-map-executable",
                "READ",
                len(policy.read),
            ),
            _tier(
                "execute: the environment + the versioned utility allowlist",
                "(allow process-exec* file-map-executable",
                "EXEC",
                len(policy.execute),
            ),
            ";; network: not controlled by lc, on any platform (recorded as `allowed`)",
            # Separate forms: `network*` and `system-socket` are distinct
            # operation families, and one malformed form voids the whole
            # profile rather than just its own line.
            "(allow network*)",
            "(allow system-socket)",
            # Sockets alone do not make the network usable: name lookup
            # and TLS go through mach services, which the base's
            # `(deny default)` blocks. Without this the attestation would
            # say `allowed` on macOS while every resolution failed.
            read_profile(NETWORK),
            "",
            read_profile(PLATFORM_DEFAULTS),
            _read_only_guard(policy),
            # Last, so a nested writable path beats the guard above it.
            _tier(
                "write: the project, the per-run private scope, and shared scratch",
                "(allow file-read* file-write*",
                "WRITE",
                len(policy.write),
            ),
        ]
    )


def _tier(comment: str, opener: str, prefix: str, count: int) -> str:
    """One `(allow …)` form over a parameterised path list."""
    if not count:
        return f";; {comment} — empty"
    body = "\n".join(f'  (subpath (param "{prefix}_{i}"))' for i in range(count))
    return f";; {comment}\n{opener}\n{body}\n)\n"


def _read_only_guard(policy: Policy) -> str:
    """Take back writes on everything readable that is not also writable.

    This is what keeps the profile's write set equal to the *policy's*
    write set. The Linux side gets that for free — Landlock grants only
    what the policy names — but here the upstream fragments hand out
    writes of their own, on shared scratch (``/tmp``, ``/var/tmp``) and
    on devices, and only a later ``deny`` can take one back.

It emits a ``deny`` over every read root that is not also writable —
    ``/usr``, ``/etc``, the interpreter's stdlib root — but **none of
    those is a write the fragments had granted**, so today it takes
    nothing back that was not already denied by ``(deny default)``. That
    is a property of the current baseline, not a reason to drop it:
    narrow ``_WRITE_BASELINE`` — dropping the host's ``/tmp`` to look
    more like a container, say — and the upstream ``/tmp`` grant would
    silently reopen it. The guard is computed from the policy, so it
    starts taking things back the moment there is something to take.

    Deliberately narrow: it names *our* read roots and nothing else, so
    the device and pty writes the upstream defaults grant survive. And it
    is emitted before the write tier, which restates the grants that must
    win — see :func:`generate_profile`.
    """
    unwritable = [
        index
        for index, path in enumerate(policy.read)
        if not policy.grants(path, policy.write)
    ]
    if not unwritable:
        return ""
    body = "\n".join(f'  (subpath (param "READ_{i}"))' for i in unwritable)
    return f";; readable, and deliberately not writable\n(deny file-write*\n{body}\n)\n"
