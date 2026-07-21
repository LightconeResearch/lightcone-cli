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
        "LIGHTCONE_WORKER_IMAGE",
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
    def __init__(
        self,
        resources: dict[str, float] | None,
        worker_image: str | None = None,
        wait_error: Exception | None = None,
    ) -> None:
        self._resources = resources
        self._worker_image = worker_image
        self._wait_error = wait_error

    def scheduler_info(self) -> dict[str, object]:
        if self._resources is None:
            return {"workers": {}}
        return {"workers": {"w0": {"resources": self._resources}}}

    def wait_for_workers(self, n_workers: int, timeout: int) -> None:
        if self._wait_error is not None:
            raise self._wait_error

    def run_on_scheduler(self, fn):  # noqa: ANN001, ANN202
        # The real client executes fn inside the scheduler pod, where the
        # deployment injects LIGHTCONE_WORKER_IMAGE. Simulate that env.
        import os
        from unittest.mock import patch as _patch

        env = dict(os.environ)
        if self._worker_image is not None:
            env["LIGHTCONE_WORKER_IMAGE"] = self._worker_image
        else:
            env.pop("LIGHTCONE_WORKER_IMAGE", None)
        with _patch.dict("os.environ", env, clear=True):
            return fn()

    def close(self) -> None:
        pass


class _FakeGatewayCluster:
    def __init__(
        self,
        name: str,
        log: list[str],
        resources: dict[str, float] | None,
        worker_image: str | None = None,
        wait_error: Exception | None = None,
    ) -> None:
        self.name = name
        self.dashboard_link = f"http://hub/services/dask-gateway/{name}/status"
        self._log = log
        self._resources = resources
        self._worker_image = worker_image
        self._wait_error = wait_error

    def adapt(self, minimum: int, maximum: int) -> None:
        self._log.append(f"adapt({minimum},{maximum})")

    def get_client(self) -> _FakeGatewayClient:
        return _FakeGatewayClient(
            self._resources, self._worker_image, self._wait_error
        )

    def shutdown(self) -> None:
        self._log.append("shutdown")

    def close(self) -> None:
        self._log.append("close")


#: A worker resource set satisfying the deployment contract: cpus AND
#: memory must be advertised together (either alone hangs some rules).
_GOOD_RESOURCES = {RESOURCE_CPUS: 2.0, RESOURCE_MEMORY: 4e9}


def _install_fake_gateway(
    monkeypatch: pytest.MonkeyPatch,
    log: list[str],
    resources: dict[str, float] | None = None,
    worker_image: str | None = None,
    connect_error: Exception | None = None,
    init_error: Exception | None = None,
    new_cluster_error: Exception | None = None,
    wait_error: Exception | None = None,
):
    """Inject a fake ``dask_gateway`` module; return it for inspection.

    ``new_cluster`` logs the options it was called with and returns a
    fresh cluster named ``hub.new1`` — the create/cull default path.
    ``connect`` returns a cluster with the given name — the attach
    path. *connect_error* / *init_error* / *new_cluster_error* simulate
    the real client's failure modes (stale cluster name; no gateway
    address configured; rejected cluster options), which raise
    non-RuntimeError types. *wait_error* makes the first
    ``wait_for_workers`` call fail (unpullable image / unschedulable
    pool).
    """
    import sys
    import types

    class _FakeGateway:
        def __init__(self) -> None:
            if init_error is not None:
                raise init_error

        def new_cluster(
            self, shutdown_on_close: bool = False, **options: object
        ) -> _FakeGatewayCluster:
            log.append(
                "new_cluster("
                + ",".join(f"{k}={v}" for k, v in sorted(options.items()))
                + f";shutdown_on_close={shutdown_on_close})"
            )
            if new_cluster_error is not None:
                raise new_cluster_error
            return _FakeGatewayCluster(
                "hub.new1", log, resources, worker_image, wait_error
            )

        def connect(self, name: str) -> _FakeGatewayCluster:
            log.append(f"connect({name})")
            if connect_error is not None:
                raise connect_error
            return _FakeGatewayCluster(name, log, resources, worker_image)

    module = types.ModuleType("dask_gateway")
    module.Gateway = _FakeGateway  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "dask_gateway", module)
    return module


def test_gateway_env_creates_run_scoped_cluster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ambient DASK_GATEWAY__ADDRESS (a hub pod) routes to the gateway
    branch, which creates a run-scoped cluster, scales it adaptively up
    to max_workers, and shuts it down when the run finishes — the PRD
    create/cull lifecycle."""
    monkeypatch.setenv("DASK_GATEWAY__ADDRESS", "http://proxy/services/dask-gateway/")
    log: list[str] = []
    _install_fake_gateway(monkeypatch, log, resources=dict(_GOOD_RESOURCES))

    with cluster_for_run(max_workers=8) as env:
        assert env == {"LIGHTCONE_GATEWAY_CLUSTER": "hub.new1"}
        assert "new_cluster(;shutdown_on_close=True)" in log
        assert "adapt(1,8)" in log
        assert "shutdown" not in log

    assert "shutdown" in log, "run-scoped clusters are culled on exit"


def test_gateway_creates_with_project_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The project's resolved worker image is passed as the `image`
    cluster option — the whole point of create-per-run: every run picks
    up the freshly built image."""
    monkeypatch.setenv("DASK_GATEWAY__ADDRESS", "http://proxy/services/dask-gateway/")
    log: list[str] = []
    _install_fake_gateway(monkeypatch, log, resources=dict(_GOOD_RESOURCES))

    with cluster_for_run(expected_worker_image="reg.example/lc-proj:abc123"):
        pass

    assert (
        "new_cluster(image=reg.example/lc-proj:abc123;shutdown_on_close=True)"
        in log
    )


def test_gateway_creation_failure_names_the_image_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deployment without an `image` cluster option rejects creation
    with a dask-gateway error type; the message must say what to fix."""
    monkeypatch.setenv("DASK_GATEWAY__ADDRESS", "http://proxy/services/dask-gateway/")
    log: list[str] = []
    _install_fake_gateway(
        monkeypatch,
        log,
        new_cluster_error=ValueError("Unknown fields ['image']"),
    )

    with pytest.raises(RuntimeError, match="image") as exc:
        with cluster_for_run(expected_worker_image="reg.example/lc-proj:abc"):
            pass  # pragma: no cover
    assert "cluster option" in str(exc.value)


def test_gateway_default_bound_is_one_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASK_GATEWAY__ADDRESS", "http://proxy/services/dask-gateway/")
    log: list[str] = []
    _install_fake_gateway(monkeypatch, log, resources=dict(_GOOD_RESOURCES))

    with cluster_for_run():
        pass

    assert "adapt(1,1)" in log


def test_gateway_worker_wait_timeout_raises_and_culls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No worker within the deadline (unpullable image, no capacity) must
    fail with guidance — and still shut the created cluster down."""
    monkeypatch.setenv("DASK_GATEWAY__ADDRESS", "http://proxy/services/dask-gateway/")
    log: list[str] = []
    _install_fake_gateway(
        monkeypatch,
        log,
        resources=dict(_GOOD_RESOURCES),
        wait_error=TimeoutError("timed out"),
    )

    with pytest.raises(RuntimeError, match="worker became ready") as exc:
        with cluster_for_run(expected_worker_image="reg.example/lc-proj:abc"):
            pass  # pragma: no cover
    assert "reg.example/lc-proj:abc" in str(exc.value)
    assert "shutdown" in log, "a failed creation must not leak the cluster"


def test_gateway_ignores_stale_cluster_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LIGHTCONE_GATEWAY_CLUSTER is the parent→child rendezvous, not a
    user knob: a value lingering in the ambient environment must not
    redirect lc to some old cluster — on a hub it still creates a fresh
    run-scoped cluster, and off-hub it doesn't trigger the gateway
    branch at all."""
    monkeypatch.setenv("LIGHTCONE_GATEWAY_CLUSTER", "hub.stale99")
    monkeypatch.setenv("DASK_GATEWAY__ADDRESS", "http://proxy/services/dask-gateway/")
    log: list[str] = []
    _install_fake_gateway(monkeypatch, log, resources=dict(_GOOD_RESOURCES))

    with cluster_for_run() as env:
        assert env == {"LIGHTCONE_GATEWAY_CLUSTER": "hub.new1"}
        assert not any(c.startswith("connect") for c in log)

    assert "shutdown" in log


def test_gateway_stale_env_var_off_hub_takes_local_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LIGHTCONE_GATEWAY_CLUSTER", "hub.stale99")
    sentinel: dict[str, str] = {}

    @contextmanager
    def _fake_local(*, verbose: bool, local_directory: str | None = None):
        sentinel["called"] = "local"
        yield "tcp://stub:9999"

    with patch("lightcone.engine.dask_cluster._local_cluster", _fake_local):
        with cluster_for_run() as env:
            assert env == {"DASK_SCHEDULER_ADDRESS": "tcp://stub:9999"}
            assert sentinel["called"] == "local"


def test_gateway_wins_over_slurm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASK_GATEWAY__ADDRESS", "http://proxy/services/dask-gateway/")
    monkeypatch.setenv("SLURM_JOB_ID", "12345")
    log: list[str] = []
    _install_fake_gateway(monkeypatch, log, resources=dict(_GOOD_RESOURCES))

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
    no error — the branch must refuse loudly at startup instead (and
    still cull the cluster it just created)."""
    monkeypatch.setenv("DASK_GATEWAY__ADDRESS", "http://proxy/services/dask-gateway/")
    log: list[str] = []
    _install_fake_gateway(monkeypatch, log, resources={})  # workers, no contract

    with pytest.raises(RuntimeError, match="resource contract"):
        with cluster_for_run():
            pass  # pragma: no cover

    assert "shutdown" in log, "a failed creation must not leak the cluster"


def test_gateway_rejects_partial_resource_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cpus without memory is the sneaky variant: rules schedule until
    the first mem_mb rule, which then hangs forever. The startup check
    must demand cpus AND memory together."""
    monkeypatch.setenv("DASK_GATEWAY__ADDRESS", "http://proxy/services/dask-gateway/")
    log: list[str] = []
    _install_fake_gateway(monkeypatch, log, resources={RESOURCE_CPUS: 2.0})

    with pytest.raises(RuntimeError, match="resource contract"):
        with cluster_for_run():
            pass  # pragma: no cover


def test_executor_connects_via_gateway_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The executor side of the rendezvous: LIGHTCONE_GATEWAY_CLUSTER set →
    rejoin by name through the Gateway API, never a bare Client."""
    monkeypatch.setenv("LIGHTCONE_GATEWAY_CLUSTER", "hub.abc999")
    monkeypatch.delenv("DASK_SCHEDULER_ADDRESS", raising=False)
    log: list[str] = []
    _install_fake_gateway(monkeypatch, log, resources=dict(_GOOD_RESOURCES))

    from snakemake_executor_plugin_dask.executor import _connect_client

    client, cluster = _connect_client()
    assert "connect(hub.abc999)" in log
    assert cluster is not None and cluster.name == "hub.abc999"
    assert isinstance(client, _FakeGatewayClient)


def test_executor_address_wins_over_gateway_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The child must mirror the parent's branch priority: an explicit
    scheduler address outranks a (possibly stale, shell-exported)
    LIGHTCONE_GATEWAY_CLUSTER. Otherwise the child silently rejoins a
    Gateway cluster the parent never verified while the scheduler the
    parent reported sits idle."""
    monkeypatch.setenv("DASK_SCHEDULER_ADDRESS", "tcp://existing:8786")
    monkeypatch.setenv("LIGHTCONE_GATEWAY_CLUSTER", "hub.stale99")
    log: list[str] = []
    _install_fake_gateway(monkeypatch, log)

    captured: dict[str, str] = {}

    class _FakeClient:
        def __init__(self, addr: str) -> None:
            captured["addr"] = addr

    monkeypatch.setattr("dask.distributed.Client", _FakeClient)

    from snakemake_executor_plugin_dask.executor import _connect_client

    client, cluster = _connect_client()
    assert captured["addr"] == "tcp://existing:8786"
    assert cluster is None
    assert not any(c.startswith("connect") for c in log)


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
