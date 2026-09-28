"""Command output survives detached workers and the real CLI byte streams."""

from __future__ import annotations

import io
import json
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from uuid import uuid4

import pytest

from lightcone.engine import sandbox
from lightcone.engine.compute import Compute
from lightcone.engine.compute.model import GIB, Request
from lightcone.engine.sandbox.boundary import _STDERR_TAIL_BYTES, _Tail, write_output


@pytest.fixture
def detached_cluster(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    catalog = tmp_path / "compute.json"
    catalog.write_text(json.dumps({
        "version": 1,
        "connections": {
            "local": {
                "provider": "local",
                "namespace": str(uuid4()),
                "launch": {
                    "connection_root": str(tmp_path / "connections"),
                    "scratch_root": str(tmp_path / "scratch"),
                },
            },
        },
        "offers": [{
            "name": "small",
            "connection": "local",
            "resources": {"cpus": 1, "memory": 1},
            "max_nodes": 1,
            "time": {"default": "2m", "max": "2m"},
        }],
    }))
    monkeypatch.setenv("LC_COMPUTE_CONFIG", str(catalog))
    compute = Compute()
    identity = compute.launch(compute.plan(Request(1, GIB))).encode()
    try:
        assert compute.status(identity, wait=True, timeout=30).ready
        yield identity
    finally:
        compute.down(identity)


def test_detached_probe_preserves_redirected_stdout_bytes(
    analysis: Callable[..., Path], detached_cluster: str,
) -> None:
    root = analysis("version: '0.0.13'\nname: analysis\ninputs: []\noutputs: []\n")
    result = subprocess.run(
        [
            sys.executable, "-c", "from lightcone.cli.commands import main; main()",
            "run", detached_cluster, "--", "python", "-c",
            "import sys; assert sys.stdin.buffer.read() == b''; "
            "sys.stdout.buffer.write(bytes(range(256)) * 1000 + b'\\xff\\r\\n')",
        ],
        cwd=root,
        input=b"this is not forwarded to remote stdin\n",
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert result.stdout == bytes(range(256)) * 1000 + b"\xff\r\n"


def test_probe_uses_allocation_environment_and_accepts_explicit_command_variables(
    analysis: Callable[..., Path], detached_cluster: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = analysis("version: '0.0.13'\nname: analysis\ninputs: []\noutputs: []\n")
    name = f"LC_TEST_AFTER_LAUNCH_{uuid4().hex}"
    monkeypatch.setenv(name, "driver-only")
    command = ["python", "-c", f"import os; print(os.environ.get({name!r}, 'not-forwarded'))"]
    cli = [sys.executable, "-c", "from lightcone.cli.commands import main; main()",
           "run", detached_cluster, "--"]
    for prefix, expected in (([], b"not-forwarded\n"),
                             (["env", f"{name}=explicit"], b"explicit\n")):
        result = subprocess.run(
            [*cli, *prefix, *command], cwd=root, capture_output=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        assert result.stdout == expected


def test_interrupt_warns_that_the_remote_command_may_still_run(
    analysis: Callable[..., Path], detached_cluster: str,
) -> None:
    root = analysis("version: '0.0.13'\nname: analysis\ninputs: []\noutputs: []\n")
    started = root / "results/started"
    process = subprocess.Popen(
        [
            sys.executable, "-c", "from lightcone.cli.commands import main; main()",
            "run", detached_cluster, "--", "python", "-c",
            "from pathlib import Path; import time; "
            "Path('results/started').touch(); time.sleep(30)",
        ],
        cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 15
        while not started.exists():
            if process.poll() is not None:
                pytest.fail(process.communicate()[1].decode(errors="replace"))
            if time.monotonic() >= deadline:
                pytest.fail("remote command did not start")
            time.sleep(0.05)
        process.send_signal(signal.SIGINT)
        _, stderr = process.communicate(timeout=15)
        assert process.returncode != 0
        assert b"may still be running" in stderr
        assert f"lc compute down {detached_cluster}".encode() in stderr
        assert Compute().status(detached_cluster).phase == "active"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_dask_cleans_output_history_after_the_borrowed_client_disconnects(
    cluster_id: str, capsys: pytest.CaptureFixture[str],
) -> None:
    import dask

    from lightcone.engine.compute import connect
    from lightcone.engine.compute.output import call, forwarding

    def emit(*, output: Callable[[str, bytes], None]) -> None:
        output("stdout", b"remote bytes\n")

    with dask.config.set({"distributed.scheduler.events-cleanup-delay": "20ms"}):
        with connect(cluster_id) as client:
            with forwarding(client) as output:
                topic = output.topic
                client.submit(call, emit, topic, "probe", pure=False).result()
                assert output.wait("probe")
            assert client.get_events(topic)
            assert capsys.readouterr().out == "remote bytes\n"
        with connect(cluster_id) as observer:
            deadline = time.monotonic() + 2
            while observer.get_events(topic) and time.monotonic() < deadline:
                time.sleep(0.01)
            assert observer.get_events(topic) == ()


def test_failed_detached_recipe_forwards_diagnostics_without_corrupting_json(
    analysis: Callable[..., Path], detached_cluster: str,
) -> None:
    command = "printf 'recipe stdout\\r\\n'; printf 'recipe failure\\n' >&2; exit 19"
    root = analysis(
        "version: '0.0.13'\nname: analysis\ninputs: []\noutputs:\n"
        "  - id: broken\n    type: metric\n    format: txt\n"
        f"    recipe:\n      command: {json.dumps(command)}\n"
    )
    result = subprocess.run(
        [
            sys.executable, "-c", "from lightcone.cli.commands import main; main()",
            "materialize", detached_cluster, "--json",
        ],
        cwd=root,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 1, result.stderr.decode(errors="replace")
    assert result.stdout, result.stderr.decode(errors="replace")
    report = json.loads(result.stdout)
    assert report["failed"] == ["baseline/broken"]
    assert b"recipe stdout\r\n" in result.stderr
    assert b"recipe failure\n" in result.stderr


def test_boundary_receives_bytes_and_only_decodes_the_denial_tail(tmp_path: Path) -> None:
    received: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
    policy = sandbox.Policy((), (), (), tmp_path)
    result = sandbox.run(
        sandbox.Unavailable(), policy,
        [sys.executable, "-c", (
            "import os; os.write(1, b'\\xff\\r\\n'); os.write(2, b'\\xfe\\r\\n')"
        )],
        cwd=tmp_path,
        env={},
        output=lambda stream, data: received[stream].extend(data),
    )
    assert result.returncode == 0
    assert received == {"stdout": b"\xff\r\n", "stderr": b"\xfe\r\n"}

    tail = _Tail(io.BytesIO(b"x" * (3 * _STDERR_TAIL_BYTES) + b"failure\xff\r\n"),
                 lambda stream, data: None)
    tail.run()
    assert tail.text().endswith("failure\ufffd\r\n")
    assert len(tail.text()) == _STDERR_TAIL_BYTES


def test_a_text_only_output_receiver_remains_readable(monkeypatch: pytest.MonkeyPatch) -> None:
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    write_output("stdout", b"diagnostic\r\n")
    assert output.getvalue() == "diagnostic\r\n"
