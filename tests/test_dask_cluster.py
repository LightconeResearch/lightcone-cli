"""Unit tests for the cluster bootstrap.

We test the routing decision (which branch fires given env vars) and the
node-shape detection. The actual `LocalCluster` spin-up is exercised in a
single smoke test; the `srun`-backed path is mocked because real
multi-node testing requires SLURM.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import pytest

from lightcone.engine.dask_cluster import (
    RESOURCE_CPUS,
    RESOURCE_GPUS,
    RESOURCE_MEMORY,
    _detect_node_shape,
    _NodeShape,
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


def test_resources_arg_minimal() -> None:
    arg = _resources_arg(_NodeShape(cpus=8, mem_bytes=0, gpus=0))
    assert arg == "cpus=8"


def test_resources_arg_full() -> None:
    arg = _resources_arg(_NodeShape(cpus=64, mem_bytes=256_000_000_000, gpus=4))
    assert arg == "cpus=64 memory=256000000000 gpus=4"


def test_existing_scheduler_address_yields_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASK_SCHEDULER_ADDRESS", "tcp://example:8786")

    with cluster_for_run() as addr:
        assert addr == "tcp://example:8786"


def test_no_env_uses_local_cluster() -> None:
    """The local-cluster branch should actually start a (tiny) cluster."""
    sentinel: dict[str, str] = {}

    @contextmanager
    def _fake_local(*, verbose: bool, local_directory: str | None = None):
        sentinel["called"] = "local"
        yield "tcp://stub:9999"

    with patch("lightcone.engine.dask_cluster._local_cluster", _fake_local):
        with cluster_for_run() as addr:
            assert addr == "tcp://stub:9999"
            assert sentinel["called"] == "local"


def test_slurm_env_takes_slurm_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLURM_JOB_ID", "12345")
    sentinel: dict[str, str] = {}

    @contextmanager
    def _fake_slurm(*, verbose: bool, local_directory: str | None = None):
        sentinel["called"] = "slurm"
        yield "tcp://stub:9999"

    with patch("lightcone.engine.dask_cluster._slurm_backed_cluster", _fake_slurm):
        with cluster_for_run() as addr:
            assert addr == "tcp://stub:9999"
            assert sentinel["called"] == "slurm"


def test_existing_scheduler_address_wins_over_slurm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If both are set, the explicit address takes precedence."""
    monkeypatch.setenv("DASK_SCHEDULER_ADDRESS", "tcp://existing:8786")
    monkeypatch.setenv("SLURM_JOB_ID", "12345")

    @contextmanager
    def _should_not_run(*, verbose: bool, local_directory: str | None = None):
        raise AssertionError("slurm path should not have been taken")
        yield  # pragma: no cover

    with patch("lightcone.engine.dask_cluster._slurm_backed_cluster", _should_not_run):
        with cluster_for_run() as addr:
            assert addr == "tcp://existing:8786"


def test_slurm_backed_cluster_binds_to_routable_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multi-node SLURM allocations need the scheduler bound to a hostname
    workers on other nodes can reach. The default LocalCluster host of
    127.0.0.1 fails silently with `wait_for_workers` timeouts.
    """
    monkeypatch.setenv("SLURM_JOB_ID", "12345")
    monkeypatch.setenv("SLURM_NNODES", "2")
    monkeypatch.setenv("SLURMD_NODENAME", "nid001234")
    monkeypatch.setattr(
        "lightcone.engine.dask_cluster.shutil.which", lambda _: "/usr/bin/dask"
    )

    captured: dict[str, object] = {}

    class _FakeCluster:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)
            self.scheduler_address = "tcp://nid001234:8786"

        def close(self) -> None:
            pass

    class _FakeClient:
        def __init__(self, addr: str) -> None:
            captured["client_addr"] = addr

        def wait_for_workers(self, n_workers: int, timeout: int) -> None:
            pass

        def close(self) -> None:
            pass

    class _FakePopen:
        def __init__(self, cmd: list[str]) -> None:
            captured["worker_cmd"] = cmd

        def terminate(self) -> None:
            pass

        def wait(self, timeout: int | None = None) -> int:
            return 0

        def kill(self) -> None:
            pass

    monkeypatch.setattr("dask.distributed.LocalCluster", _FakeCluster)
    monkeypatch.setattr("dask.distributed.Client", _FakeClient)
    monkeypatch.setattr("subprocess.Popen", _FakePopen)

    from lightcone.engine.dask_cluster import _slurm_backed_cluster

    with _slurm_backed_cluster(verbose=False, local_directory=None) as addr:
        assert addr == "tcp://nid001234:8786"

    assert captured.get("host") == "nid001234", (
        f"LocalCluster must be told to bind to the SLURM nodename so remote "
        f"workers can connect; got host={captured.get('host')!r}"
    )


def test_slurm_backed_cluster_falls_back_to_gethostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without SLURMD_NODENAME, fall back to socket.gethostname()."""
    monkeypatch.setenv("SLURM_JOB_ID", "12345")
    monkeypatch.setenv("SLURM_NNODES", "1")
    monkeypatch.delenv("SLURMD_NODENAME", raising=False)
    monkeypatch.setattr(
        "lightcone.engine.dask_cluster.shutil.which", lambda _: "/usr/bin/dask"
    )
    monkeypatch.setattr(
        "lightcone.engine.dask_cluster.socket.gethostname", lambda: "host-fallback"
    )

    captured: dict[str, object] = {}

    class _FakeCluster:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)
            self.scheduler_address = "tcp://host-fallback:8786"

        def close(self) -> None:
            pass

    class _FakeClient:
        def __init__(self, addr: str) -> None:
            pass

        def wait_for_workers(self, n_workers: int, timeout: int) -> None:
            pass

        def close(self) -> None:
            pass

    class _FakePopen:
        def __init__(self, cmd: list[str]) -> None:
            pass

        def terminate(self) -> None:
            pass

        def wait(self, timeout: int | None = None) -> int:
            return 0

        def kill(self) -> None:
            pass

    monkeypatch.setattr("dask.distributed.LocalCluster", _FakeCluster)
    monkeypatch.setattr("dask.distributed.Client", _FakeClient)
    monkeypatch.setattr("subprocess.Popen", _FakePopen)

    from lightcone.engine.dask_cluster import _slurm_backed_cluster

    with _slurm_backed_cluster(verbose=False, local_directory=None):
        pass

    assert captured.get("host") == "host-fallback"


def test_local_cluster_advertises_memory_and_gpus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dask only schedules a task on a worker that advertises every
    requested resource key — so the local worker must expose mem and
    gpus too, otherwise rules with ``mem_mb``/``gpus_per_task`` hang.
    """
    monkeypatch.setattr(
        "lightcone.engine.dask_cluster._detect_node_shape",
        lambda: _NodeShape(cpus=4, mem_bytes=16_000_000_000, gpus=2),
    )

    captured: dict[str, object] = {}

    class _FakeCluster:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)
            self.scheduler_address = "tcp://stub:0"

        def close(self) -> None:
            pass

    monkeypatch.setattr("dask.distributed.LocalCluster", _FakeCluster)

    from lightcone.engine.dask_cluster import _local_cluster

    with _local_cluster(verbose=False, local_directory=None):
        pass

    resources = captured.get("resources")
    assert isinstance(resources, dict)
    assert set(resources.keys()) == {RESOURCE_CPUS, RESOURCE_MEMORY, RESOURCE_GPUS}


@pytest.mark.slow
def test_local_cluster_smoke() -> None:
    """End-to-end: a real LocalCluster spins up, accepts a task, tears down."""
    from dask.distributed import Client

    from lightcone.engine.dask_cluster import _local_cluster

    with _local_cluster(verbose=False, local_directory=None) as addr:
        client = Client(addr)
        try:
            assert client.submit(lambda x: x + 1, 41).result() == 42
        finally:
            client.close()
