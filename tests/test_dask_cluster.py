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
        "DASK_GATEWAY__ADDRESS",
        "LIGHTCONE_GATEWAY_CLUSTER",
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

    with cluster_for_run() as env:
        assert env == {"DASK_SCHEDULER_ADDRESS": "tcp://example:8786"}


def test_no_env_uses_local_cluster() -> None:
    """The local-cluster branch should actually start a (tiny) cluster."""
    sentinel: dict[str, str] = {}

    @contextmanager
    def _fake_local(*, verbose: bool, local_directory: str | None = None):
        sentinel["called"] = "local"
        yield "tcp://stub:9999"

    with patch("lightcone.engine.dask_cluster._local_cluster", _fake_local):
        with cluster_for_run() as env:
            assert env == {"DASK_SCHEDULER_ADDRESS": "tcp://stub:9999"}
            assert sentinel["called"] == "local"


def test_slurm_env_takes_slurm_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLURM_JOB_ID", "12345")
    sentinel: dict[str, str] = {}

    @contextmanager
    def _fake_slurm(*, verbose: bool, local_directory: str | None = None):
        sentinel["called"] = "slurm"
        yield "tcp://stub:9999"

    with patch("lightcone.engine.dask_cluster._slurm_backed_cluster", _fake_slurm):
        with cluster_for_run() as env:
            assert env == {"DASK_SCHEDULER_ADDRESS": "tcp://stub:9999"}
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
        with cluster_for_run() as env:
            assert env == {"DASK_SCHEDULER_ADDRESS": "tcp://existing:8786"}


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
        def __init__(self, cmd: list[str], **kwargs: object) -> None:
            captured["worker_cmd"] = cmd
            captured["worker_kwargs"] = kwargs

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
        def __init__(self, cmd: list[str], **kwargs: object) -> None:
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


# ---------------------------------------------------------------------------
# Dask Gateway branch
# ---------------------------------------------------------------------------


class _FakeGatewayClient:
    def __init__(self, resources: dict[str, float] | None) -> None:
        self._resources = resources

    def wait_for_workers(self, n: int, timeout: int) -> None:
        pass

    def scheduler_info(self) -> dict[str, object]:
        if self._resources is None:
            return {"workers": {}}
        return {"workers": {"w0": {"resources": self._resources}}}

    def close(self) -> None:
        pass


class _FakeGatewayCluster:
    def __init__(
        self, name: str, log: list[str], resources: dict[str, float] | None
    ) -> None:
        self.name = name
        self.dashboard_link = f"http://hub/services/dask-gateway/{name}/status"
        self._log = log
        self._resources = resources

    def adapt(self, minimum: int, maximum: int) -> None:
        self._log.append(f"adapt({minimum},{maximum})")

    def get_client(self) -> _FakeGatewayClient:
        return _FakeGatewayClient(self._resources)

    def shutdown(self) -> None:
        self._log.append("shutdown")

    def close(self) -> None:
        self._log.append("close")


def _install_fake_gateway(
    monkeypatch: pytest.MonkeyPatch,
    log: list[str],
    resources: dict[str, float] | None = None,
):
    """Inject a fake ``dask_gateway`` module; return it for inspection."""
    import sys
    import types

    class _FakeGateway:
        def new_cluster(self, **kwargs: object) -> _FakeGatewayCluster:
            log.append(f"new_cluster({kwargs})")
            return _FakeGatewayCluster("hub.new123", log, resources)

        def connect(self, name: str) -> _FakeGatewayCluster:
            log.append(f"connect({name})")
            return _FakeGatewayCluster(name, log, resources)

    module = types.ModuleType("dask_gateway")
    module.Gateway = _FakeGateway  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "dask_gateway", module)
    return module


def test_gateway_env_takes_gateway_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ambient DASK_GATEWAY__ADDRESS (a hub pod) routes to the gateway branch."""
    monkeypatch.setenv("DASK_GATEWAY__ADDRESS", "http://proxy/services/dask-gateway/")
    log: list[str] = []
    _install_fake_gateway(monkeypatch, log, resources={RESOURCE_CPUS: 2.0})

    with cluster_for_run(max_workers=5) as env:
        assert env == {"LIGHTCONE_GATEWAY_CLUSTER": "hub.new123"}
        assert any(call.startswith("new_cluster") for call in log)
        assert "adapt(1,5)" in log

    assert "shutdown" in log, "owned gateway cluster must be shut down on exit"


def test_gateway_wins_over_slurm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASK_GATEWAY__ADDRESS", "http://proxy/services/dask-gateway/")
    monkeypatch.setenv("SLURM_JOB_ID", "12345")
    log: list[str] = []
    _install_fake_gateway(monkeypatch, log, resources={RESOURCE_CPUS: 2.0})

    @contextmanager
    def _should_not_run(*, verbose: bool, local_directory: str | None = None):
        raise AssertionError("slurm path should not have been taken")
        yield  # pragma: no cover

    with patch("lightcone.engine.dask_cluster._slurm_backed_cluster", _should_not_run):
        with cluster_for_run() as env:
            assert "LIGHTCONE_GATEWAY_CLUSTER" in env


def test_explicit_address_wins_over_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASK_SCHEDULER_ADDRESS", "tcp://existing:8786")
    monkeypatch.setenv("DASK_GATEWAY__ADDRESS", "http://proxy/services/dask-gateway/")

    with cluster_for_run() as env:
        assert env == {"DASK_SCHEDULER_ADDRESS": "tcp://existing:8786"}


def test_gateway_attach_by_name_is_not_shut_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LIGHTCONE_GATEWAY_CLUSTER attaches to an existing (e.g. sidebar-created)
    cluster and must leave it running on exit — same convention as the
    DASK_SCHEDULER_ADDRESS branch."""
    monkeypatch.setenv("LIGHTCONE_GATEWAY_CLUSTER", "hub.abc999")
    log: list[str] = []
    _install_fake_gateway(monkeypatch, log, resources={RESOURCE_CPUS: 2.0})

    with cluster_for_run() as env:
        assert env == {"LIGHTCONE_GATEWAY_CLUSTER": "hub.abc999"}
        assert "connect(hub.abc999)" in log
        assert not any(call.startswith("new_cluster") for call in log)
        assert not any(call.startswith("adapt") for call in log), (
            "attached clusters keep the user's scaling"
        )

    assert "shutdown" not in log
    assert "close" in log


def test_gateway_missing_client_raises_helpfully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    monkeypatch.setenv("DASK_GATEWAY__ADDRESS", "http://proxy/services/dask-gateway/")
    monkeypatch.setitem(sys.modules, "dask_gateway", None)  # forces ImportError

    with pytest.raises(RuntimeError, match=r"lightcone-cli\[gateway\]"):
        with cluster_for_run():
            pass  # pragma: no cover


def test_gateway_rejects_workers_without_resource_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workers not advertising cpus/memory/gpus would hang every task with
    no error — the branch must refuse loudly at startup instead."""
    monkeypatch.setenv("DASK_GATEWAY__ADDRESS", "http://proxy/services/dask-gateway/")
    log: list[str] = []
    _install_fake_gateway(monkeypatch, log, resources={})  # workers, no contract

    with pytest.raises(RuntimeError, match="resource contract"):
        with cluster_for_run():
            pass  # pragma: no cover

    assert "shutdown" in log, "failed owned cluster must still be cleaned up"


def test_executor_connects_via_gateway_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The executor side of the rendezvous: LIGHTCONE_GATEWAY_CLUSTER set →
    rejoin by name through the Gateway API, never a bare Client."""
    monkeypatch.setenv("LIGHTCONE_GATEWAY_CLUSTER", "hub.abc999")
    monkeypatch.delenv("DASK_SCHEDULER_ADDRESS", raising=False)
    log: list[str] = []
    _install_fake_gateway(monkeypatch, log, resources={RESOURCE_CPUS: 2.0})

    from snakemake_executor_plugin_dask.executor import _connect_client

    client, cluster = _connect_client()
    assert "connect(hub.abc999)" in log
    assert cluster is not None and cluster.name == "hub.abc999"
    assert isinstance(client, _FakeGatewayClient)


def test_executor_requires_some_connection_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DASK_SCHEDULER_ADDRESS", raising=False)
    monkeypatch.delenv("LIGHTCONE_GATEWAY_CLUSTER", raising=False)

    from snakemake_interface_common.exceptions import WorkflowError

    from snakemake_executor_plugin_dask.executor import _connect_client

    with pytest.raises(WorkflowError, match="LIGHTCONE_GATEWAY_CLUSTER"):
        _connect_client()


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
