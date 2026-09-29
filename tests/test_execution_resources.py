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


@pytest.mark.parametrize("duration", ["1h30m", "30m", "45s", None])
def test_recipe_time_limit_is_explicitly_refused(duration: object) -> None:
    with pytest.raises(ProjectError, match="recipe time_limit is not supported"):
        TaskResources.parse({"time_limit": duration})


def test_integral_astra_float_cpu_count_is_accepted_without_rounding() -> None:
    assert TaskResources.parse({"cpus": 4.0}).cpus == 4
    with pytest.raises(ProjectError, match="fractional CPUs"):
        TaskResources.parse({"cpus": 0.5})


@pytest.mark.parametrize(
    "declaration",
    [
        {"cpus": 0}, {"cpus": True}, {"cpus": "4"}, {"cpus": None},
        {"memory": "0Gi"}, {"memory": "0.1B"}, {"memory": "16"}, {"memory": 16},
        {"memory": "400m"}, {"memory": None},
        {"time_limit": "0m"}, {"time_limit": ""}, {"time_limit": "5m2h"},
        {"time_limit": "unlimited"}, {"disk": "10Gi"}, {"ram": "1Gi"},
        {"gpus": -1}, {"gpus": True}, {"gpus": 0.5}, {"gpus": "1"}, {"gpus": None},
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
        TaskResources(gpus=-1)


def test_recipe_memory_uses_exact_bytes_without_decimal_context_rounding() -> None:
    one_byte = "0.000000000931322574615478515625"
    assert TaskResources.parse({"memory": f"{one_byte}Gi"}).memory_bytes == 1
    with pytest.raises(ProjectError, match="exactly representable"):
        TaskResources.parse({"memory": f"{one_byte}00000000000000001Gi"})


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


def test_gpu_recipe_reserves_the_whole_worker_gpu_budget() -> None:
    workers = _workers((8, 16 * GIB))
    workers["worker-0"]["resources"]["GPU"] = 4
    task = TaskResources.parse({"cpus": 2, "memory": "4Gi", "gpus": 1})
    assert task.requirements(workers) == {"CPU": 2, "MEMORY": 4 * GIB, "GPU": 4}


def test_gpu_request_must_fit_one_worker() -> None:
    workers = _workers((8, 16 * GIB), (8, 16 * GIB))
    for worker in workers.values():
        worker["resources"]["GPU"] = 1
    with pytest.raises(ProjectError, match="2 GPUs on one worker"):
        TaskResources(gpus=2).requirements(workers)


def test_cpu_workers_cannot_satisfy_gpu_requests() -> None:
    with pytest.raises(ProjectError, match="1 GPUs on one worker"):
        TaskResources(gpus=1).requirements(_workers((8, 16 * GIB)))


def test_cpu_recipe_does_not_reserve_gpu_capacity() -> None:
    workers = _workers((8, 16 * GIB), (8, 16 * GIB))
    workers["worker-0"]["resources"]["GPU"] = 4
    assert TaskResources().requirements(workers) == {"CPU": 1, "MEMORY": 16 * GIB}


def test_probe_reserves_cpu_memory_and_all_gpus() -> None:
    workers = _workers((8, 16 * GIB))
    workers["worker-0"]["resources"]["GPU"] = 4
    assert TaskResources().requirements(workers, whole_worker=True) == {
        "CPU": 8, "MEMORY": 16 * GIB, "GPU": 4,
    }


def test_gpu_requirements_ignore_workers_that_cannot_fit_the_recipe() -> None:
    workers = _workers((2, 2 * GIB), (8, 16 * GIB))
    workers["worker-0"]["resources"]["GPU"] = 1
    workers["worker-1"]["resources"]["GPU"] = 4
    assert TaskResources(cpus=4, memory_bytes=8 * GIB, gpus=1).requirements(workers) == {
        "CPU": 4, "MEMORY": 8 * GIB, "GPU": 4,
    }


@pytest.mark.parametrize("whole_worker", [False, True])
def test_whole_gpu_reservations_refuse_ambiguous_worker_budgets(whole_worker: bool) -> None:
    workers = _workers((8, 16 * GIB), (8, 16 * GIB))
    workers["worker-0"]["resources"]["GPU"] = 1
    workers["worker-1"]["resources"]["GPU"] = 4
    with pytest.raises(ProjectError, match="identical GPU budgets"):
        TaskResources(gpus=1).requirements(workers, whole_worker=whole_worker)


@pytest.mark.parametrize("capacity", [-1, 0.5, True, "1", None, float("nan"), float("inf")])
def test_malformed_gpu_capacity_is_never_ignored(capacity: object) -> None:
    workers = _workers((8, 16 * GIB))
    workers["worker-0"]["resources"]["GPU"] = capacity
    with pytest.raises(ProjectError, match="GPU count"):
        TaskResources().requirements(workers)
