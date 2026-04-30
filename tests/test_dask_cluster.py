"""Tests for the cluster-connection helpers.

Two surfaces:

- node-shape detection and resource-key formatting (pure functions
  consumed by both the daemon and the executor plugin),
- ``cluster_for_run``'s routing: explicit ``DASK_SCHEDULER_ADDRESS``
  vs. session-scoped scheduler via :mod:`dask_daemon`.

The daemon's own behavior is exercised in ``test_dask_daemon.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lightcone.engine.dask_cluster import (
    RESOURCE_CPUS,
    RESOURCE_GPUS,
    RESOURCE_MEMORY,
    _detect_node_shape,
    _NodeShape,
    _resource_dict,
    _resources_arg,
    cluster_for_run,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "DASK_SCHEDULER_ADDRESS",
        "SLURM_JOB_ID",
        "SLURM_NNODES",
        "SLURM_CPUS_ON_NODE",
        "SLURM_MEM_PER_NODE",
        "SLURM_GPUS_ON_NODE",
    ):
        monkeypatch.delenv(var, raising=False)


# ---- node shape ----------------------------------------------------------


def test_detect_shape_falls_back_to_os(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.cpu_count", lambda: 8)
    shape = _detect_node_shape()
    assert shape.cpus == 8
    assert shape.gpus == 0


def test_detect_shape_reads_slurm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLURM_CPUS_ON_NODE", "64")
    monkeypatch.setenv("SLURM_MEM_PER_NODE", "256000")  # 256 GB in MB
    monkeypatch.setenv("SLURM_GPUS_ON_NODE", "4")
    shape = _detect_node_shape()
    assert shape.cpus == 64
    assert shape.mem_bytes == 256_000_000_000
    assert shape.gpus == 4


def test_resource_dict_minimal() -> None:
    res = _resource_dict(_NodeShape(cpus=8, mem_bytes=0, gpus=0))
    assert res == {RESOURCE_CPUS: 8.0}


def test_resource_dict_full() -> None:
    res = _resource_dict(_NodeShape(cpus=64, mem_bytes=256_000_000_000, gpus=4))
    assert set(res.keys()) == {RESOURCE_CPUS, RESOURCE_MEMORY, RESOURCE_GPUS}


def test_resources_arg_minimal() -> None:
    arg = _resources_arg(_NodeShape(cpus=8, mem_bytes=0, gpus=0))
    assert arg == "cpus=8"


def test_resources_arg_full() -> None:
    arg = _resources_arg(_NodeShape(cpus=64, mem_bytes=256_000_000_000, gpus=4))
    assert arg == "cpus=64 memory=256000000000 gpus=4"


# ---- cluster_for_run routing --------------------------------------------


def test_existing_scheduler_address_yields_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When the user (or CI) supplies an address, we use it verbatim and
    never reach into the daemon — that's the escape hatch the env var is
    for, and going through ``ensure`` would fight the user's setup."""
    monkeypatch.setenv("DASK_SCHEDULER_ADDRESS", "tcp://example:8786")

    def _should_not_be_called(_: Path) -> str:
        raise AssertionError("ensure_scheduler must not run when env is set")

    monkeypatch.setattr(
        "lightcone.engine.dask_daemon.ensure_scheduler", _should_not_be_called
    )

    with cluster_for_run(project_path=tmp_path) as addr:
        assert addr == "tcp://example:8786"


def test_no_env_calls_ensure_scheduler(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Default path: cluster_for_run delegates to ensure_scheduler with
    the project path so the daemon picks the right scratch dir."""
    seen: dict[str, Path] = {}

    def _fake_ensure(project: Path) -> str:
        seen["project"] = project
        return "tcp://stub:9999"

    monkeypatch.setattr(
        "lightcone.engine.dask_daemon.ensure_scheduler", _fake_ensure
    )

    with cluster_for_run(project_path=tmp_path) as addr:
        assert addr == "tcp://stub:9999"
        assert seen["project"] == tmp_path
