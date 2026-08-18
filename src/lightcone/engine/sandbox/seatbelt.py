"""The macOS backend: a generated Seatbelt profile.

``sandbox-exec`` is already the shape the seam wants — a wrapper command
that confines everything it execs — so there is no shim on this side.
The work is generating the SBPL, which is ordinary string building and
therefore testable (and golden-tested) on any OS.

**Most of the profile is not ours.** ``profiles/base.sbpl`` and
``profiles/platform-defaults.sbpl`` are vendored from the codex CLI
(itself derived from Chrome's macOS sandbox policy), because the macOS
read baseline is not something to derive from first principles — it is a
list of things that break, discovered one production failure at a time.
It carries entries no one would guess: ``/dev/dtracehelper``, the
``/dev/fd`` and pty regexes, firmlink-parent traversal under
``/System/Volumes/Data``, the ``opendirectoryd.libinfo`` lookup without
which ``getpwuid()`` raises ``KeyError``, ``cfprefsd``, and
``/opt/homebrew/lib``. They are kept near-verbatim so they can be diffed
against upstream; the single local delta is marked in the file.

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

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from importlib import resources

from lightcone.engine.sandbox.model import Attestation, Capability, Policy

SANDBOX_EXEC = "/usr/bin/sandbox-exec"

#: The vendored fragments, in the order they are concatenated.
BASE_PROFILE = "base.sbpl"
PLATFORM_DEFAULTS = "platform-defaults.sbpl"


def read_profile(name: str) -> str:
    """The raw text of a vendored SBPL fragment."""
    if name not in (BASE_PROFILE, PLATFORM_DEFAULTS):
        raise KeyError(f"unknown profile fragment: {name!r}")
    return (resources.files(__package__) / "profiles" / name).read_text(encoding="utf-8")


def capability() -> Capability:
    """Whether ``sandbox-exec`` is present and usable on this host.

    Seatbelt has been "deprecated" since 2012 and has never been given a
    removal date, but the profile dialect does drift across releases —
    so this runs a live canary rather than trusting the file's presence.
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
            # The overlay is applied *inside* the sandbox, so whatever the
            # caller put outside the wrap — the `uv run` hop — keeps the
            # real environment and finds its own cache. `env` is in the
            # utility allowlist, so it is executable by construction.
            *env_prefix(policy),
            *argv,
        ]

    def attest(self, policy: Policy) -> Attestation:
        return Attestation(
            mechanism="seatbelt",
            fs="declared",
            exec_allowlist_version=policy.exec_allowlist_version,
        )


def env_prefix(policy: Policy) -> list[str]:
    """``env K=V …``, or nothing when the policy sets no overlay."""
    if not policy.env:
        return []
    return ["/usr/bin/env", *(f"{k}={v}" for k, v in sorted(policy.env.items()))]


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
    """The SBPL for *policy*: vendored base, our tiers, vendored defaults.

    Order is load-bearing, because **SBPL is last-match-wins** — which is
    why ``(deny default)`` can lead the base and still be overridden, and
    why the read-only guard at the end can take back a write the
    platform defaults granted.
    """
    return "\n".join(
        [
            read_profile(BASE_PROFILE),
            _tier(
                "read: project tree + declared inputs",
                "(allow file-read* file-test-existence",
                "READ",
                len(policy.read),
            ),
            _tier(
                "write: the per-run private scope and scratch — never the project tree",
                "(allow file-read* file-write*",
                "WRITE",
                len(policy.write),
            ),
            _tier(
                "execute: the environment + the versioned utility allowlist",
                "(allow process-exec* file-map-executable",
                "EXEC",
                len(policy.execute),
            ),
            ";; network: not controlled by lc, on any platform (recorded as `allowed`)",
            "(allow network* system-socket)",
            "",
            read_profile(PLATFORM_DEFAULTS),
            _read_only_guard(policy),
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

    The vendored defaults grant write to shared scratch (``/tmp``,
    ``/var/tmp``) unconditionally, which would otherwise re-open exactly
    the hole :func:`~lightcone.engine.sandbox.policy._write_roots`
    closes on Linux — a project living under ``/tmp`` becoming writable.
    Emitting the deny **last** is what makes the two platforms agree,
    and it is only expressible because SBPL is last-match-wins (Landlock,
    which unions rights, cannot say this at all — which is why the Linux
    side has to solve it by leaving the root out of the policy instead).
    """
    unwritable = [
        index
        for index, path in enumerate(policy.read)
        if not any(path == root or path.is_relative_to(root) for root in policy.write)
    ]
    if not unwritable:
        return ""
    body = "\n".join(f'  (subpath (param "READ_{i}"))' for i in unwritable)
    return f";; readable, and deliberately not writable\n(deny file-write*\n{body}\n)\n"
