"""GPU requests reach each command without changing the shared worker environment."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from lightcone.engine import assets, container, gpu, identity, plan, worker
from lightcone.engine import run as engine_run
from lightcone.engine.project import ProjectError

_SPEC = """
version: "0.0.13"
name: analysis
inputs: []
outputs:
  - id: visibility
    type: metric
    format: txt
    recipe:
      command: printf '%s' "$CUDA_VISIBLE_DEVICES" > {output}
"""


@pytest.fixture
def project(analysis: Callable[..., Path]) -> tuple[Path, plan.Task, worker.RunContext]:
    root = analysis(_SPEC)
    task = plan.build(root).tasks[("baseline", "visibility")]
    context = worker.RunContext(
        env_version=identity.env_version(root),
        head=("0123456789abcdef", "https://example/analysis.git"),
        versions=assets.Versions(),
        runtime=container.runtime_for_run(root, build=False),
        uv_version="0.0.0-test",
    )
    return root, task, context


@pytest.mark.parametrize("requested", [0, 1, 2])
def test_recipe_gets_requested_uuid_mask_in_its_actual_subprocess(
    project: tuple[Path, plan.Task, worker.RunContext],
    monkeypatch: pytest.MonkeyPatch, requested: int,
) -> None:
    root, task, context = project
    visible = ("GPU-second", "GPU-first")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "native-allocation")
    monkeypatch.setattr(gpu, "visible_devices", lambda: visible)
    monkeypatch.setattr(gpu, "device_paths", lambda: ())
    result = worker.execute(root, replace(task, resources={"gpus": requested}), {}, context)
    assert result.status == "ok", result.reason
    assert task.output_path.read_text() == ",".join(visible[:requested])
    assert os.environ["CUDA_VISIBLE_DEVICES"] == "native-allocation"


def test_worker_gpu_mismatch_preserves_existing_output_and_manifest(
    project: tuple[Path, plan.Task, worker.RunContext], monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, task, context = project
    task.output_path.parent.mkdir(parents=True, exist_ok=True)
    task.output_path.write_text("previous output")
    task.manifest_path.write_text("previous manifest")
    monkeypatch.setattr(gpu, "visible_devices", lambda: ("GPU-only",))
    with pytest.raises(ProjectError, match="requests 2 GPUs.*only 1 CUDA devices"):
        worker.execute(root, replace(task, resources={"gpus": 2}), {}, context)
    assert task.output_path.read_text() == "previous output"
    assert task.manifest_path.read_text() == "previous manifest"


@pytest.mark.parametrize("reserved", [0, 1, 2])
def test_probe_exposes_only_reserved_devices_from_the_native_allocation(
    project: tuple[Path, plan.Task, worker.RunContext], monkeypatch: pytest.MonkeyPatch,
    reserved: int,
) -> None:
    _, _, context = project
    visible = ("GPU-first", "GPU-second", "GPU-unreserved")
    discover = Mock(return_value=visible)
    monkeypatch.setattr(gpu, "visible_devices", discover)
    monkeypatch.setattr(gpu, "device_paths", lambda: ())
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "native-allocation")
    received: list[bytes] = []
    outcome = engine_run._probe(
        context.runtime, [], ("sh", "-c", "printf '%s' \"$CUDA_VISIBLE_DEVICES\""),
        reserved,
        output=lambda stream, data: received.append(data) if stream == "stdout" else None,
    )
    assert outcome.returncode == 0
    assert b"".join(received).decode() == ",".join(visible[:reserved])
    assert discover.call_count == bool(reserved)
    assert os.environ["CUDA_VISIBLE_DEVICES"] == "native-allocation"


def test_probe_gpu_shortage_refuses_before_running_the_command(
    project: tuple[Path, plan.Task, worker.RunContext], monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, context = project
    monkeypatch.setattr(gpu, "visible_devices", lambda: ("GPU-only",))
    execute = Mock()
    monkeypatch.setattr(engine_run.sandbox, "run", execute)
    with pytest.raises(ProjectError, match="reserved 2 GPUs.*only 1 CUDA devices"):
        engine_run._probe(context.runtime, [], ("true",), 2, output=lambda *_: None)
    execute.assert_not_called()
