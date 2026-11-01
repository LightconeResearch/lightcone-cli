"""Malformed YAML through the real CLI, with no engine or tool stubs."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

_SPEC = '''version: "0.1"
name: demo
inputs: []
outputs:
  - id: fit
    type: data
    format: txt
    recipe:
      command: echo valid > {output}
decisions: {}
'''


@pytest.mark.parametrize("argv", [["status"], ["materialize", "--check"]])
@pytest.mark.parametrize("json_output", [False, True])
@pytest.mark.parametrize("filename", ["astra.yaml", "universes/baseline.yaml"])
def test_malformed_yaml_is_reported_without_a_traceback_or_writes(
    analysis: Callable[..., Path], argv: list[str], json_output: bool, filename: str,
) -> None:
    root = analysis(_SPEC)
    command = [sys.executable, "-c", "from lightcone.cli import main; main()", *argv]
    if json_output:
        command.append("--json")
    env = dict(os.environ, PATH=f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}")

    valid = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
    assert valid.returncode == (0 if argv == ["status"] else 1), valid.stderr
    assert valid.stderr == ""
    assert "baseline/fit" in valid.stdout
    assert not (root / "results/baseline/fit.txt").exists()

    malformed = root / filename
    malformed.write_text("name: a: b\n")
    before_paths = set(root.rglob("*"))
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("Error: ")
    assert str(malformed) in result.stderr
    assert "mapping values are not allowed here" in result.stderr
    assert "line 1, column 8" in result.stderr
    assert "Traceback" not in result.stderr
    assert set(root.rglob("*")) == before_paths
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
