"""The registry is read-only, and attached clusters outlive their clients."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import dask.config
import psutil
import pytest

from lightcone.engine import clusters
from lightcone.engine.project import ProjectError


@pytest.fixture
def registry(tmp_path, monkeypatch):
    path = tmp_path / "clusters"
    path.mkdir()
    monkeypatch.setattr(clusters, "registry_root", lambda: path)
    return path


def record(registry, backend="slurm", suffix="aaaa", **overrides):
    directory = registry / f"20260925-120000-{suffix}"
    directory.mkdir(exist_ok=True)
    data = {
        "format": clusters.FORMAT,
        "id": directory.name,
        "backend": backend,
        "label": f"Example {suffix}",
        "workers": {**clusters._probe(str(registry)), "image": None},
        "tls": {"ca": "tls/cert.pem", "cert": "tls/cert.pem", "key": "tls/key.pem"},
        "local": {"host": socket.gethostname(), "pid": 123, "worker": 456},
        "slurm": {"job": "12345"},
        "gateway": {"name": "alice.cluster", "address": "https://gateway.example"},
        **overrides,
    }
    (directory / "cluster.json").write_text(json.dumps(data))
    return clusters.Record(directory, data)


def scheduler_file(record):
    (record.directory / "scheduler.json").write_text(json.dumps({"address": "tls://127.0.0.1:1"}))


def test_registry_skips_foreign_malformed_and_invalid_directories(registry, monkeypatch):
    expected = record(registry)
    record(registry, suffix="bbbb", format="future")
    record(registry, suffix="cccc", slurm={"job": []})
    record(registry, suffix="dddd", tls={"ca": "/outside", "cert": "a", "key": "b"})
    bad = registry / "20260925-120000-eeee"
    bad.mkdir()
    (bad / "cluster.json").write_text("{")
    unrelated = registry / "not-a-cluster"
    unrelated.mkdir()
    (unrelated / "cluster.json").write_text(json.dumps(expected.data))
    assert [item.id for item in clusters._records()] == [expected.id]
    assert len(list(registry.iterdir())) == 6


@pytest.mark.parametrize(
    ("status", "has_file", "expected"),
    [
        ("PENDING", False, "queued"),
        ("RUNNING", False, "starting"),
        ("CONFIGURING", True, "running"),
        ("COMPLETING", True, None),
        (None, True, None),
    ],
)
def test_slurm_backend_decides_liveness(registry, monkeypatch, status, has_file, expected):
    item = record(registry)
    if has_file:
        scheduler_file(item)
    monkeypatch.setattr(
        clusters, "_slurm_states", lambda: {"12345": (status, "soon")} if status else {}
    )
    selected = clusters.attached_cluster(registry)
    assert (selected.state if selected else None) == expected


def test_queue_snapshot_is_batched_and_ambiguity_refuses(registry, monkeypatch):
    record(registry)
    record(registry, suffix="bbbb")
    command = MagicMock(
        return_value=SimpleNamespace(stdout="12345|PENDING|2026-09-25T15:00|1:00\n")
    )
    monkeypatch.setattr(clusters.subprocess, "run", command)
    with pytest.raises(ProjectError, match="Several clusters.*aaaa.*bbbb.*Stop all but one"):
        clusters.attached_cluster(registry)
    command.assert_called_once()
    assert command.call_args.args[0] == ["squeue", "--me", "--noheader", "--format=%i|%T|%S|%L"]


def test_unavailable_backend_does_not_make_a_stale_file_live(registry, monkeypatch):
    scheduler_file(record(registry))
    monkeypatch.setattr(clusters, "_slurm_states", MagicMock(side_effect=FileNotFoundError))
    assert clusters.attached_cluster(registry) is None


@pytest.mark.parametrize("field", ["host", "pid_started", "cwd", "zombie"])
def test_local_selection_requires_process_identity(registry, monkeypatch, field):
    item = record(registry, backend="local")
    item.data["local"]["pid_started"] = 42
    process = MagicMock()
    process.create_time.return_value = 43 if field == "pid_started" else 42
    process.cwd.return_value = "/elsewhere" if field == "cwd" else str(item.directory)
    process.status.return_value = (
        psutil.STATUS_ZOMBIE if field == "zombie" else psutil.STATUS_RUNNING
    )
    monkeypatch.setattr(clusters.psutil, "Process", lambda pid: process)
    if field == "host":
        item.data["local"]["host"] = "another-machine"
    (item.directory / "cluster.json").write_text(json.dumps(item.data))
    scheduler_file(item)
    assert clusters.attached_cluster(registry) is None


def test_legacy_process_requires_exact_module_and_scheduler_file(registry, monkeypatch):
    item = record(registry, backend="local")
    process = MagicMock()
    process.cwd.return_value = str(item.directory)
    process.cmdline.return_value = [
        sys.executable,
        "-m",
        "distributed.cli.dask_scheduler",
        "--scheduler-file",
        str(item.directory / "scheduler.json"),
    ]
    monkeypatch.setattr(clusters.psutil, "Process", lambda pid: process)
    assert clusters._local_process(item, "pid")
    process.cmdline.return_value[-1] = "/other/scheduler.json"
    assert not clusters._local_process(item, "pid")


@pytest.mark.parametrize("incompatible", ["address", "image", "containerized", "missing-image"])
def test_gateway_compatibility_filters_before_connect(registry, monkeypatch, incompatible):
    item = record(registry, backend="gateway")
    item.data["workers"]["image"] = "hub:image"
    if incompatible == "address":
        item.data["gateway"]["address"] = "https://elsewhere"
    if incompatible == "image":
        item.data["workers"]["image"] = "hub:other"
    if incompatible != "missing-image":
        monkeypatch.setenv("JUPYTER_IMAGE_SPEC", "hub:image")
    else:
        monkeypatch.delenv("JUPYTER_IMAGE_SPEC", raising=False)
        monkeypatch.delenv("JUPYTER_IMAGE", raising=False)
    monkeypatch.setattr(
        clusters.project,
        "mode",
        lambda root: "containerized" if incompatible == "containerized" else "direct",
    )
    (item.directory / "cluster.json").write_text(json.dumps(item.data))
    monkeypatch.setattr(
        clusters, "_gateway_states", MagicMock(side_effect=AssertionError("not queried"))
    )
    with dask.config.set({"gateway.address": "https://gateway.example"}):
        assert clusters.attached_cluster(registry) is None


@pytest.mark.parametrize(
    "status,expected",
    [("PENDING", "starting"), ("RUNNING", "running"), ("STOPPING", None), (None, None)],
)
def test_gateway_liveness(registry, monkeypatch, status, expected):
    item = record(registry, backend="gateway")
    item.data["workers"]["image"] = "hub:image"
    (item.directory / "cluster.json").write_text(json.dumps(item.data))
    monkeypatch.setenv("JUPYTER_IMAGE_SPEC", "hub:image")
    monkeypatch.setattr(
        clusters, "_gateway_states", lambda: {"alice.cluster": status} if status else {}
    )
    with dask.config.set({"gateway.address": "https://gateway.example/"}):
        selected = clusters.attached_cluster(registry)
    assert (selected.state if selected else None) == expected


def test_queued_refuses_with_estimate_and_never_connects(registry, monkeypatch):
    item = replace(record(registry), state="queued", start_estimate="tomorrow")
    connected = MagicMock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr("distributed.Client", connected)
    with pytest.raises(ProjectError, match="queued.*tomorrow.*Run again"):
        with clusters.client(item, registry):
            pytest.fail("yielded queued cluster")
    connected.assert_not_called()


def test_startup_wait_is_bounded(registry, monkeypatch):
    item = replace(record(registry), state="starting")
    monkeypatch.setattr(clusters, "_WAIT", 0)
    with pytest.raises(ProjectError, match="did not start.*Check its logs"):
        with clusters.client(item, registry):
            pytest.fail("yielded starting cluster")


def test_scheduler_disappearing_does_not_wait_forever(registry, monkeypatch):
    item = replace(record(registry), state="running")
    monkeypatch.setattr(
        "distributed.Client", MagicMock(side_effect=AssertionError("must not connect"))
    )
    with pytest.raises(ProjectError, match="scheduler file is missing"):
        with clusters.client(item, registry):
            pytest.fail("yielded missing scheduler")


@pytest.mark.parametrize("mismatch", ["lightcone", "distributed", "python", "project"])
def test_probe_checks_every_worker(registry, mismatch):
    item = record(registry)
    report = clusters._probe(str(registry))
    other = {**report, mismatch: False if mismatch == "project" else "different"}
    connected = MagicMock()
    connected.run.return_value = {"worker-1": report, "worker-2": other}
    with pytest.raises(ProjectError, match="worker-2.*(Replace|Move)"):
        clusters._verify(connected, item, registry)


@pytest.fixture
def gateway(monkeypatch):
    gateway = MagicMock()
    gateway.__aenter__.return_value = gateway
    gateway.get_cluster = AsyncMock(
        return_value=SimpleNamespace(
            scheduler_address="gateway://example/alice.cluster",
            security=object(),
            status=SimpleNamespace(name="RUNNING"),
        )
    )
    monkeypatch.setattr(clusters, "_gateway_type", lambda: lambda **kwargs: gateway)
    return gateway


def test_gateway_uses_native_credentials_and_only_closes_client(registry, monkeypatch, gateway):
    item = replace(record(registry, backend="gateway"), state="running")
    report = gateway.get_cluster.return_value
    connected = MagicMock()
    constructor = MagicMock(return_value=connected)
    monkeypatch.setattr("distributed.Client", constructor)
    connected.run.return_value = {"worker-1": clusters._probe(str(registry))}
    with pytest.raises(ValueError, match="recipe failed"):
        with clusters.client(item, registry):
            raise ValueError("recipe failed")
    gateway.get_cluster.assert_awaited_once_with("alice.cluster")
    constructor.assert_called_once_with(
        report.scheduler_address, security=report.security, timeout=30, set_as_default=False
    )
    connected.wait_for_workers.assert_called_once_with(1, timeout=120)
    connected.close.assert_called_once()
    gateway.__aexit__.assert_awaited_once()
    gateway.connect.assert_not_called()
    gateway.stop_cluster.assert_not_called()
    connected.retire_workers.assert_not_called()


def test_gateway_timeout_closes_connection_without_shutdown(registry, monkeypatch, gateway):
    item = replace(record(registry, backend="gateway"), state="running")
    monkeypatch.setattr(clusters, "_CONNECT_TIMEOUT", 0.01)

    async def pending(name):
        await asyncio.sleep(1)

    gateway.get_cluster.side_effect = pending
    with pytest.raises(ProjectError, match="could not be reached"):
        with clusters.client(item, registry):
            pytest.fail("yielded pending cluster")
    gateway.__aexit__.assert_awaited_once()
    gateway.connect.assert_not_called()
    gateway.stop_cluster.assert_not_called()


def test_gateway_states_use_native_async_lifecycle(gateway):
    gateway.list_clusters = AsyncMock(
        return_value=[SimpleNamespace(name="alice.cluster", status=SimpleNamespace(name="RUNNING"))]
    )
    assert clusters._gateway_states() == {"alice.cluster": "RUNNING"}
    gateway.list_clusters.assert_awaited_once_with(status=["pending", "running", "stopping"])
    gateway.__aexit__.assert_awaited_once()


def test_missing_gateway_dependency_is_actionable(monkeypatch):
    monkeypatch.setitem(sys.modules, "dask_gateway", None)
    with pytest.raises(ProjectError, match=r"lightcone-cli\[gateway\]"):
        clusters._gateway_type()


def test_gateway_report_credentials_survive_api_cleanup(registry, gateway):
    """Pin the optional client's public API, including its real TLS security."""
    api = pytest.importorskip("dask_gateway.client")
    from distributed import Security

    generated = Security.temporary()
    gateway.get_cluster.return_value = api.ClusterReport(
        name="alice.cluster",
        options={},
        status=api.ClusterStatus.RUNNING,
        scheduler_address="gateway://example/alice.cluster",
        dashboard_link=None,
        start_time=None,
        stop_time=None,
        tls_cert=generated.tls_ca_file,
        tls_key=generated.tls_client_key,
    )
    address, security = clusters._gateway_connection(record(registry, backend="gateway"))
    gateway.__aexit__.assert_awaited_once()
    assert address == "gateway://example/alice.cluster"
    assert security.get_connection_args("client")["require_encryption"]


def test_gateway_outage_is_unknown(registry, monkeypatch):
    item = record(registry, backend="gateway")
    monkeypatch.setattr(clusters, "_gateway_states", MagicMock(side_effect=ConnectionError))
    assert clusters._states([item])[0].state == "unknown"


@pytest.mark.parametrize("key", ["lightcone", "distributed", "python"])
def test_record_version_mismatch_refuses_before_connect(registry, gateway, key):
    item = replace(record(registry, backend="gateway"), state="running")
    item.data["workers"][key] = "obsolete"
    with pytest.raises(ProjectError, match=f"{key} obsolete.*Replace"):
        with clusters.client(item, registry):
            pytest.fail("yielded obsolete cluster")
    gateway.get_cluster.assert_not_called()


def test_worker_wait_failure_closes_client(registry, monkeypatch, gateway):
    item = replace(record(registry, backend="gateway"), state="running")
    connected = MagicMock()
    connected.wait_for_workers.side_effect = TimeoutError("no workers arrived")
    monkeypatch.setattr("distributed.Client", MagicMock(return_value=connected))
    with pytest.raises(ProjectError, match="no workers arrived"):
        with clusters.client(item, registry):
            pytest.fail("yielded empty cluster")
    connected.close.assert_called_once()
    connected.retire_workers.assert_not_called()


def test_real_tls_cluster_survives_two_runs(registry):
    """The writer's actual record and command lines work across processes."""
    from distributed import Security

    item = record(registry, backend="local")
    security = Security.temporary()
    tls = item.directory / "tls"
    tls.mkdir()
    (tls / "cert.pem").write_text(security.tls_ca_file)
    (tls / "key.pem").write_text(security.tls_client_key)
    common = [
        "--scheduler-file",
        str(item.directory / "scheduler.json"),
        "--protocol",
        "tls",
        "--tls-ca-file",
        str(tls / "cert.pem"),
        "--tls-cert",
        str(tls / "cert.pem"),
        "--tls-key",
        str(tls / "key.pem"),
    ]
    argv = {
        "pid": [
            "distributed.cli.dask_scheduler",
            *common,
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--no-dashboard",
        ],
        "worker": [
            "distributed.cli.dask_worker",
            *common,
            "--nthreads",
            "1",
            "--nworkers",
            "1",
            "--no-nanny",
            "--no-dashboard",
            "--memory-limit",
            "0",
            "--death-timeout",
            "20",
        ],
    }
    processes = []
    with (item.directory / "test.log").open("w") as log:
        try:
            for key, args in argv.items():
                process = subprocess.Popen(
                    [sys.executable, "-m", *args],
                    cwd=item.directory,
                    stdout=log,
                    stderr=log,
                    env=os.environ.copy(),
                )
                processes.append(process)
                item.data["local"][key] = process.pid
                item.data["local"][f"{key}_started"] = psutil.Process(process.pid).create_time()
            path = item.directory / "cluster.json"
            path.write_text(json.dumps(item.data))
            initial = path.read_bytes()
            for number in (1, 2):
                selected = clusters.attached_cluster(registry)
                assert selected is not None
                with clusters.client(selected, registry) as connected:
                    assert (
                        connected.submit(sum, [number, 2], key=f"run-{number}").result()
                        == number + 2
                    )
                assert all(process.poll() is None for process in processes)
                assert path.read_bytes() == initial
        finally:
            for process in processes:
                process.terminate()
            for process in processes:
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
