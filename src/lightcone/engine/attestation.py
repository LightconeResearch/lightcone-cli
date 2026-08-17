"""Worker-side runtime attestation.

Captures the environment-describing manifest fields (spec §3) from
*inside the process that runs the recipe* — which in containerized mode
is inside the image, so the scoping rule "environment-describing fields
are captured inside the boundary" holds by construction.

Everything here is best-effort observation, never a gate: a field that
cannot be determined records ``None`` rather than failing the run.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

#: Threading knobs that silently change numerical results' runtime
#: behaviour — recorded so a puzzled reader a year later can see them.
_THREAD_KNOBS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)


def capture_runtime_attestation() -> dict[str, Any]:
    """The attestation block merged into every manifest."""
    return {
        "uv_version": _uv_version(),
        "platform": {
            "os_release": _os_release(),
            "kernel": platform.release(),
            "glibc": _glibc(),
            "arch": platform.machine(),
        },
        "python_build": _python_build(),
        "env_snapshot": {
            "locale": os.environ.get("LC_ALL") or os.environ.get("LANG"),
            "tz": os.environ.get("TZ"),
            **{k.lower(): os.environ.get(k) for k in _THREAD_KNOBS},
        },
        "gpu_driver": _gpu_driver(),
    }


def _uv_version() -> str | None:
    if not shutil.which("uv"):
        return None
    try:
        out = subprocess.run(
            ["uv", "--version"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    # "uv 0.12.3 (…)" → "0.12.3"
    parts = out.stdout.split()
    return parts[1] if out.returncode == 0 and len(parts) >= 2 else None


def _os_release() -> str | None:
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.partition("=")[2].strip().strip('"')
    except OSError:
        pass
    if sys.platform == "darwin":
        return f"macOS {platform.mac_ver()[0]}"
    return None


def _glibc() -> str | None:
    lib, version = platform.libc_ver()
    return f"{lib} {version}" if lib else None


def _python_build() -> str:
    impl = platform.python_implementation()
    build = " ".join(platform.python_build())
    return f"{impl} {platform.python_version()} ({build})"


def _gpu_driver() -> str | None:
    try:
        text = Path("/proc/driver/nvidia/version").read_text()
        return text.splitlines()[0].strip() if text else None
    except OSError:
        return None
