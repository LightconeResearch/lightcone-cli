"""The Linux backend: Landlock, reached through the exec shim.

All the kernel work lives in :mod:`lightcone._sandbox_exec`, which has to
be stdlib-only and importable on its own. What is left here is the two
things the engine needs: probing whether this host can enforce, and the
argv rewrite that makes a self-restricting mechanism look like a wrapper
command.
"""

from __future__ import annotations

import functools
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field

from lightcone import _sandbox_exec
from lightcone.engine.sandbox.model import (
    EXEC_ALLOWLIST_VERSION,
    Attestation,
    Capability,
    Policy,
)


@functools.cache
def capability() -> Capability:
    """Whether this kernel can enforce, and at which ABI.

    Cached: it is a syscall whose answer cannot change inside one
    process. The ABI is carried through to the manifest rather than
    merely gating — a "best effort" that succeeded against a kernel with
    no Landlock at all is exactly the silent degradation this layer
    exists to make impossible.
    """
    abi = _sandbox_exec.abi()
    if abi > 0:
        return Capability(kind="landlock", landlock_abi=abi)
    return Capability(
        kind="none",
        detail="landlock unavailable (kernel < 5.13, blocked by seccomp, or unsupported arch)",
    )


def _document(policy: Policy) -> dict[str, object]:
    """*policy* as the shim's ``--policy`` JSON.

    The shim's wire format, so it lives with the backend that speaks it
    rather than on the mechanism-free :class:`Policy`. It is an interface
    between two possibly different lightcone-cli versions — the launcher's
    and the project's — so it carries a version and holds only strings.
    """
    return {
        "version": _sandbox_exec.POLICY_VERSION,
        "read": [str(p) for p in policy.read],
        "write": [str(p) for p in policy.write],
        "execute": [str(p) for p in policy.execute],
    }


@dataclass(frozen=True)
class LandlockBackend:
    """Landlock, expressed as an argv rewrite.

    The command becomes ``<python> -m lightcone._sandbox_exec --policy
    <json> -- <command>``: the shim restricts itself and then *becomes*
    the command, so the restriction is inherited by everything below it
    and can never be shed.

    The interpreter is **lc's own**, not the project's. It only has to
    live long enough to issue three syscalls before ``execvp``, and using
    ours means the shim is always the same version as the engine that
    wrote the policy — the ``--policy`` document never has to survive a
    version gap.
    """

    capability: Capability = field(default_factory=capability)
    interpreter: str = sys.executable

    def wrap(self, policy: Policy, argv: Sequence[str]) -> list[str]:
        document = json.dumps(_document(policy), separators=(",", ":"), sort_keys=True)
        return [
            self.interpreter,
            "-m",
            "lightcone._sandbox_exec",
            "--policy",
            document,
            "--",
            *argv,
        ]

    def attest(self, policy: Policy) -> Attestation:
        return Attestation(
            mechanism="landlock",
            fs="declared",
            landlock_abi=self.capability.landlock_abi,
            exec_allowlist_version=EXEC_ALLOWLIST_VERSION,
        )
