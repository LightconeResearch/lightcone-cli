"""Command output survives detached workers and the real CLI byte streams."""

from __future__ import annotations

import io
import json
import subprocess
import sys
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
            "import sys; sys.stdout.buffer.write(bytes(range(256)) * 1000 + b'\\xff\\r\\n')",
        ],
        cwd=root,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert result.stdout == bytes(range(256)) * 1000 + b"\xff\r\n"


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
