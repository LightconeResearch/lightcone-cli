"""Tests for `lightcone.engine.run` — what `lc run` decides before it execs.

The project check, the declared inputs, and the uv hop. Nothing here
spawns a command; the boundary is tested in `test_sandbox_*`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lightcone.engine import run as engine_run
from lightcone.engine.project import ProjectError, current_project

SPEC = """\
title: Test
inputs:
  - id: local
    type: data
    source: data/local.csv
  - id: remote
    type: data
    source: https://example.org/nope.csv
outputs:
  - id: best_fit
    type: metric
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "data").mkdir(parents=True)
    (root / "data" / "local.csv").write_text("a,b\n")
    (root / "astra.yaml").write_text(SPEC)
    (root / "pyproject.toml").write_text('[project]\nname = "proj"\n')
    (root / "uv.lock").write_text("version = 1\n")
    (root / ".venv").mkdir()
    return root


# ---- the project check ----------------------------------------------------


def test_a_directory_with_an_environment_is_a_project(project: Path) -> None:
    assert current_project(project) == project.resolve()


def test_the_spec_is_not_required(project: Path) -> None:
    """`lc run` probes the *environment*; a uv project with no
    `astra.yaml` is still runnable."""
    (project / "astra.yaml").unlink()
    assert current_project(project) == project.resolve()


def test_a_subdirectory_is_not_the_project(project: Path) -> None:
    """No walk-up, deliberately: the directory a verb is invoked from is
    the directory that is used, or it is an error."""
    nested = project / "a" / "b"
    nested.mkdir(parents=True)
    with pytest.raises(ProjectError, match="not a project"):
        current_project(nested)


def test_the_missing_pieces_are_named(project: Path) -> None:
    (project / "uv.lock").unlink()
    with pytest.raises(ProjectError, match="missing uv.lock"):
        current_project(project)


def test_a_directory_without_an_environment_says_so(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="lc init"):
        current_project(tmp_path)


def test_the_default_is_the_working_directory(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(project)
    assert current_project() == project.resolve()


def test_a_missing_spec_reads_as_an_empty_one(project: Path) -> None:
    (project / "astra.yaml").unlink()
    assert engine_run.read_spec(project) == {}
    assert engine_run.input_paths(project, {}) == []


# ---- declared inputs ------------------------------------------------------


def test_declared_file_inputs_become_read_paths(project: Path) -> None:
    spec = engine_run.read_spec(project)
    assert engine_run.input_paths(project, spec) == [(project / "data" / "local.csv").resolve()]


def test_a_source_that_is_not_a_path_is_left_alone(project: Path) -> None:
    """ASTRA's `source` is free-form — a URI, a dotted name, a path — so
    "is this a path" is answered by whether it resolves to something that
    exists, not by parsing."""
    spec = engine_run.read_spec(project)
    assert not any("example.org" in str(p) for p in engine_run.input_paths(project, spec))


def test_a_spec_with_no_inputs_is_fine(tmp_path: Path) -> None:
    root = tmp_path / "bare"
    root.mkdir()
    (root / "astra.yaml").write_text("title: Bare\n")
    assert engine_run.input_paths(root, engine_run.read_spec(root)) == []


def test_an_unresolvable_tree_degrades_to_the_top_level_document(project: Path) -> None:
    """A probe exists to debug a project, and a spec whose sub-analysis
    references are stale is exactly when someone reaches for one."""
    (project / "astra.yaml").write_text(SPEC + "analyses:\n  - id: sub\n    path: ../gone\n")
    assert engine_run.read_spec(project)["inputs"]


# ---- the uv hop -----------------------------------------------------------


def test_uv_is_pinned_to_the_project_and_refuses_to_drift(project: Path) -> None:
    """uv's own walk-up discovery is never trusted, and a stale lock must
    be uv's loud error rather than a silent relock."""
    prefix = engine_run.uv_prefix(project)
    assert prefix[:2] == ["uv", "run"]
    assert "--locked" in prefix
    assert "--exact" in prefix
    assert prefix[prefix.index("--project") + 1] == str(project)
    assert prefix[-1] == "--"
