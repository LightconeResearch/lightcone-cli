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
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import psutil
import pytest
from click.testing import CliRunner

from lightcone.cli.commands import main
from lightcone.engine.compute import Compute, local, local_runtime
from lightcone.engine.compute.catalog import Catalog
from lightcone.engine.compute.local import LocalProvider
from lightcone.engine.compute.model import (
    ComputeError,
    Connection,
    Identity,
    Offer,
    Request,
    Resources,
    TimeLimits,
    UnavailableOfferError,
)
from lightcone.engine.compute.runtime import (
    configured_directory,
    open_client,
    private_directory,
    read_private_json,
    write_private_json,
)

pytestmark = pytest.mark.usefixtures("local_allocation_lock")


@pytest.fixture
def provider(tmp_path: Path) -> LocalProvider:
    return LocalProvider(
        Connection(
            namespace=str(uuid4()),
            provider="local",
            launch={
                "connection_root": str(tmp_path / "connections"),
                "scratch_root": str(tmp_path / "scratch"),
            },
        )
    )


def _launch(provider: LocalProvider, *, seconds: int = 60) -> Identity:
    offer = Offer(
        name="small", connection="workstation", resources=Resources(cpus=1, memory_gib=0.5),
        max_nodes=1, time=TimeLimits(default="1m", max="1m"),
    )
    return provider.launch(
        provider.plan(offer, Request(cpus=1, memory_bytes=512 * 1024**2, seconds=seconds))
    )


def test_singleton_survives_cli_exit_and_spans_names_and_connection_roots(
    provider: LocalProvider, tmp_path: Path,
) -> None:
    # Independent launchers compete for the same host/user lock, with distinct catalogs.
    script = """
import json, sys
from pathlib import Path
from lightcone.engine.compute import local
from lightcone.engine.compute.local import LocalProvider
from lightcone.engine.compute.model import (
    Connection, ComputeError, Offer, Request, Resources, TimeLimits,
)
local._LOCK_ROOT = Path(sys.argv[3])
p = LocalProvider(Connection.model_validate_json(sys.argv[1]))
offer = Offer(name='small', connection='workstation', resources=Resources(cpus=1, memory_gib=0.5),
              max_nodes=1, time=TimeLimits(default='1m', max='1m'))
plan = p.plan(offer, Request(cpus=1, memory_bytes=512 * 1024**2)).replace(name=sys.argv[2])
input()
try:
    print(json.dumps({'id': p.launch(plan).encode()}))
except ComputeError as exc:
    print(json.dumps({'error': str(exc)}))
"""
    other = LocalProvider(provider.connection.replace(
        namespace=str(uuid4()), launch={"connection_root": str(tmp_path / "other")},
    ))
    providers = [provider, other]
    processes = [subprocess.Popen(
        [sys.executable, "-c", script, item.connection.model_dump_json(),
         f"local-{index}", str(local._LOCK_ROOT)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ) for index, item in enumerate(providers)]
    identities: list[tuple[LocalProvider, Identity]] = []
    try:
        for process in processes:
            assert process.stdin is not None
            process.stdin.write("go\n")
            process.stdin.flush()
        results = []
        for item, process in zip(providers, processes):
            stdout, stderr = process.communicate(timeout=20)
            assert process.returncode == 0, stderr
            result = json.loads(stdout)
            results.append(result)
            if "id" in result:
                identities.append((item, Identity.decode(result["id"])))
        assert len(identities) == 1, results
        assert "already running or starting" in next(r["error"] for r in results if "error" in r)
        owner, identity = identities[0]
        _ready(owner, identity)
        with pytest.raises(ComputeError, match="already running") as conflict:
            _launch(other)
        assert identity.encode() in str(conflict.value)
        assert conflict.value.cluster_id is None
        assert str(owner.root) in str(conflict.value)
        owner.terminate(identity)
        _ended(owner, identity)
        replacement = _launch(other)
        identities.append((other, replacement))
        _ready(other, replacement)
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        for item, identity in identities:
            item.terminate(identity)


def test_allocation_lock_preserves_errors_from_the_launch_body() -> None:
    error = OSError("allocation storage is full")
    with pytest.raises(OSError) as raised, local._allocation_lock():
        raise error
    assert raised.value is error
    with local._allocation_lock():
        pass


@pytest.mark.parametrize("closed", [(0,), (1,), (2,), (0, 1, 2)])
def test_singleton_lock_survives_launch_with_closed_standard_descriptors(
    provider: LocalProvider, tmp_path: Path, closed: tuple[int, ...],
) -> None:
    result_path = tmp_path / "launched.json"
    script = """
import json, os, sys
from pathlib import Path
from lightcone.engine.compute import local
from lightcone.engine.compute.model import Connection, Offer, Request, Resources, TimeLimits
local._LOCK_ROOT = Path(sys.argv[2])
p = local.LocalProvider(Connection.model_validate_json(sys.argv[1]))
offer = Offer(name='small', connection='workstation', resources=Resources(cpus=1, memory_gib=0.5),
              max_nodes=1, time=TimeLimits(default='1m', max='1m'))
plan = p.plan(offer, Request(cpus=1, memory_bytes=512 * 1024**2))
for descriptor in json.loads(sys.argv[4]):
    os.close(descriptor)
identity = p.launch(plan)
Path(sys.argv[3]).write_text(identity.encode())
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", script, provider.connection.model_dump_json(),
             str(local._LOCK_ROOT), str(result_path), json.dumps(closed)],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, result.stderr
        identity = Identity.decode(result_path.read_text())
        _ready(provider, identity)
        with pytest.raises(ComputeError, match="already running"):
            _launch(provider)
    finally:
        for snapshot in provider.discover():
            provider.terminate(snapshot.identity)


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


def _ignoring_recipe(provider: LocalProvider, identity: Identity) -> psutil.Process:
    with provider.connect(identity) as client:
        def spawn() -> int:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                    "print('ready', flush=True); time.sleep(120)",
                ],
                stdout=subprocess.PIPE,
            )
            assert process.stdout is not None
            assert process.stdout.readline() == b"ready\n"
            process.stdout.close()
            return process.pid

        return psutil.Process(client.submit(spawn).result(timeout=5))


def test_allocation_survives_launcher_and_borrowed_client_exit(provider: LocalProvider) -> None:
    script = """
import json, sys
from pathlib import Path
from lightcone.engine.compute import local
from lightcone.engine.compute.local import LocalProvider
from lightcone.engine.compute.model import Connection, Offer, Request, Resources, TimeLimits
local._LOCK_ROOT = Path(sys.argv[2])
p = LocalProvider(Connection(**json.loads(sys.argv[1])))
offer = Offer(
    name='small', connection='workstation', resources=Resources(cpus=1, memory_gib=0.5),
    max_nodes=1, time=TimeLimits(default='1m', max='1m'),
)
print(p.launch(p.plan(offer, Request(cpus=1, memory_bytes=512 * 1024**2))).encode())
"""
    launched = subprocess.run(
        [sys.executable, "-c", script, json.dumps(provider.connection.model_dump()),
         str(local._LOCK_ROOT)],
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
                json.dumps(provider.connection.model_dump()), identity.encode(),
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


def test_builtin_allocation_can_be_reopened_in_another_process_without_a_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lightcone.engine.compute import Compute
    from lightcone.engine.compute.model import GIB

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("LC_COMPUTE_CONFIG", raising=False)
    monkeypatch.setattr("dask.system.CPU_COUNT", 1)
    monkeypatch.setattr("distributed.system.MEMORY_LIMIT", GIB)
    try:
        result = CliRunner().invoke(main, [
            "compute", "launch", "--wait", "--time", "1m", "--timeout", "20", "--json",
        ])
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        identity = Identity.decode(data["id"])
        assert data["ready"] and data["name"] == "local"
        script = """
import sys
from lightcone.engine.compute import Compute, connect
assert Compute().status(sys.argv[1], wait=True, timeout=20).ready
with connect(sys.argv[1]) as client:
    print(client.submit(sum, [4, 5]).result(timeout=5))
"""
        result = subprocess.run(
            [sys.executable, "-c", script, identity.encode()],
            capture_output=True, text=True, timeout=30, check=True,
        )
        assert result.stdout.strip() == "9"
        assert not (tmp_path / ".lightcone" / "compute.yaml").exists()
        builtin = LocalProvider(Compute().catalog.connections["local"])
        with monkeypatch.context() as other_catalog:
            other_catalog.setenv("LC_COMPUTE_CONFIG", str(tmp_path / "other.yaml"))
            with pytest.raises(ComputeError, match="already running") as conflict:
                _launch(builtin)
            command = ["env", "-u", "LC_COMPUTE_CONFIG", "lc", "compute", "down", identity.encode()]
            assert " ".join(command) in str(conflict.value)
            stopped = subprocess.run(command, capture_output=True, text=True, timeout=15)
            assert stopped.returncode == 0, stopped.stderr
    finally:
        # Discovery also covers a successful launch whose CLI output is malformed.
        for snapshot in Compute().discover()[0]:
            Compute().down(snapshot.identity.encode())
    assert Compute().status(identity.encode()).phase == "ended"
    assert Compute().discover() == ([], {})


def test_cpu_allocation_without_gpu_fields_remains_discoverable_and_stoppable(
    provider: LocalProvider,
) -> None:
    identity = _launch(provider)
    try:
        _ready(provider, identity)
        path = provider.root / identity.token / "identity.json"
        record = read_private_json(path)
        del record["gpus"], record["accelerator_name"]
        write_private_json(path, record)

        snapshot, = provider.discover()
        assert snapshot.identity == identity
        assert snapshot.resources is not None
        assert snapshot.resources.gpus == 0
        assert snapshot.resources.accelerator_name is None
        with provider.connect(identity) as client:
            assert client.submit(sum, [1, 2]).result(timeout=5) == 3
    finally:
        provider.terminate(identity)
    _ended(provider, identity)


@pytest.mark.parametrize("threads", [None, "2"])
def test_local_workers_keep_native_dask_thread_defaults_and_launch_overrides(
    provider: LocalProvider, monkeypatch: pytest.MonkeyPatch, threads: str | None,
) -> None:
    names = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS")
    for name in names:
        if threads is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, threads)
    identity = _launch(provider)
    try:
        _ready(provider, identity)
        with provider.connect(identity) as client:
            actual = client.submit(lambda: {name: os.environ.get(name) for name in names}).result(
                timeout=5,
            )
        assert actual == dict.fromkeys(names, threads or "1")
    finally:
        provider.terminate(identity)


def test_named_local_allocation_is_discovered_and_name_can_be_reused_after_down(
    provider: LocalProvider, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lightcone.engine.compute import Compute, connect

    catalog = tmp_path / "compute.yaml"
    catalog.write_text(json.dumps({
        "version": 1,
        "connections": {
            "workstation": {
                "provider": "local",
                "namespace": provider.connection.namespace,
                "launch": provider.connection.launch,
            },
        },
        "offers": [{
            "name": "small",
            "connection": "workstation",
            "resources": {"cpus": 1, "memory": 0.5},
            "max_nodes": 1,
            "time": {"default": "1m", "max": "1m"},
        }],
    }))
    monkeypatch.setenv("LC_COMPUTE_CONFIG", str(catalog))
    service = Compute()
    plan = service.plan(Request(cpus=1, memory_bytes=512 * 1024**2), name="analysis")
    identities = []
    try:
        first = service.launch(plan)
        identities.append(first)
        assert first.name == "analysis"
        assert Identity.decode(first.encode()) == first
        # A new adapter reconstructs the name from the existing native locator.
        assert [item.identity for item in LocalProvider(provider.connection).discover()] == [first]
        assert Compute().status("analysis", wait=True, timeout=20).identity == first
        with monkeypatch.context() as other_catalog:
            other_catalog.setenv("LC_COMPUTE_CONFIG", str(tmp_path / "other.yaml"))
            with pytest.raises(ComputeError, match="already running") as conflict:
                _launch(provider)
        assert str(catalog) in str(conflict.value)
        assert first.encode() in str(conflict.value)
        assert conflict.value.cluster_id is None
        with connect("analysis") as client:
            assert client.submit(sum, [7, 8]).result(timeout=5) == 15
        Compute().down("analysis")
        assert Compute().status(first.encode()).phase == "ended"
        second = Compute().launch(plan)
        identities.append(second)
        assert second.name == first.name
        assert second.encode() != first.encode()
        assert Compute().status("analysis", wait=True, timeout=20).identity == second
        assert Compute().status(first.encode()).phase == "ended"
    finally:
        for identity in identities:
            provider.terminate(identity)


def test_walltime_expires_without_a_connected_client(provider: LocalProvider) -> None:
    identity = _launch(provider, seconds=2)
    identities = [identity]
    try:
        _ended(provider, identity, timeout=6)
        # Native exit and release of the owner's last kernel descriptor can differ.
        identities.append(_launch(provider))
    finally:
        for allocation in identities:
            provider.terminate(allocation)


@pytest.mark.parametrize("ending", ["down", "walltime"])
def test_an_ended_allocation_keeps_its_record_but_not_its_secrets_or_scratch(
    provider: LocalProvider, ending: str,
) -> None:
    identity = _launch(provider, seconds=2 if ending == "walltime" else 60)
    directory = provider.root / identity.token
    scratch = Path(str(read_private_json(directory / "launch.json")["scratch"]))
    try:
        if ending == "down":
            _ready(provider, identity)
            provider.terminate(identity)
        else:
            _ended(provider, identity, timeout=6)
            assert provider.discover() == []
    finally:
        provider.terminate(identity)
    assert not (directory / "tls-key.pem").exists()
    assert not scratch.exists()
    assert provider.inspect(identity).phase == "ended"
    # A retired record is never read again, so it cannot break discovery.
    (directory / "identity.json").write_text("{")
    assert provider.discover() == []

def test_unavailable_boot_identity_refuses_before_creating_an_allocation(
    provider: LocalProvider, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable() -> str:
        raise ComputeError("cannot verify this host's boot identity")

    monkeypatch.setattr("lightcone.engine.compute.local._boot_identity", unavailable)
    with pytest.raises(ComputeError, match="boot identity"):
        _launch(provider)
    assert not provider.root.exists()


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


def test_down_drains_sigterm_ignoring_recipe_process(provider: LocalProvider) -> None:
    identity = _launch(provider)
    child: psutil.Process | None = None
    try:
        _ready(provider, identity)
        child = _ignoring_recipe(provider, identity)
        assert os.getpgid(child.pid) == int(identity.native_id)
        provider.terminate(identity)
        assert not child.is_running() or child.status() == psutil.STATUS_ZOMBIE
        assert provider.inspect(identity).phase == "ended"
        provider.terminate(identity)
    finally:
        provider.terminate(identity)
        if child is not None and child.is_running():
            child.kill()


def test_owner_shutdown_drains_recipes_without_a_waiting_cli(provider: LocalProvider) -> None:
    identity = _launch(provider)
    child: psutil.Process | None = None
    try:
        _ready(provider, identity)
        child = _ignoring_recipe(provider, identity)
        os.kill(int(identity.native_id), signal.SIGTERM)
        _ended(provider, identity, timeout=10)
        deadline = time.monotonic() + 3
        while child.is_running() and child.status() != psutil.STATUS_ZOMBIE:
            assert time.monotonic() < deadline
            time.sleep(0.05)
    finally:
        provider.terminate(identity)
        if child is not None and child.is_running():
            child.kill()


def test_termination_escalates_captured_children_when_owner_exits_first(
    provider: LocalProvider, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # Isolate the provider's drain from the runtime finalizer: this owner exits
    # immediately on SIGTERM while leaving a signal-ignoring member behind.
    script = """
import signal, subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c',
    "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
    "print('ready',flush=True); time.sleep(120)"], stdout=subprocess.PIPE)
assert child.stdout.readline() == b'ready\\n'
print(child.pid, flush=True)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
time.sleep(120)
"""
    owner = subprocess.Popen(
        [sys.executable, "-c", script], start_new_session=True, stdout=subprocess.PIPE,
    )
    assert owner.stdout is not None
    child = psutil.Process(int(owner.stdout.readline()))
    process = psutil.Process(owner.pid)
    identity = Identity(
        namespace=provider.connection.namespace, native_id=str(owner.pid), token=uuid4().hex,
    )
    monkeypatch.setattr(provider, "_record", lambda _identity: (tmp_path, {}))
    monkeypatch.setattr(
        provider, "_process", lambda *_args: process if owner.poll() is None else None,
    )
    monkeypatch.setattr("lightcone.engine.compute.local._STOP_GRACE", 0.2)
    try:
        provider.terminate(identity)
        assert owner.poll() is not None
        assert not child.is_running() or child.status() == psutil.STATUS_ZOMBIE
    finally:
        if child.is_running():
            child.kill()
        if owner.poll() is None:
            owner.kill()
        owner.wait(timeout=3)
        owner.stdout.close()


@pytest.mark.parametrize("exited", [False, True])
def test_command_line_access_denial_requires_confirmed_exit(
    provider: LocalProvider, monkeypatch: pytest.MonkeyPatch, exited: bool,
) -> None:
    def denied() -> None:
        raise psutil.AccessDenied(123)

    def wait(*, timeout: float) -> None:
        if not exited:
            raise psutil.TimeoutExpired(timeout, pid=123)

    process = SimpleNamespace(
        status=lambda: psutil.STATUS_RUNNING,
        create_time=lambda: 123,
        uids=lambda: SimpleNamespace(real=os.getuid()),
        cmdline=denied,
        wait=wait,
    )
    monkeypatch.setattr("lightcone.engine.compute.local.psutil.Process", lambda _pid: process)
    monkeypatch.setattr("lightcone.engine.compute.local._boot_identity", lambda: "test-boot")
    identity = Identity(namespace=provider.connection.namespace, native_id="123", token=uuid4().hex)
    record = {"created": 123, "boot": "test-boot"}
    if exited:
        assert provider._process(identity, provider.root, record) is None
    else:
        with pytest.raises(ComputeError, match="cannot verify"):
            provider._process(identity, provider.root, record)


def test_failed_spawn_and_unpublished_launch_do_not_hide_healthy_allocations(
    provider: LocalProvider, monkeypatch: pytest.MonkeyPatch,
) -> None:
    popen = subprocess.Popen
    with monkeypatch.context() as patch:
        def fail(argv: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
            if "lightcone.engine.compute.local_runtime" in argv:
                raise OSError("configured interpreter cannot execute")
            return popen(argv, **kwargs)

        patch.setattr("lightcone.engine.compute.local.subprocess.Popen", fail)
        with pytest.raises(ComputeError, match="cannot execute"):
            _launch(provider)
    assert list(provider.root.iterdir()) == []
    assert list(Path(provider.connection.launch["scratch_root"]).iterdir()) == []
    # A failed spawn must release the singleton lock as well as its private files.
    identity = _launch(provider)
    try:
        _ready(provider, identity)
        # A launcher interrupted before identity publication can also leave a
        # directory. This is not a published allocation or a discovery error.
        interrupted = private_directory(provider.root / uuid4().hex, create=True)
        write_private_json(interrupted / "launch.json", {"identity": ""})
        assert [snapshot.identity for snapshot in provider.discover()] == [identity]
    finally:
        provider.terminate(identity)


def test_failed_initial_launch_write_removes_unpublished_files_and_releases_lock(
    provider: LocalProvider, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(path: Path, value: dict[str, Any]) -> None:
        if path.name == "launch.json":
            raise OSError("allocation storage is full")
        write_private_json(path, value)

    monkeypatch.setattr(local, "write_private_json", fail)
    with pytest.raises(ComputeError, match="cannot start.*storage is full"):
        _launch(provider)
    assert list(provider.root.iterdir()) == []
    assert list(Path(provider.connection.launch["scratch_root"]).iterdir()) == []
    with local._allocation_lock():
        pass


def test_interrupted_launch_kills_the_unreturned_owner_and_releases_lock(
    provider: LocalProvider, monkeypatch: pytest.MonkeyPatch,
) -> None:
    children: list[subprocess.Popen[bytes]] = []
    popen = subprocess.Popen

    def capture(argv: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        process = popen(argv, **kwargs)
        if "lightcone.engine.compute.local_runtime" in argv:
            children.append(process)
        return process

    def interrupt(path: Path, value: dict[str, Any]) -> None:
        if path.name == "launch.json" and value["identity"]:
            raise KeyboardInterrupt
        write_private_json(path, value)

    monkeypatch.setattr(local.subprocess, "Popen", capture)
    monkeypatch.setattr(local, "write_private_json", interrupt)
    try:
        with pytest.raises(KeyboardInterrupt):
            _launch(provider)
        assert len(children) == 1
        assert children[0].poll() is not None
        assert provider.discover() == []
        with local._allocation_lock():
            pass
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)


@pytest.mark.parametrize("publication", ["identity.json", "launch.json"])
def test_owner_exits_when_launcher_is_killed_before_startup_commit(
    provider: LocalProvider, tmp_path: Path, publication: str,
) -> None:
    marker = tmp_path / "paused.json"
    script = """
import json, sys, time
from pathlib import Path
from lightcone.engine.compute import local
from lightcone.engine.compute.model import Connection, Offer, Request, Resources, TimeLimits
local._LOCK_ROOT = Path(sys.argv[2])
write = local.write_private_json
def pause(path, value):
    if path.name == sys.argv[4] and (path.name != 'launch.json' or value['identity']):
        record = value if path.name == 'identity.json' else local.read_private_json(
            path.parent / 'identity.json')
        checkpoint = {'pid': record['pid'], 'directory': str(path.parent)}
        marker = Path(sys.argv[3])
        pending = marker.with_suffix('.pending')
        pending.write_text(json.dumps(checkpoint))
        pending.replace(marker)
        time.sleep(60)
    write(path, value)
local.write_private_json = pause
p = local.LocalProvider(Connection.model_validate_json(sys.argv[1]))
offer = Offer(name='small', connection='workstation', resources=Resources(cpus=1, memory_gib=0.5),
              max_nodes=1, time=TimeLimits(default='1m', max='1m'))
p.launch(p.plan(offer, Request(cpus=1, memory_bytes=512 * 1024**2)))
"""
    launcher = subprocess.Popen(
        [sys.executable, "-c", script, provider.connection.model_dump_json(),
         str(local._LOCK_ROOT), str(marker), publication],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    owner: psutil.Process | None = None
    try:
        deadline = time.monotonic() + 15
        while not marker.exists():
            if launcher.poll() is not None:
                pytest.fail(launcher.communicate()[1].decode(errors="replace"))
            assert time.monotonic() < deadline, "launcher did not reach the publication checkpoint"
            time.sleep(0.05)
        checkpoint = json.loads(marker.read_text())
        owner = psutil.Process(checkpoint["pid"])
        launcher.kill()
        launcher.communicate(timeout=5)
        deadline = time.monotonic() + 5
        while owner.is_running() and owner.status() != psutil.STATUS_ZOMBIE:
            assert time.monotonic() < deadline, "unpublished owner retained the singleton lock"
            time.sleep(0.05)
        assert not (Path(checkpoint["directory"]) / "connection.json").exists()
        with local._allocation_lock():
            pass
    finally:
        if launcher.poll() is None:
            launcher.kill()
        launcher.communicate(timeout=5)
        if owner is not None and owner.is_running():
            owner.kill()
        for snapshot in provider.discover():
            provider.terminate(snapshot.identity)


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
            write_private_json(path, {**original, "boot": "another-boot"})
            assert provider.discover() == []
            with pytest.raises(ComputeError, match="different host or boot"):
                provider.inspect(identity)
            with pytest.raises(ComputeError, match="different host or boot"):
                provider.terminate(identity)
            with (
                pytest.raises(ComputeError, match="different host or boot"),
                provider.connect(identity),
            ):
                pytest.fail("a different boot must not attach to a scheduler")
            write_private_json(path, original)
            with patch.context() as reused:
                reused.setattr(
                    psutil.Process, "cmdline",
                    lambda _process: [
                        sys.executable, "-P", "-m", "lightcone.engine.compute.local_runtime",
                        str(provider.root / uuid4().hex),
                    ],
                )
                assert provider.inspect(identity).phase == "ended"
                assert provider.discover() == []
                provider.terminate(identity)
            unrelated = identity.replace(native_id=str(os.getpid()))
            write_private_json(
                path,
                {
                    **original,
                    "identity": unrelated.encode(),
                    "pid": os.getpid(),
                },
            )
            assert provider.inspect(unrelated).phase == "ended"
            provider.terminate(unrelated)
    finally:
        write_private_json(path, original)
        provider.terminate(identity)


def test_shared_home_does_not_claim_foreign_allocations_ended_or_terminated(
    provider: LocalProvider, monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _launch(provider)
    try:
        _ready(provider, identity)
        remote_boot = str(uuid4())
        with monkeypatch.context() as remote:
            remote.setattr(socket, "gethostname", lambda: identity.host + "-other")
            remote.setattr("lightcone.engine.compute.local._boot_identity", lambda: remote_boot)
            remote.setattr(
                "lightcone.engine.compute.local.os.killpg",
                lambda *args: pytest.fail("another host cannot signal the allocation"),
            )
            assert provider.discover() == []
            with pytest.raises(ComputeError, match="different host or boot"):
                provider.inspect(identity)
            with pytest.raises(ComputeError, match="different host or boot"):
                provider.terminate(identity)
            with (
                pytest.raises(ComputeError, match="different host or boot"),
                provider.connect(identity),
            ):
                pytest.fail("another host cannot borrow the allocation")
        assert provider.inspect(identity).phase == "active"
        assert [item.identity for item in provider.discover()] == [identity]
    finally:
        provider.terminate(identity)


def test_local_identity_survives_hostname_and_wall_clock_changes(
    provider: LocalProvider, monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _launch(provider)
    try:
        _ready(provider, identity)
        created = psutil.Process.create_time
        booted = psutil.boot_time()
        monkeypatch.setattr(socket, "gethostname", lambda: identity.host + "-renamed")
        monkeypatch.setattr(psutil.Process, "create_time", lambda process: created(process) + 3600)
        monkeypatch.setattr(psutil, "boot_time", lambda: booted + 3600)
        assert provider.inspect(identity).phase == "active"
        assert [item.identity for item in provider.discover()] == [identity]
        with provider.connect(identity) as client:
            assert client.submit(sum, [1, 2]).result(timeout=5) == 3
    finally:
        provider.terminate(identity)
    assert provider.inspect(identity).phase == "ended"


def test_local_bootstrap_does_not_import_modules_from_the_launch_directory(
    provider: LocalProvider, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    launch_directory = tmp_path / "project"
    launch_directory.mkdir()
    (launch_directory / "signal.py").write_text(
        "raise RuntimeError('project module shadowed stdlib')"
    )
    monkeypatch.chdir(launch_directory)
    identity = _launch(provider)
    try:
        _ready(provider, identity)
        with provider.connect(identity) as client:
            paths = client.run(lambda: __import__("signal").__file__)
            assert all(Path(path).parent != launch_directory for path in paths.values())
    finally:
        provider.terminate(identity)


def test_macos_boot_identity_uses_the_native_boot_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    from lightcone.engine.compute.local import _boot_identity

    value = str(uuid4())
    calls = []

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        assert kwargs["timeout"] == 5
        return subprocess.CompletedProcess(argv, 0, value.upper() + "\n")

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", run)
    assert _boot_identity() == value
    assert calls == [["/usr/sbin/sysctl", "-n", "kern.bootsessionuuid"]]


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


def test_a_starting_allocation_says_to_wait(provider: LocalProvider) -> None:
    identity = _launch(provider)
    connection = provider.root / identity.token / "connection.json"
    try:
        _ready(provider, identity)
        # The owner publishes this file once its scheduler is up.
        connection.rename(connection.with_suffix(".held"))
        with pytest.raises(ComputeError, match="has not started yet"):
            with provider.connect(identity):
                pytest.fail("a starting allocation must not connect")
        connection.with_suffix(".held").rename(connection)
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
    offer = Offer(
        name="small", connection="workstation", resources=Resources(cpus=1, memory_gib=0.5),
        max_nodes=1, time=TimeLimits(default="1m", max="1m"),
    )
    plan = provider.plan(offer, Request(cpus=1, memory_bytes=512 * 1024**2))
    assert not provider.root.exists()
    assert "cooperative" in plan.details["resource_enforcement"]
    with pytest.raises(ComputeError, match="one execution node"):
        provider.plan(offer, Request(cpus=1, memory_bytes=512 * 1024**2, num_nodes=2))
    with pytest.raises(ComputeError, match="finite time"):
        provider.plan(offer, Request(cpus=1, memory_bytes=512 * 1024**2, seconds=61))


def test_local_allocation_reopens_through_symlinked_configured_roots(
    provider: LocalProvider, tmp_path: Path,
) -> None:
    physical = private_directory(tmp_path / "physical-home", create=True)
    alias = tmp_path / "home-alias"
    alias.symlink_to(physical, target_is_directory=True)
    connection = provider.connection.replace(
        launch={
            "connection_root": str(alias / ".lightcone" / "compute"),
            "scratch_root": str(alias / "scratch"),
        },
    )
    launcher = LocalProvider(connection)
    identity = _launch(launcher)
    try:
        scheduler = _ready(launcher, identity)
        reopened = LocalProvider(connection)
        assert reopened.root == physical / ".lightcone" / "compute" / connection.namespace
        assert [snapshot.identity for snapshot in reopened.discover()] == [identity]
        with reopened.connect(identity) as client:
            assert client.scheduler_info()["id"] == scheduler["id"]
            assert client.submit(sum, [2, 3]).result(timeout=5) == 5
        metadata = read_private_json(reopened.root / identity.token / "launch.json")
        assert metadata["scratch"] == str(physical / "scratch" / f"lc-{identity.token}")
        assert private_directory(Path(metadata["scratch"])).is_dir()
    finally:
        LocalProvider(connection).terminate(identity)
    _ended(launcher, identity)


def test_local_managed_namespace_symlink_is_still_refused(
    provider: LocalProvider, tmp_path: Path,
) -> None:
    root = private_directory(tmp_path / "configured-root", create=True)
    alias = tmp_path / "root-alias"
    alias.symlink_to(root, target_is_directory=True)
    other = private_directory(tmp_path / "other-namespace", create=True)
    (root / provider.connection.namespace).symlink_to(other, target_is_directory=True)
    connection = provider.connection.replace(
        launch={**provider.connection.launch, "connection_root": str(alias)},
    )
    configured = LocalProvider(connection)
    with pytest.raises(ComputeError, match="plain directory"):
        _launch(configured)
    with pytest.raises(ComputeError, match="plain directory"):
        configured.discover()
    assert list(other.iterdir()) == []


def test_default_and_configured_scratch_aliases_are_resolved_without_resolving_python(
    provider: LocalProvider, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    physical = tmp_path / "physical"
    physical.mkdir(mode=0o700)
    alias = tmp_path / "os-temp"
    alias.symlink_to(physical, target_is_directory=True)
    monkeypatch.setattr("lightcone.engine.compute.local.tempfile.gettempdir", lambda: str(alias))
    connection = provider.connection.replace(
        launch={"connection_root": str(tmp_path / "connections")},
    )
    offer = Offer(
        name="small", connection="workstation", resources=Resources(cpus=1, memory_gib=0.5),
        max_nodes=1, time=TimeLimits(default="1m", max="1m"),
    )
    request = Request(cpus=1, memory_bytes=512 * 1024**2)
    plan = LocalProvider(connection).plan(offer, request)
    assert plan.details["scratch_root"] == str(physical)
    private_directory(Path(plan.details["scratch_root"]) / "default", create=True)
    python = tmp_path / "python"
    python.symlink_to(sys.executable)
    configured = connection.replace(launch={
        **connection.launch, "scratch_root": str(alias), "python": str(python),
    })
    plan = LocalProvider(configured).plan(offer, request)
    assert plan.details["scratch_root"] == str(physical)
    assert plan.details["python"] == str(python)
    private_directory(Path(plan.details["scratch_root"]) / "configured", create=True)


def test_configured_roots_reject_relative_parent_and_unresolvable_paths(tmp_path: Path) -> None:
    for path in (Path("relative"), tmp_path / ".." / "parent"):
        with pytest.raises(ComputeError, match="absolute path without"):
            configured_directory(path)
    loop = tmp_path / "loop"
    loop.symlink_to(loop)
    # Python versions differ on whether non-strict resolution raises for loops;
    # neither outcome may bypass validation before using allocation material.
    with pytest.raises(ComputeError, match="cannot resolve compute root|plain directory"):
        private_directory(configured_directory(loop), create=True)


def test_local_provider_allows_interactive_nodes_but_refuses_login_nodes_before_allocation(
    provider: LocalProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NERSC_HOST", "perlmutter")
    monkeypatch.setenv("SLURM_JOB_ID", "123")
    monkeypatch.setattr(socket, "gethostname", lambda: "nid200021")
    offer = Offer(
        name="small", connection="workstation", resources=Resources(cpus=1, memory_gib=0.5),
        max_nodes=1, time=TimeLimits(default="1m", max="1m"),
    )
    plan = provider.plan(offer, Request(cpus=1, memory_bytes=512 * 1024**2))
    assert plan.resources == offer.resources
    monkeypatch.setattr(socket, "gethostname", lambda: "login01")
    with pytest.raises(UnavailableOfferError, match="NERSC login nodes"):
        provider.plan(offer, plan.request)
    with pytest.raises(ComputeError, match="NERSC login nodes"):
        provider.launch(plan)
    assert not provider.root.exists()


@pytest.mark.parametrize("gpus", [0, 1])
@pytest.mark.parametrize("order", [None, "PCI_BUS_ID", "FASTEST_FIRST"])
def test_local_gpu_plan_freezes_native_mask_and_publishes_the_configured_envelope(
    provider: LocalProvider, monkeypatch: pytest.MonkeyPatch, gpus: int,
    order: str | None,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    # Mask interpretation and agreement with the catalog belong to the operator.
    mask = "GPU-opaque,3"
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", mask)
    if order is None:
        monkeypatch.delenv("CUDA_DEVICE_ORDER", raising=False)
    else:
        monkeypatch.setenv("CUDA_DEVICE_ORDER", order)
    probe = MagicMock(side_effect=AssertionError("local GPU planning must not probe hardware"))
    monkeypatch.setattr(local.subprocess, "run", probe)
    offer = Offer(
        name="gpu", connection="workstation",
        resources=Resources.from_bytes(
            cpus=1, memory_bytes=512 * 1024**2, gpus=gpus, accelerator_name="A100",
        ),
        max_nodes=1, time=TimeLimits(default="1m", max="1m"),
    )
    plan = provider.plan(offer, Request.parse("1", "0.5", gpus=f"GPU:{gpus}" if gpus else "0"))
    assert plan.details["cuda_visible_devices"] == (mask if gpus else "")
    assert plan.details["cuda_device_order"] == order
    assert not provider.root.exists()
    boot = str(uuid4())
    monkeypatch.setattr(local, "_boot_identity", lambda: boot)
    startup_readers: list[int] = []

    def spawned(argv: list[str], **kwargs: Any) -> SimpleNamespace:
        launch = read_private_json(Path(argv[-1]) / "launch.json")
        startup_readers.append(os.dup(int(launch["startup_fd"])))
        return SimpleNamespace(pid=12345)

    popen = MagicMock(side_effect=spawned)
    monkeypatch.setattr(local.subprocess, "Popen", popen)
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "an-ambient-mask")
    monkeypatch.setenv("CUDA_DEVICE_ORDER", "changed-after-planning")

    try:
        identity = provider.launch(plan)
        assert os.read(startup_readers[0], 1) == b"1"
    finally:
        for descriptor in startup_readers:
            os.close(descriptor)

    assert popen.call_args.kwargs["env"]["CUDA_VISIBLE_DEVICES"] == (mask if gpus else "")
    assert popen.call_args.kwargs["env"].get("CUDA_DEVICE_ORDER") == order
    assert os.environ["CUDA_VISIBLE_DEVICES"] == "an-ambient-mask"
    assert os.environ["CUDA_DEVICE_ORDER"] == "changed-after-planning"
    record = read_private_json(provider.root / identity.token / "identity.json")
    assert record["gpus"] == gpus
    monkeypatch.setattr(provider, "_process", lambda *_: None)
    snapshot = provider.inspect(identity)
    assert snapshot.resources is not None and snapshot.resources.gpus == gpus
    assert snapshot.resources.accelerator_name == ("A100" if gpus else None)
    probe.assert_not_called()


@pytest.mark.parametrize("mask", [None, ""])
def test_local_gpu_offers_require_an_explicit_nonempty_native_mask(
    provider: LocalProvider, monkeypatch: pytest.MonkeyPatch,
    mask: str | None,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    if mask is None:
        monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    else:
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", mask)
    offer = Offer(
        name="gpu", connection="workstation",
        resources=Resources.from_bytes(cpus=1, memory_bytes=512 * 1024**2, gpus=1),
        max_nodes=1, time=TimeLimits(default="1m", max="1m"),
    )
    request = Request.parse("1", "0.5", gpus="GPU:1")
    with pytest.raises(ComputeError, match="nonempty CUDA_VISIBLE_DEVICES"):
        provider.plan(offer, request)
    assert not provider.root.exists()


def test_unconfigured_local_gpu_mask_does_not_hide_a_later_slurm_offer(
    provider: LocalProvider, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    local_offer = Offer(
        name="local-gpu", connection="workstation",
        resources=Resources.from_bytes(cpus=1, memory_bytes=512 * 1024**2, gpus=1),
        max_nodes=1, time=TimeLimits(default="1m", max="1m"),
    )
    service = Compute.__new__(Compute)
    service.catalog = Catalog(
        version=1,
        connections={
            "workstation": provider.connection,
            "hpc": Connection(namespace=str(uuid4()), provider="slurm"),
        },
        offers=[local_offer, local_offer.replace(name="batch-gpu", connection="hpc")],
    )
    plan = service.plan(Request.parse("1", "0.5", gpus="GPU:1"))
    assert plan.offer.name == "batch-gpu"
    assert not provider.root.exists()


def test_local_gpu_offers_are_unavailable_outside_linux(
    provider: LocalProvider, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    offer = Offer(
        name="gpu", connection="workstation",
        resources=Resources.from_bytes(
            cpus=1, memory_bytes=512 * 1024**2, gpus=1,
        ),
        max_nodes=1, time=TimeLimits(default="1m", max="1m"),
    )
    with pytest.raises(ComputeError, match="GPU allocations require Linux"):
        provider.plan(offer, Request.parse("1", "0.5", gpus="GPU:1"))


@pytest.mark.parametrize("gpus", [None, 0, 2])
def test_local_runtime_advertises_configured_resources_and_preserves_native_gpu_mask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gpus: int | None,
) -> None:
    import distributed

    directory = private_directory(tmp_path / "allocation", create=True)
    scratch = private_directory(tmp_path / "scratch", create=True)
    record = {"cpus": 1, "memory": 1024**3}
    if gpus is not None:
        record["gpus"] = gpus
    write_private_json(directory / "identity.json", record)
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "3,1")
    monkeypatch.setattr(sys, "argv", ["local_runtime", str(directory)])
    monkeypatch.setattr(os, "getsid", lambda _: os.getpid())
    monkeypatch.setattr(os, "getpgrp", os.getpid)
    monkeypatch.setattr(os, "umask", lambda _: 0)
    monkeypatch.setattr(local_runtime.atexit, "register", lambda *_: None)
    monkeypatch.setattr(signal, "setitimer", lambda *_: None)
    monkeypatch.setattr(
        signal, "signal", lambda signum, handler: handler(signum, None)
        if signum == signal.SIGTERM else None,
    )
    monkeypatch.setattr(local_runtime, "create_security", lambda _: None)
    cluster = MagicMock()
    cluster.return_value.__enter__.return_value.scheduler.id = "Scheduler-gpu"
    monkeypatch.setattr(distributed, "LocalCluster", cluster)

    with local._allocation_lock() as lock_fd:
        startup_read, startup_write = os.pipe()
        try:
            os.write(startup_write, b"1")
            write_private_json(directory / "launch.json", {
                "identity": "allocation", "deadline": time.monotonic() + 60,
                "task_slots": 1, "scratch": str(scratch),
                "lock_fd": lock_fd, "startup_fd": startup_read,
            })
            local_runtime.main()
        finally:
            os.close(startup_write)
            try:
                os.close(startup_read)
            except OSError:
                pass  # The runtime owns and closes its inherited startup reader.
    assert cluster.call_args.kwargs["resources"] == {"CPU": 1, "MEMORY": 1024**3, "GPU": gpus or 0}
    assert os.environ["CUDA_VISIBLE_DEVICES"] == ("3,1" if gpus else "")
