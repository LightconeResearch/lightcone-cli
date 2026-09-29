"""GPU commands inherit the allocation mask without probing or assigning devices."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from lightcone.engine import assets, container, identity, plan, worker
from lightcone.engine import run as engine_run
from lightcone.engine.project import ProjectError
from lightcone.engine.sandbox import policy as policy_module

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
def test_recipe_inherits_the_whole_mask_without_changing_the_worker_environment(
    project: tuple[Path, plan.Task, worker.RunContext],
    monkeypatch: pytest.MonkeyPatch, requested: int,
) -> None:
    root, task, context = project
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "2,0")
    monkeypatch.setattr(policy_module, "_gpu_device_paths", lambda: ())
    result = worker.execute(root, replace(task, resources={"gpus": requested}), {}, context)
    assert result.status == "ok", result.reason
    assert task.output_path.read_text() == ("2,0" if requested else "")
    assert os.environ["CUDA_VISIBLE_DEVICES"] == "2,0"


@pytest.mark.parametrize("mask", [None, ""])
def test_missing_worker_gpu_mask_preserves_existing_output_and_manifest(
    project: tuple[Path, plan.Task, worker.RunContext], monkeypatch: pytest.MonkeyPatch,
    mask: str | None,
) -> None:
    root, task, context = project
    task.output_path.parent.mkdir(parents=True, exist_ok=True)
    task.output_path.write_text("previous output")
    task.manifest_path.write_text("previous manifest")
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    if mask is not None:
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", mask)
    with pytest.raises(ProjectError, match="nonempty allocation CUDA_VISIBLE_DEVICES"):
        worker.execute(root, replace(task, resources={"gpus": 1}), {}, context)
    assert task.output_path.read_text() == "previous output"
    assert task.manifest_path.read_text() == "previous manifest"


@pytest.mark.parametrize("runtime", ["docker", "podman"])
def test_unsupported_gpu_container_preserves_existing_output_and_manifest(
    project: tuple[Path, plan.Task, worker.RunContext], monkeypatch: pytest.MonkeyPatch,
    runtime: str,
) -> None:
    root, task, context = project
    task.output_path.parent.mkdir(parents=True, exist_ok=True)
    task.output_path.write_text("previous output")
    task.manifest_path.write_text("previous manifest")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "2,0")
    context = replace(context, runtime=replace(
        context.runtime, mode="containerized", runtime=runtime,
    ))
    with pytest.raises(ProjectError, match="GPU containers require podman-hpc"):
        worker.execute(root, replace(task, resources={"gpus": 1}), {}, context)
    assert task.output_path.read_text() == "previous output"
    assert task.manifest_path.read_text() == "previous manifest"


@pytest.mark.parametrize("use_gpus", [False, True])
def test_probe_inherits_the_native_allocation_mask_or_hides_gpus(
    project: tuple[Path, plan.Task, worker.RunContext], monkeypatch: pytest.MonkeyPatch,
    use_gpus: bool,
) -> None:
    _, _, context = project
    monkeypatch.setattr(policy_module, "_gpu_device_paths", lambda: ())
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "2,0")
    received: list[bytes] = []
    outcome = engine_run._probe(
        context.runtime, [], ("sh", "-c", "printf '%s' \"$CUDA_VISIBLE_DEVICES\""),
        use_gpus,
        output=lambda stream, data: received.append(data) if stream == "stdout" else None,
    )
    assert outcome.returncode == 0
    assert b"".join(received).decode() == ("2,0" if use_gpus else "")
    assert os.environ["CUDA_VISIBLE_DEVICES"] == "2,0"


def test_probe_missing_gpu_mask_refuses_before_running_the_command(
    project: tuple[Path, plan.Task, worker.RunContext], monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, context = project
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    execute = Mock()
    monkeypatch.setattr(engine_run.sandbox, "run", execute)
    with pytest.raises(ProjectError, match="nonempty allocation CUDA_VISIBLE_DEVICES"):
        engine_run._probe(context.runtime, [], ("true",), True, output=lambda *_: None)
    execute.assert_not_called()
