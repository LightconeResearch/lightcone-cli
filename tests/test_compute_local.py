"""Real local allocation lifetimes and the boundaries around their credentials."""

from __future__ import annotations

import json
import os
import signal
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, replace
from pathlib import Path
from uuid import uuid4

import psutil
import pytest

from lightcone.engine.compute.local import LocalProvider
from lightcone.engine.compute.model import (
    ComputeError,
    Connection,
    Identity,
    Offer,
    Request,
    Resources,
)
from lightcone.engine.compute.runtime import (
    open_client,
    private_directory,
    read_private_json,
    write_private_json,
)
from lightcone.engine.project import ProjectError


@pytest.fixture
def provider(tmp_path: Path) -> LocalProvider:
    return LocalProvider(
        Connection(
            "workstation",
            str(uuid4()),
            "local",
            launch={
                "connection_root": str(tmp_path / "connections"),
                "scratch_root": str(tmp_path / "scratch"),
            },
        )
    )


def _launch(provider: LocalProvider, *, seconds: int = 60) -> Identity:
    offer = Offer("small", "workstation", Resources(1, 512 * 1024**2), 1, seconds, 60)
    return provider.launch(provider.plan(offer, Request(1, 512 * 1024**2)))


def _ready(provider: LocalProvider, identity: Identity) -> dict[str, object]:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            with provider.connect(identity, timeout=2) as client:
                client.wait_for_workers(1, timeout=2)
                return dict(client.scheduler_info())
        except (ComputeError, TimeoutError):
            if provider.inspect(identity).phase == "ended":
                pytest.fail(provider.inspect(identity).reason)
            time.sleep(0.05)
    pytest.fail("the real local scheduler did not become ready")


def _ended(provider: LocalProvider, identity: Identity, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if provider.inspect(identity).phase == "ended":
            return
        time.sleep(0.05)
    pytest.fail("the local allocation did not terminate")


def test_allocation_survives_launcher_and_borrowed_client_exit(provider: LocalProvider) -> None:
    script = """
import json, sys
from lightcone.engine.compute.local import LocalProvider
from lightcone.engine.compute.model import Connection, Offer, Request, Resources
p = LocalProvider(Connection(**json.loads(sys.argv[1])))
offer = Offer('small', 'workstation', Resources(1, 512 * 1024**2), 1, 60, 60)
print(p.launch(p.plan(offer, Request(1, 512 * 1024**2))).encode())
"""
    launched = subprocess.run(
        [sys.executable, "-c", script, json.dumps(asdict(provider.connection))],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    identity = Identity.decode(launched.stdout.strip())
    try:
        scheduler = _ready(provider, identity)
        assert str(scheduler["address"]).startswith("tls://127.0.0.1:")
        with provider.connect(identity) as client:
            addresses = client.run_on_scheduler(
                lambda dask_scheduler: [
                    sock.getsockname()[0] for sock in dask_scheduler.http_server._sockets.values()
                ]
            )
        assert addresses == ["127.0.0.1"]
        port = scheduler["services"]["dashboard"]
        with pytest.raises(urllib.error.HTTPError) as response:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=2)
        assert response.value.code == 404
        assert [s.identity for s in provider.discover()] == [identity]
        with provider.connect(identity) as client:
            assert client.submit(sum, [4, 5]).result(timeout=5) == 9
            worker_pids = client.run(os.getpid)
            assert all(
                pid not in (os.getpid(), int(identity.native_id)) for pid in worker_pids.values()
            )
        second = """
import json, sys
from lightcone.engine.compute.local import LocalProvider
from lightcone.engine.compute.model import Connection, Identity
p = LocalProvider(Connection(**json.loads(sys.argv[1])))
with p.connect(Identity.decode(sys.argv[2])) as client:
    print(client.submit(sum, [10, 11]).result(timeout=5))
"""
        result = subprocess.run(
            [
                sys.executable, "-c", second,
                json.dumps(asdict(provider.connection)), identity.encode(),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.stdout.strip() == "21"
        assert _ready(provider, identity)["id"] == scheduler["id"]
        assert provider.inspect(identity).evidence == "configured"
    finally:
        provider.terminate(identity)
    _ended(provider, identity)
    assert provider.discover() == []
    provider.terminate(identity)


def test_walltime_expires_without_a_connected_client(provider: LocalProvider) -> None:
    identity = _launch(provider, seconds=2)
    try:
        _ended(provider, identity, timeout=6)
    finally:
        provider.terminate(identity)


def test_down_kills_frozen_owner_and_workers_without_contacting_dask(
    provider: LocalProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = _launch(provider)
    try:
        _ready(provider, identity)
        children = psutil.Process(int(identity.native_id)).children(recursive=True)
        os.kill(int(identity.native_id), signal.SIGSTOP)
        monkeypatch.setattr(
            "lightcone.engine.compute.local.open_client",
            lambda *args, **kwargs: pytest.fail("termination must not contact Dask"),
        )
        assert provider.inspect(identity).phase == "active"
        provider.terminate(identity)
        _ended(provider, identity)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and any(
            child.is_running() and child.status() != psutil.STATUS_ZOMBIE for child in children
        ):
            time.sleep(0.05)
        assert all(
            not child.is_running() or child.status() == psutil.STATUS_ZOMBIE for child in children
        )
    finally:
        provider.terminate(identity)


def test_reused_pid_and_boot_identity_are_never_signalled(
    provider: LocalProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = _launch(provider)
    path = provider.root / identity.token / "identity.json"
    original = read_private_json(path)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(
                "lightcone.engine.compute.local.os.killpg",
                lambda *args: pytest.fail("a stale process identity must never be signalled"),
            )
            for changed in ({"created": original["created"] + 1}, {"boot": "another-boot"}):
                write_private_json(path, {**original, **changed})
                assert provider.inspect(identity).phase == "ended"
                provider.terminate(identity)
            unrelated = replace(identity, native_id=str(os.getpid()))
            write_private_json(
                path,
                {
                    **original,
                    "identity": unrelated.encode(),
                    "pid": os.getpid(),
                    "created": psutil.Process().create_time(),
                },
            )
            with pytest.raises(ComputeError, match="process session"):
                provider.terminate(unrelated)
    finally:
        write_private_json(path, original)
        provider.terminate(identity)


def test_connection_files_are_private_and_scheduler_identity_is_authenticated(
    provider: LocalProvider,
) -> None:
    identity = _launch(provider)
    try:
        _ready(provider, identity)
        directory = provider.root / identity.token
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        for name in ("identity.json", "connection.json", "scheduler.json", "tls-key.pem"):
            assert stat.S_IMODE((directory / name).stat().st_mode) == 0o600
        with pytest.raises(ComputeError, match="scheduler identity"):
            open_client(directory, "a-different-scheduler")
        key = directory / "tls-key.pem"
        key.chmod(0o644)
        with pytest.raises(ComputeError, match="private"):
            provider.connect(identity).__enter__()
        key.chmod(0o600)
    finally:
        provider.terminate(identity)


def test_missing_credentials_preserve_native_discovery_and_termination(
    provider: LocalProvider,
) -> None:
    identity = _launch(provider)
    try:
        _ready(provider, identity)
        (provider.root / identity.token / "tls-key.pem").unlink()
        assert provider.inspect(identity).phase == "active"
        assert provider.discover()[0].identity == identity
        with pytest.raises(ComputeError):
            with provider.connect(identity):
                pytest.fail("a connection must require credentials")
    finally:
        provider.terminate(identity)


def test_private_material_rejects_symlinks_broad_modes_and_hardlinks(tmp_path: Path) -> None:
    directory = private_directory(tmp_path / "private", create=True)
    path = directory / "test.json"
    write_private_json(path, {"test": True})
    linked = tmp_path / "linked"
    linked.symlink_to(directory, target_is_directory=True)
    with pytest.raises(ComputeError, match="plain directory"):
        read_private_json(linked / "test.json")
    alias = directory / "alias.json"
    alias.symlink_to(path)
    with pytest.raises(ComputeError):
        read_private_json(alias)
    alias.unlink()
    os.link(path, alias)
    with pytest.raises(ComputeError, match="private"):
        read_private_json(path)
    alias.unlink()
    directory.chmod(0o755)
    with pytest.raises(ComputeError, match="0700"):
        read_private_json(path)


def test_local_plan_is_one_node_finite_cooperative_and_does_not_allocate(
    provider: LocalProvider,
) -> None:
    offer = Offer("small", "workstation", Resources(1, 512 * 1024**2), 1, 60, 60)
    plan = provider.plan(offer, Request(1, 512 * 1024**2))
    assert not provider.root.exists()
    assert "cooperative" in plan.details["resource_enforcement"]
    with pytest.raises(ComputeError, match="one execution node"):
        provider.plan(offer, Request(1, 512 * 1024**2, num_nodes=2))
    with pytest.raises(ComputeError, match="finite time"):
        provider.plan(replace(offer, default_seconds=0), Request(1, 512 * 1024**2))


def test_os_temporary_directory_alias_is_resolved_but_configured_paths_stay_strict(
    provider: LocalProvider, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    physical = tmp_path / "physical"
    physical.mkdir(mode=0o700)
    alias = tmp_path / "os-temp"
    alias.symlink_to(physical, target_is_directory=True)
    monkeypatch.setattr("lightcone.engine.compute.local.tempfile.gettempdir", lambda: str(alias))
    connection = replace(
        provider.connection,
        launch={"connection_root": str(tmp_path / "connections")},
    )
    offer = Offer("small", "workstation", Resources(1, 512 * 1024**2), 1, 60, 60)
    request = Request(1, 512 * 1024**2)
    plan = LocalProvider(connection).plan(offer, request)
    assert plan.details["scratch_root"] == str(physical)
    private_directory(Path(plan.details["scratch_root"]) / "default", create=True)
    configured = replace(connection, launch={**connection.launch, "scratch_root": str(alias)})
    strict = LocalProvider(configured).plan(offer, request)
    assert strict.details["scratch_root"] == str(alias)
    with pytest.raises(ComputeError, match="plain directory"):
        private_directory(Path(strict.details["scratch_root"]) / "configured", create=True)


def test_login_node_refusal_precedes_process_start_even_with_leaked_allocation(
    provider: LocalProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NERSC_HOST", "perlmutter")
    monkeypatch.setenv("SLURM_JOB_ID", "123")
    monkeypatch.setattr(socket, "gethostname", lambda: "login01")
    monkeypatch.setattr(
        "lightcone.engine.compute.local.subprocess.Popen",
        lambda *args, **kwargs: pytest.fail("a login node must not spawn local compute"),
    )
    offer = Offer("small", "workstation", Resources(1, 512 * 1024**2), 1, 60, 60)
    with pytest.raises(ProjectError, match="compute"):
        provider.plan(offer, Request(1, 512 * 1024**2))
    assert not provider.root.exists()
