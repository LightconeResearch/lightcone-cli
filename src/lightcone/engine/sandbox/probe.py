"""Capability probe + hermeticity composition.

The probe runs worker-side, per job — the driver's kernel is not the
worker's, and in containerized mode the relevant question (are the
``landlock_*`` syscalls admitted by the seccomp profile?) can only be
answered inside the container. :func:`compose_attestation` is the single
home of the §7 enum mapping; the manifest records what the *applied
flags* were, never a documentation row.
"""
from __future__ import annotations

import os
import platform
import subprocess
from functools import cache

from lightcone.engine.boundary import SandboxAttestation
from lightcone.engine.contract import CONTAINER_NETWORK_ENV, in_container
from lightcone.engine.sandbox.model import SandboxCapability


@cache
def probe() -> SandboxCapability:
    system = platform.system()
    if system == "Linux":
        from lightcone.engine.sandbox import _landlock

        abi = _landlock.abi()
        if abi > 0:
            return SandboxCapability(kind="landlock", landlock_abi=abi)
        return SandboxCapability(
            kind="none",
            detail="landlock unavailable (kernel < 5.13, or seccomp blocks it)",
        )
    if system == "Darwin":
        return _seatbelt_capability()
    return SandboxCapability(kind="none", detail=f"no sandbox mechanism on {system}")


def _seatbelt_capability() -> SandboxCapability:
    if not os.path.isfile("/usr/bin/sandbox-exec"):
        return SandboxCapability(kind="none", detail="sandbox-exec not present")
    try:
        canary = subprocess.run(
            ["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)",
             "/usr/bin/true"],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return SandboxCapability(kind="none", detail=f"sandbox-exec canary failed: {e}")
    if canary.returncode != 0:
        return SandboxCapability(
            kind="none",
            detail=f"sandbox-exec canary exited {canary.returncode} "
            f"(macOS {platform.mac_ver()[0]})",
        )
    return SandboxCapability(kind="seatbelt")


def compose_attestation(
    capability: SandboxCapability,
    *,
    fs_scope: str,
    exec_allowlist_version: int | None,
    disabled: bool = False,
) -> SandboxAttestation:
    """The §7 hermeticity record for one exec.

    Network enum, normative mapping: Landlock cannot express a useful
    deny (ABI ≤ 3 has no network control; ABI 4 cannot carve out
    loopback) ⇒ ``unenforced``; Seatbelt denies non-loopback ⇒
    ``denied``; a container run under ``--net=none`` ⇒ ``denied``;
    no restriction applied ⇒ ``allowed``.
    """
    inside = in_container()
    container_network = os.environ.get(CONTAINER_NETWORK_ENV)

    if disabled or capability.kind == "none":
        if inside:
            # The mount set still bounds the world even without an
            # in-container Landlock tier.
            return SandboxAttestation(
                mechanism="podman",
                fs="project-rw",
                network="denied" if container_network == "none" else "allowed",
            )
        return SandboxAttestation(mechanism="none", fs="open", network="allowed")

    if capability.kind == "landlock":
        mechanism = "podman+landlock" if inside else "landlock"
        if inside:
            network = "denied" if container_network == "none" else "allowed"
        else:
            network = "unenforced"
        return SandboxAttestation(
            mechanism=mechanism,
            fs=fs_scope,
            network=network,
            landlock_abi=capability.landlock_abi,
            exec_allowlist_version=exec_allowlist_version,
        )

    # seatbelt
    return SandboxAttestation(
        mechanism="seatbelt",
        fs=fs_scope,
        network="denied",
        exec_allowlist_version=exec_allowlist_version,
    )


def status_line() -> str:
    """The ``lc status`` sandbox header line for this host."""
    cap = probe()
    att = compose_attestation(
        cap, fs_scope="declared", exec_allowlist_version=None
    )
    detail = f" — {cap.detail}" if cap.kind == "none" and cap.detail else ""
    return f"{att.mechanism} (fs: {att.fs}, network: {att.network}){detail}"
