"""Parse recipe requests and refuse impossible execution before submitting work."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from lightcone.engine.execution_resources import TaskResources
from lightcone.engine.project import ProjectError

GIB = 1024**3


def _workers(*capacities: tuple[int, int]) -> dict[str, Any]:
    return {
        f"worker-{index}": {"resources": {"CPU": cpus, "MEMORY": memory}}
        for index, (cpus, memory) in enumerate(capacities)
    }


@pytest.mark.parametrize(
    ("memory", "expected"),
    [("512Mi", 512 * 1024**2), ("1.5GiB", 3 * GIB // 2), ("8GB", 8_000_000_000),
     ("1B", 1), ("2 Ti", 2 * 1024**4), ("1000kB", 1_000_000)],
)
def test_memory_units_have_explicit_decimal_or_binary_meaning(memory: str, expected: int) -> None:
    assert TaskResources.parse({"memory": memory}).memory_bytes == expected


@pytest.mark.parametrize(
    ("duration", "expected"),
    [("1h30m", 5400), ("30m", 1800), ("2d3h4m5s", 183845), ("45s", 45)],
)
def test_task_walltime_accepts_compound_durations(duration: str, expected: int) -> None:
    assert TaskResources.parse({"time_limit": duration}).time_seconds == expected


def test_integral_astra_float_cpu_count_is_accepted_without_rounding() -> None:
    assert TaskResources.parse({"cpus": 4.0}).cpus == 4
    with pytest.raises(ProjectError, match="fractional CPUs"):
        TaskResources.parse({"cpus": 0.5})


@pytest.mark.parametrize(
    "declaration",
    [
        {"cpus": 0}, {"cpus": True}, {"cpus": "4"}, {"cpus": None},
        {"memory": "0Gi"}, {"memory": "0.1B"}, {"memory": "16"}, {"memory": 16},
        {"memory": "400m"},
        {"time_limit": "0m"}, {"time_limit": ""}, {"time_limit": "5m2h"},
        {"time_limit": "unlimited"}, {"gpus": 1}, {"disk": "10Gi"}, {"ram": "1Gi"},
    ],
)
def test_invalid_or_unhonored_declarations_are_not_silently_ignored(
    declaration: dict[str, Any],
) -> None:
    with pytest.raises(ProjectError):
        TaskResources.parse(declaration)


def test_internal_resource_models_remain_validated() -> None:
    with pytest.raises(ValidationError):
        TaskResources(memory_bytes=-1)
    with pytest.raises(ValidationError):
        TaskResources(time_seconds=0)


def test_declared_requests_reserve_exact_cpu_and_memory_budgets() -> None:
    task = TaskResources.parse({"cpus": 4, "memory": "6Gi"})
    assert task.requirements(_workers((8, 16 * GIB))) == {"CPU": 4, "MEMORY": 6 * GIB}


def test_missing_memory_reserves_entire_worker_instead_of_guessing() -> None:
    assert TaskResources().requirements(_workers((8, 16 * GIB), (8, 16 * GIB))) == {
        "CPU": 1, "MEMORY": 16 * GIB,
    }


def test_probe_reserves_an_entire_worker() -> None:
    assert TaskResources().requirements(_workers((8, 16 * GIB)), whole_worker=True) == {
        "CPU": 8, "MEMORY": 16 * GIB,
    }


def test_cpu_count_is_independent_of_dask_execution_threads() -> None:
    workers = _workers((8, 16 * GIB))
    workers["worker-0"]["nthreads"] = 1
    assert TaskResources(cpus=8).requirements(workers)["CPU"] == 8


def test_a_task_must_fit_one_worker_not_the_sum_of_the_cluster() -> None:
    with pytest.raises(ProjectError, match="on one worker"):
        TaskResources(cpus=8, memory_bytes=20 * GIB).requirements(
            _workers((4, 16 * GIB), (4, 16 * GIB))
        )


def test_cpu_and_memory_must_fit_on_the_same_worker() -> None:
    with pytest.raises(ProjectError, match="no worker"):
        TaskResources(cpus=8, memory_bytes=16 * GIB).requirements(
            _workers((8, 4 * GIB), (4, 16 * GIB))
        )


def test_explicit_requests_can_select_a_fitting_worker() -> None:
    assert TaskResources(cpus=8, memory_bytes=8 * GIB).requirements(
        _workers((4, 4 * GIB), (8, 16 * GIB))
    ) == {"CPU": 8, "MEMORY": 8 * GIB}


@pytest.mark.parametrize("whole_worker", [False, True])
def test_missing_budgets_are_not_guessed_for_heterogeneous_workers(whole_worker: bool) -> None:
    with pytest.raises(ProjectError, match="identical"):
        TaskResources().requirements(
            _workers((4, 4 * GIB), (8, 16 * GIB)), whole_worker=whole_worker
        )


@pytest.mark.parametrize(
    "workers",
    [{}, {"worker": {}}, {"worker": {"resources": {"CPU": 1}}},
     {"worker": {"resources": {"CPU": True, "MEMORY": GIB}}},
     {"worker": {"resources": {"CPU": 1, "MEMORY": float("nan")}}}],
)
def test_absent_or_unknown_worker_capacity_refuses_execution(workers: dict[str, Any]) -> None:
    with pytest.raises(ProjectError):
        TaskResources().requirements(workers)
