"""CUDA owns native device enumeration; only verified identities become capacity."""

from __future__ import annotations

import ctypes
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest

from lightcone.engine import gpu
from lightcone.engine.project import ProjectError

GPU = "GPU-01234567-89ab-cdef-0123-456789abcdef"
DEVICE = {"uuid": GPU, "name": "NVIDIA-A100-SXM4-80GB"}


def test_empty_mask_never_loads_or_queries_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    run = Mock(side_effect=AssertionError("must not query hidden devices"))
    monkeypatch.setattr(gpu.subprocess, "run", run)
    assert gpu.visible_devices() == ()


def test_uuid_probe_preserves_the_native_mask_and_uses_isolated_python(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gpu.sys, "platform", "linux")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "2,0")
    run = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps([DEVICE]), ""))
    monkeypatch.setattr(gpu.subprocess, "run", run)
    assert gpu.visible_devices() == (GPU,)
    argv = run.call_args.args[0]
    assert argv[1] == "-I"
    assert Path(argv[2]) == Path(gpu.__file__)
    assert "env" not in run.call_args.kwargs  # Native visibility is inherited unchanged.
    assert run.call_args.kwargs["timeout"] > 0


@pytest.mark.parametrize("payload", [
    {}, [42], [GPU], [{"uuid": "GPU-bad", "name": "A100"}],
    [{"uuid": GPU, "name": "A100:4"}], [{"uuid": GPU, "name": ""}],
])
def test_invalid_inventory_is_not_capacity(
    monkeypatch: pytest.MonkeyPatch, payload: object,
) -> None:
    monkeypatch.setattr(gpu.sys, "platform", "linux")
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    monkeypatch.setattr(
        gpu.subprocess, "run",
        Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(payload), "")),
    )
    with pytest.raises(ProjectError, match="invalid GPU identities"):
        gpu.visible_devices()


def test_inventory_preserves_models_and_rejects_duplicate_identities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gpu.sys, "platform", "linux")
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    run = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps([DEVICE]), ""))
    monkeypatch.setattr(gpu.subprocess, "run", run)
    assert gpu.inventory() == (gpu.Device(GPU, DEVICE["name"]),)
    run.return_value.stdout = json.dumps([DEVICE, DEVICE])
    with pytest.raises(ProjectError, match="duplicate GPU identities"):
        gpu.inventory()


def test_probe_failure_is_not_reported_as_an_empty_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gpu.sys, "platform", "linux")
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    monkeypatch.setattr(
        gpu.subprocess, "run",
        Mock(return_value=subprocess.CompletedProcess([], 1, "", "CUDA driver returned error 999")),
    )
    with pytest.raises(ProjectError, match="error 999"):
        gpu.visible_devices()


def test_missing_cuda_is_a_cpu_only_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ctypes, "CDLL", Mock(side_effect=OSError("no CUDA driver")))
    assert gpu._probe() == []


@pytest.mark.parametrize("partitioned", [False, True])
@pytest.mark.parametrize("model_name", ["NVIDIA A100-SXM4-80GB", "TITAN X (Pascal)"])
def test_probe_uses_cuda_device_handles_and_rejects_mig(
    monkeypatch: pytest.MonkeyPatch, partitioned: bool, model_name: str,
) -> None:
    # The visible ordinal maps to a driver handle, not a host GPU index. The
    # real ctypes buffers and signatures exercise the probe's FFI boundary.
    raw = UUID(GPU.removeprefix("GPU-")).bytes

    def count(pointer: object) -> int:
        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_int))[0] = 1
        return 0

    def device(pointer: object, ordinal: int) -> int:
        assert ordinal == 0
        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_int))[0] = 7
        return 0

    def identity(buffer: object, handle: ctypes.c_int, *, instance: bool = False) -> int:
        assert handle.value == 7
        ctypes.memmove(buffer, bytes(16) if partitioned and instance else raw, 16)
        return 0

    def name(buffer: object, size: int, handle: ctypes.c_int) -> int:
        assert handle.value == 7
        value = model_name.encode("ascii") + b"\x00"
        assert size >= len(value)
        ctypes.memmove(buffer, value, len(value))
        return 0

    driver = SimpleNamespace(
        cuInit=Mock(return_value=0),
        cuDeviceGetCount=Mock(side_effect=count),
        cuDeviceGet=Mock(side_effect=device),
        cuDeviceGetUuid=Mock(side_effect=identity),
        cuDeviceGetUuid_v2=Mock(side_effect=lambda b, d: identity(b, d, instance=True)),
        cuDeviceGetName=Mock(side_effect=name),
    )
    monkeypatch.setattr(ctypes, "CDLL", Mock(return_value=driver))
    if partitioned:
        with pytest.raises(RuntimeError, match="MIG"):
            gpu._probe()
    else:
        expected = "NVIDIA-A100-SXM4-80GB" if model_name.startswith("NVIDIA") else "TITAN-X-Pascal"
        assert gpu._probe() == [{"uuid": GPU, "name": expected}]


def test_only_nvidia_character_nodes_are_granted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("nvidia0", "nvidiactl", "nvidia-user-file", "unrelated"):
        (tmp_path / name).touch()
    monkeypatch.setattr(gpu, "_DEVICE_ROOT", tmp_path)
    monkeypatch.setattr(Path, "is_char_device", lambda p: p.name in {"nvidia0", "nvidiactl"})
    assert gpu.device_paths() == (tmp_path / "nvidia0", tmp_path / "nvidiactl")
