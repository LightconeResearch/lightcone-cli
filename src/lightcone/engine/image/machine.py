"""macOS ``podman machine`` preflight (spec §5).

podman on macOS runs containers in a one-time Linux VM. Two failure
modes get refusals with the exact fix — never a mysterious error or,
worse, a silently empty mount:

* no machine / machine stopped → the one-time setup commands;
* a mount source outside the VM's shared directories → the
  ``podman machine set --volume`` command naming the source.

Linux is a no-op. (Designed here, exercised on macOS CI/manual runs —
this module is deliberately pure-subprocess so the JSON-shape unit
tests cover the logic.)
"""
from __future__ import annotations

import json
import platform
import subprocess
from pathlib import Path

from lightcone.engine.image.errors import MachinePreflightError

#: Default shares podman machine configures when none are listed.
_DEFAULT_SHARES = ("/Users", "/private", "/var/folders")


def machine_preflight(sources: list[Path], *, podman: str = "podman") -> None:
    if platform.system() != "Darwin":
        return
    inspect = _machine_inspect(podman)
    if inspect is None:
        raise MachinePreflightError(
            "podman on macOS needs its Linux VM (a one-time setup, "
            "~minutes):\n"
            "    podman machine init\n"
            "    podman machine start\n"
            "note: no GPU is available inside the VM."
        )
    if inspect.get("State") != "running":
        raise MachinePreflightError(
            "the podman machine VM is not running — start it with:\n"
            "    podman machine start"
        )
    shares = _shares(inspect)
    for source in sources:
        real = source.resolve()
        if not any(real.is_relative_to(share) for share in shares):
            raise MachinePreflightError(
                f"{real} lies outside the podman machine's shared "
                "directories — the mount would be silently empty inside "
                "the VM. Share it with:\n"
                f"    podman machine set --volume {real}\n"
                "    podman machine stop && podman machine start"
            )


def _machine_inspect(podman: str) -> dict[str, object] | None:
    try:
        proc = subprocess.run(
            [podman, "machine", "inspect"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    try:
        machines = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    return machines[0] if machines else None


def _shares(inspect: dict[str, object]) -> tuple[Path, ...]:
    mounts = inspect.get("Mounts")
    if not isinstance(mounts, list):
        mounts = []
    shares = [
        Path(str(m["Source"]))
        for m in mounts
        if isinstance(m, dict) and m.get("Source")
    ]
    return tuple(shares) if shares else tuple(Path(s) for s in _DEFAULT_SHARES)
