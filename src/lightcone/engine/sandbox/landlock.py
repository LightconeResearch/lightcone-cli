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
    """Probe whether this kernel can enforce, and at which ABI.

    Returns:
        A ``landlock`` capability with its ABI, or ``none`` with the
        reason. Cached: a syscall whose answer cannot change inside one
        process.
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
    rather than on the mechanism-free :class:`Policy`. Strings only —
    the shim parses it with nothing but the stdlib.
    """
    return {
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
    ours means the shim is always the same lightcone-cli as the engine
    that wrote the policy.
    """

    capability: Capability = field(default_factory=capability)
    interpreter: str = sys.executable

    def wrap(self, policy: Policy, argv: Sequence[str]) -> list[str]:
        """Rewrite *argv* to run under the Landlock shim.

        Pure: no temporary files, no file descriptors, no global state.

        Args:
            policy: What the command may touch.
            argv: The command.

        Returns:
            The rewritten command.
        """
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
        """Report what the wrapped command will have enforced.

        Args:
            policy: The policy being wrapped.

        Returns:
            The record written with every output, derived from the flags
            actually applied.
        """
        return Attestation(
            mechanism="landlock",
            fs="declared",
            landlock_abi=self.capability.landlock_abi,
            exec_allowlist_version=EXEC_ALLOWLIST_VERSION,
        )
