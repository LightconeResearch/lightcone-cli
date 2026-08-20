"""The validator's answer — the one file that can say the crate conforms.

A real project is materialized through the real driver, and the committed
``ro-crate-metadata.json`` is handed to the official ``rocrate-validator``
against the deepest profile the crate claims. Gated exactly like the
container smoke suite: hosts without the validator skip,
``LC_CRATE_TESTS_REQUIRED=1`` turns that skip into a hard failure, and
two tests cover the guard itself — an unfailing guard is worse than none.

At RECOMMENDED there is a known floor, pinned as a *set* rather than a
count: the workflow's id is the file in the crate and cannot be an http
URI, the image lives in the annex and has no registry, and lc knows no
publisher and no author affiliation. A new failure is a regression; a
disappearing one is the floor to shrink.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from lightcone.engine import dataset
from lightcone.engine import materialize as engine
from lightcone.engine.worker import TaskResult

REQUIRED_ENV = "LC_CRATE_TESTS_REQUIRED"

_VALIDATOR = importlib.util.find_spec("rocrate_validator") is not None

#: The checks the crate cannot truthfully satisfy, by check identifier.
_FLOOR = {
    # the workflow's id is its path in the crate, and the shape wants http
    "process-run-crate-0.5_5.1",
    # the image is bytes in the annex; there is no registry to name
    "process-run-crate-0.5_13.2",
    # lc knows no publishing organization and no author affiliation
    "ro-crate-1.1_22.3",
    "ro-crate-1.1_29.2",
    "ro-crate-1.1_29.3",
}

_SPEC = """
version: "0.0.13"
name: analysis
description: A two-step analysis for the crate smoke test.

inputs:
  - id: catalog
    type: data
    source: data/catalog.fits

outputs:
  - id: first
    type: metric
    decisions: [method]
    recipe:
      command: echo {decisions.method} > {output}/value.txt

  - id: second
    type: report
    inputs: [first]
    recipe:
      command: cat {inputs.first}/value.txt > {output}/copy.txt

decisions:
  method:
    label: Method
    default: alpha
    options:
      alpha: {label: alpha}
      beta: {label: beta}
"""


def _gate() -> None:
    if _VALIDATOR:
        return
    if os.environ.get(REQUIRED_ENV):
        pytest.fail(
            f"{REQUIRED_ENV} is set but rocrate-validator is not importable. "
            "Crate validation must not be skipped on CI."
        )
    pytest.skip("rocrate-validator is not installed here")


def test_the_guard_skips_without_the_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys.modules[__name__], "_VALIDATOR", False)
    monkeypatch.delenv(REQUIRED_ENV, raising=False)

    with pytest.raises(pytest.skip.Exception):
        _gate()


def test_the_guard_fails_when_skipping_is_forbidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys.modules[__name__], "_VALIDATOR", False)
    monkeypatch.setenv(REQUIRED_ENV, "1")

    with pytest.raises(pytest.fail.Exception):
        _gate()


class _Inline:
    def submit(self, fn: Any, *args: Any, key: str) -> Any:
        return fn(*args)

    def completed(self, handles: list[Any]) -> Iterator[TaskResult]:
        yield from handles


def test_a_materialized_crate_validates_against_the_provenance_profile(
    analysis: Callable[..., Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _gate()

    @contextmanager
    def inline() -> Iterator[_Inline]:
        yield _Inline()

    monkeypatch.setattr(engine, "cluster_for_run", inline)
    root = analysis(
        _SPEC,
        files={"data/catalog.fits": "stars\n", "README.md": "# analysis\n\nA demo.\n"},
        universes={"baseline": "id: baseline\ndecisions:\n  method: alpha\n"},
    )
    pyproject = root / "pyproject.toml"
    pyproject.write_text(pyproject.read_text() + 'license = "MIT"\n')
    # A remote gives the tools http ids; the manifests record origin.
    dataset._git(
        ["remote", "add", "origin", "https://github.com/example/analysis.git"], cwd=root
    )
    dataset.save(root, [root], "license and remote")

    report = engine.materialize(root, [])
    assert report.ok and (root / "ro-crate-metadata.json").is_file()
    assert not dataset.status(root)

    out = tmp_path / "validation.json"
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            # The console script, run on this interpreter — the same one
            # the gate probed for importability.
            "from rocrate_validator.cli import cli; cli()",
            "validate",
            "-p",
            "provenance-run-crate",
            "-l",
            "recommended",
            "-f",
            "json",
            "-o",
            str(out),
            str(root),
        ],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=600,
    )
    result = json.loads(out.read_text())
    issues = result.get("issues") or []

    required = [i for i in issues if i["severity"] == "REQUIRED"]
    assert not required, f"REQUIRED failures:\n{json.dumps(required, indent=1)}"
    unexpected = [i for i in issues if i["check"]["identifier"] not in _FLOOR]
    assert not unexpected, (
        f"failures beyond the recorded floor (validator exit {proc.returncode}):\n"
        f"{json.dumps(unexpected, indent=1)}"
    )
