"""A recipe's declared CPUs size its numerical thread pools."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from lightcone.engine import assets, container, identity, plan, worker
from lightcone.engine.sandbox import policy as policy_module
from lightcone.engine.sandbox import scope

_SPEC = """
version: "0.0.13"
name: analysis
inputs: []
outputs:
  - id: pools
    type: metric
    format: txt
    recipe:
      command: >-
        printf '%s %s %s %s' "$OMP_NUM_THREADS" "$MKL_NUM_THREADS"
        "$OPENBLAS_NUM_THREADS" "$NUMBA_NUM_THREADS" > {output}
"""


@pytest.mark.parametrize("cpus", [None, 1, 6])
def test_a_recipe_runs_with_as_many_threads_as_cpus_it_declares(
    analysis: Callable[..., Path], monkeypatch: pytest.MonkeyPatch, cpus: int | None,
) -> None:
    """The worker's own value (the Nanny's 1 here) is the reservation's
    business only through the declaration: undeclared is one CPU, so one
    thread, and a wide recipe gets its width."""
    monkeypatch.setenv("OMP_NUM_THREADS", "1")
    root = analysis(_SPEC)
    task = plan.build(root).tasks[("baseline", "pools")]
    if cpus is not None:
        task = replace(task, resources={"cpus": cpus})
    context = worker.RunContext(
        env_version=identity.env_version(root),
        head=("0123456789abcdef", "https://example/analysis.git"),
        versions=assets.Versions(),
        runtime=container.runtime_for_run(root, build=False),
        uv_version="0.0.0-test",
    )
    result = worker.execute(root, task, {}, context)
    assert result.status == "ok", result.reason
    width = str(cpus or 1)
    assert task.output_path.read_text() == " ".join([width] * 4)
    assert os.environ["OMP_NUM_THREADS"] == "1"


@pytest.mark.parametrize("containerized", [False, True])
def test_a_probe_keeps_the_workers_thread_pools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, containerized: bool,
) -> None:
    """`lc run` has no declaration to size from, so it sets nothing."""
    monkeypatch.delenv("NUMBA_NUM_THREADS", raising=False)
    project = tmp_path / "project"
    project.mkdir()
    with scope(policy_module.exec_policy(project, containerized=containerized)) as built:
        assert "NUMBA_NUM_THREADS" not in built.env
    with scope(
        policy_module.exec_policy(project, containerized=containerized, threads=3)
    ) as built:
        assert {built.env[name] for name in policy_module.THREAD_POOLS} == {"3"}
