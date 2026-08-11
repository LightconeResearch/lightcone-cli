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
        "LIGHTCONE_GATEWAY_WORKER_TIMEOUT",
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

# ---------------------------------------------------------------------------
# Dask Gateway branch
# ---------------------------------------------------------------------------


def _install_fake_gateway(
    monkeypatch: pytest.MonkeyPatch,
    record: dict[str, object],
    *,
    worker_resources: dict[str, float] | None = None,
    wait_raises: bool = False,
    declared_options: dict[str, object] | None = None,
):
    """Register a fake ``dask_gateway`` module and return it."""
    import sys
    from types import SimpleNamespace

    resources = (
        worker_resources
        if worker_resources is not None
        else {"cpus": 2.0, "memory": 4e9}
    )
    declared = (
        declared_options
        if declared_options is not None
        else {
            "image": "notebook:latest",
            "worker_cores": 2,
            "worker_memory": 4.0,
            "environment": {},
        }
    )

    class _FakeClient:
        def wait_for_workers(self, n_workers: int, timeout: int) -> None:
            record["waited"] = (n_workers, timeout)
            if wait_raises:
                raise TimeoutError("no workers")

        def scheduler_info(self) -> dict[str, object]:
            return {"workers": {"w0": {"resources": resources}}}

        def close(self) -> None:
            record["client_closed"] = True

    class _FakeCluster:
        name = "hub.abc123"
        dashboard_link = "http://dash"

        def adapt(self, minimum: int, maximum: int) -> None:
            record["adapt"] = (minimum, maximum)

        def get_client(self) -> _FakeClient:
            return _FakeClient()

        def shutdown(self) -> None:
            record["shutdown"] = True

        def close(self) -> None:
            record["closed"] = True

    class _FakeGateway:
        def cluster_options(self) -> dict[str, object]:
            return dict(declared)

        def new_cluster(self, shutdown_on_close: bool = True, **options: object):
            record["shutdown_on_close"] = shutdown_on_close
            record["options"] = options
            return _FakeCluster()

    module = SimpleNamespace(Gateway=_FakeGateway)
    monkeypatch.setitem(sys.modules, "dask_gateway", module)
    return module


def test_gateway_env_takes_gateway_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    from lightcone.engine.dask_cluster import GATEWAY_CLUSTER_ENV

    monkeypatch.setenv("DASK_GATEWAY__ADDRESS", "http://proxy/services/dask-gateway")
    record: dict[str, object] = {}
    _install_fake_gateway(monkeypatch, record)

    with cluster_for_run(worker_image="reg/lc-p:abc", max_workers=4) as env:
        assert env == {GATEWAY_CLUSTER_ENV: "hub.abc123"}
        opts = record["options"]
        assert opts["image"] == "reg/lc-p:abc"  # type: ignore[index]
        assert record["adapt"] == (1, 4)
        assert "shutdown" not in record

    assert record["shutdown"] is True, "run-scoped cluster must be culled on exit"


def test_gateway_without_image_uses_deployment_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASK_GATEWAY__ADDRESS", "http://proxy/services/dask-gateway")
    record: dict[str, object] = {}
    _install_fake_gateway(monkeypatch, record)

    with cluster_for_run() as _env:
        pass

    assert "image" not in record["options"], "no image option → deployment default"  # type: ignore[operator]


def test_explicit_scheduler_address_wins_over_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASK_SCHEDULER_ADDRESS", "tcp://existing:8786")
    monkeypatch.setenv("DASK_GATEWAY__ADDRESS", "http://proxy/services/dask-gateway")

    with cluster_for_run() as env:
        assert env == {"DASK_SCHEDULER_ADDRESS": "tcp://existing:8786"}


def test_gateway_culled_when_body_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cluster must be shut down even when the run fails."""
    monkeypatch.setenv("DASK_GATEWAY__ADDRESS", "http://proxy/services/dask-gateway")
    record: dict[str, object] = {}
    _install_fake_gateway(monkeypatch, record)

    with pytest.raises(RuntimeError, match="boom"):
        with cluster_for_run():
            raise RuntimeError("boom")

    assert record["shutdown"] is True


def test_gateway_zero_workers_fails_loudly_and_culls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASK_GATEWAY__ADDRESS", "http://proxy/services/dask-gateway")
    monkeypatch.setenv("LIGHTCONE_GATEWAY_WORKER_TIMEOUT", "7")
    record: dict[str, object] = {}
    _install_fake_gateway(monkeypatch, record, wait_raises=True)

    with pytest.raises(RuntimeError, match="within 7s"):
        with cluster_for_run(worker_image="reg/lc-p:abc"):
            pass  # pragma: no cover

    assert record["waited"] == (1, 7)
    assert record["shutdown"] is True


def test_gateway_missing_resource_contract_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASK_GATEWAY__ADDRESS", "http://proxy/services/dask-gateway")
    record: dict[str, object] = {}
    _install_fake_gateway(monkeypatch, record, worker_resources={})

    with pytest.raises(RuntimeError, match="resource contract"):
        with cluster_for_run():
            pass  # pragma: no cover

    assert record["shutdown"] is True


def test_gateway_branch_active_matches_routing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lightcone.engine.dask_cluster import gateway_branch_active

    assert gateway_branch_active() is False
    monkeypatch.setenv("DASK_GATEWAY__ADDRESS", "http://proxy/services/dask-gateway")
    assert gateway_branch_active() is True
    monkeypatch.setenv("DASK_SCHEDULER_ADDRESS", "tcp://existing:8786")
    assert gateway_branch_active() is False


def test_gateway_explicit_image_beats_ambient_default() -> None:
    """The deployment injects DASK_GATEWAY__CLUSTER__OPTIONS__IMAGE
    (= the notebook image) as the client's ambient default. lc run's
    explicit ``image`` kwarg MUST override it — otherwise every cluster
    would run the notebook image instead of the one `lc build` just
    produced. Pinned against the real dask-gateway client merge logic.
    """
    pytest.importorskip("dask_gateway")
    import dask
    from dask_gateway import Gateway

    captured: dict[str, object] = {}

    async def fake_request(self, method, url, json=None, **kwargs):  # type: ignore[no-untyped-def]
        captured["cluster_options"] = (json or {}).get("cluster_options")

        class _Resp:
            async def json(self) -> dict[str, str]:
                return {"name": "hub.fake"}

        return _Resp()

    with dask.config.set({"gateway.cluster.options": {"image": "notebook:latest"}}):
        gateway = Gateway(address="http://gateway.invalid", auth="basic")
        try:
            with patch.object(Gateway, "_request", fake_request):
                gateway.submit(image="reg/lc-proj:abc123")
                assert captured["cluster_options"] == {"image": "reg/lc-proj:abc123"}

                gateway.submit()
                assert captured["cluster_options"] == {"image": "notebook:latest"}
        finally:
            gateway.close()


def test_gateway_self_provisions_worker_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """lc passes everything worker pods need through the STANDARD
    `environment` cluster option — resource contract mirrored from the
    deployment's declared worker shape, driver identity forwarded,
    image ground truth — so the deployment's options handler needs no
    lightcone-specific injection."""
    monkeypatch.setenv("DASK_GATEWAY__ADDRESS", "http://proxy/services/dask-gateway")
    monkeypatch.setenv("HOME", "/home/jovyan")
    monkeypatch.setenv("USER", "jovyan")
    monkeypatch.setenv("LOGNAME", "jovyan")
    record: dict[str, object] = {}
    _install_fake_gateway(
        monkeypatch,
        record,
        declared_options={
            "image": "notebook:latest",
            "worker_cores": 2,
            "worker_memory": 4.0,
            "environment": {"EXTRA": "kept"},
        },
    )

    with cluster_for_run(worker_image="reg/lc-p:abc"):
        pass

    env = record["options"]["environment"]  # type: ignore[index]
    assert env["DASK_DISTRIBUTED__WORKER__RESOURCES__CPUS"] == "2"
    assert env["DASK_DISTRIBUTED__WORKER__RESOURCES__MEMORY"] == str(int(4.0 * 1e9))
    assert env["DASK_DISTRIBUTED__WORKER__RESOURCES__GPUS"] == "0"
    assert env["HOME"] == "/home/jovyan"
    assert env["USER"] == "jovyan"
    assert env["LOGNAME"] == "jovyan"
    assert env["LIGHTCONE_WORKER_IMAGE"] == "reg/lc-p:abc"
    assert env["EXTRA"] == "kept", "ambient environment defaults must survive"


def test_gateway_provisions_identity_without_user_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """USER/LOGNAME must be *derived*, not merely forwarded: notebook
    pods often don't export them (their own passwd entry covers
    getpass), while the environment-agnostic worker image has NO passwd
    entry for the pod uid — so the child snakemake crashes at
    ``getpass.getuser()`` unless lc always provisions the vars.
    Regression: live run failed with ``getpwuid(): uid not found``."""
    monkeypatch.setenv("DASK_GATEWAY__ADDRESS", "http://proxy/services/dask-gateway")
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.delenv("LOGNAME", raising=False)
    record: dict[str, object] = {}
    _install_fake_gateway(
        monkeypatch,
        record,
        declared_options={"image": "notebook:latest", "environment": {}},
    )

    with cluster_for_run(worker_image="reg/lc-p:abc"):
        pass

    import getpass

    env = record["options"]["environment"]  # type: ignore[index]
    assert env["USER"] == getpass.getuser()
    assert env["LOGNAME"] == env["USER"]


def test_gateway_worker_image_env_falls_back_to_declared_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASK_GATEWAY__ADDRESS", "http://proxy/services/dask-gateway")
    record: dict[str, object] = {}
    _install_fake_gateway(monkeypatch, record)

    with cluster_for_run():  # no project image → deployment default
        pass

    env = record["options"]["environment"]  # type: ignore[index]
    assert env["LIGHTCONE_WORKER_IMAGE"] == "notebook:latest"


def test_gateway_no_environment_option_no_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deployment that doesn't expose `environment` gets no surprise
    kwarg (the server would reject it)."""
    monkeypatch.setenv("DASK_GATEWAY__ADDRESS", "http://proxy/services/dask-gateway")
    record: dict[str, object] = {}
    _install_fake_gateway(
        monkeypatch, record, declared_options={"image": "notebook:latest"}
    )

    with cluster_for_run(worker_image="reg/lc-p:abc"):
        pass

    assert "environment" not in record["options"]  # type: ignore[operator]


def test_worker_environment_memory_bytes_heuristic() -> None:
    from lightcone.engine.dask_cluster import _worker_environment

    env = _worker_environment({"worker_memory": 4294967296}, None)
    assert env["DASK_DISTRIBUTED__WORKER__RESOURCES__MEMORY"] == "4294967296"
    env = _worker_environment({"worker_memory": 4.0}, None)
    assert env["DASK_DISTRIBUTED__WORKER__RESOURCES__MEMORY"] == str(int(4e9))
