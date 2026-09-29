"""Discover CUDA-visible GPU identities without initializing CUDA in a worker.

CUDA owns device ordering and native visibility masks. A short isolated process
asks the driver for UUIDs; interpreting numeric masks ourselves would confuse
Slurm's allocation-local indices with host device numbers.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

_UUID = re.compile(r"GPU-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
_DEVICE_ROOT = Path("/dev")


class Device(NamedTuple):
    """A native CUDA identity and its model name, with punctuation made CLI-safe."""

    uuid: str
    name: str


def inventory() -> tuple[Device, ...]:
    """Return devices visible under this process's native CUDA mask.

    Missing CUDA drivers or an empty visible set return no devices. Broken
    drivers and unsupported partitioned GPUs raise instead of inventing capacity.

    Returns:
        Native UUIDs and model names, in CUDA visibility order.

    Raises:
        ProjectError: CUDA discovery failed or returned an untrustworthy inventory.
    """
    from lightcone.engine.project import ProjectError

    if os.environ.get("CUDA_VISIBLE_DEVICES") == "" or sys.platform != "linux":
        return ()
    try:
        result = subprocess.run(
            [sys.executable, "-I", str(Path(__file__))],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15,
        )
        if result.returncode:
            raise ValueError(result.stderr.strip()[:1024] or "CUDA probe exited unsuccessfully")
        devices = json.loads(result.stdout)
        if not isinstance(devices, list):
            raise ValueError("CUDA probe returned invalid GPU identities")
        result_devices = []
        for item in devices:
            if (
                not isinstance(item, dict) or item.keys() != {"uuid", "name"}
                or not isinstance(item["uuid"], str) or not _UUID.fullmatch(item["uuid"])
                or not isinstance(item["name"], str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", item["name"])
            ):
                raise ValueError("CUDA probe returned invalid GPU identities")
            result_devices.append(Device(**item))
        if len({device.uuid for device in result_devices}) != len(result_devices):
            raise ValueError("CUDA probe returned duplicate GPU identities")
        return tuple(result_devices)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ProjectError(f"cannot discover visible NVIDIA GPUs: {exc}") from exc


def visible_devices() -> tuple[str, ...]:
    """Return native CUDA UUIDs suitable for a subprocess visibility mask."""
    return tuple(device.uuid for device in inventory())


def device_paths() -> tuple[Path, ...]:
    """Return existing NVIDIA device nodes needed by direct CUDA commands.

    These grants retain native OS/cgroup permissions. CUDA visibility controls
    cooperative device selection; it is not an additional device-isolation layer.
    """
    candidates = [
        *(_DEVICE_ROOT / name for name in ("nvidiactl", "nvidia-uvm", "nvidia-uvm-tools")),
        *_DEVICE_ROOT.glob("nvidia[0-9]*"),
        *(_DEVICE_ROOT / "nvidia-caps").glob("*"),
    ]
    return tuple(sorted(path for path in candidates if path.is_char_device()))


def _probe() -> list[dict[str, str]]:
    # Keep this child stdlib-only: CUDA initialization never enters the CLI,
    # allocation owner, or reusable Dask worker. No context or memory is allocated.
    import ctypes
    from uuid import UUID

    try:
        driver = ctypes.CDLL("libcuda.so.1")
    except OSError:
        return []

    def check(code: int) -> None:
        if code:
            raise RuntimeError(f"CUDA driver returned error {code}")

    driver.cuInit.argtypes = [ctypes.c_uint]
    status = driver.cuInit(0)
    if status == 100:  # CUDA_ERROR_NO_DEVICE
        return []
    check(status)
    driver.cuDeviceGetCount.argtypes = [ctypes.POINTER(ctypes.c_int)]
    driver.cuDeviceGet.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_int]
    driver.cuDeviceGetUuid.argtypes = [ctypes.c_void_p, ctypes.c_int]
    driver.cuDeviceGetUuid_v2.argtypes = [ctypes.c_void_p, ctypes.c_int]
    driver.cuDeviceGetName.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    count = ctypes.c_int()
    check(driver.cuDeviceGetCount(ctypes.byref(count)))
    devices = []
    for ordinal in range(count.value):
        device = ctypes.c_int()
        check(driver.cuDeviceGet(ctypes.byref(device), ordinal))
        physical, instance = ctypes.create_string_buffer(16), ctypes.create_string_buffer(16)
        check(driver.cuDeviceGetUuid(physical, device))
        check(driver.cuDeviceGetUuid_v2(instance, device))
        if physical.raw != instance.raw:
            raise RuntimeError("partitioned (MIG) GPUs are not supported; request whole GPUs")
        name = ctypes.create_string_buffer(256)
        check(driver.cuDeviceGetName(name, len(name), device))
        devices.append({
            "uuid": f"GPU-{UUID(bytes=physical.raw)}",
            "name": re.sub(r"[^A-Za-z0-9_.-]+", "-", name.value.decode("ascii")).strip("-"),
        })
    return devices


if __name__ == "__main__":
    try:
        print(json.dumps(_probe()))
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
