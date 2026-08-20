"""Tests for `lightcone.engine.run` — what `lc run` decides before it execs.

The project check, the declared inputs, and the uv hop. Nothing here
spawns a command; the boundary is tested in `test_sandbox_*`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lightcone.engine import run as engine_run
from lightcone.engine.project import (
    ProjectError,
    current_project,
    declared_project,
    uv_prefix,
)

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
    with pytest.raises(ProjectError, match="is not a Lightcone project"):
        current_project(nested)


def test_an_unbuilt_project_is_told_to_build_it(project: Path) -> None:
    """A fresh clone is exactly this — git carries no `.venv` — so here
    `lc init` is the whole answer, and the missing pieces are named."""
    (project / ".venv").rmdir()
    with pytest.raises(ProjectError, match="not been built yet") as raised:
        current_project(project)
    assert "missing .venv" in str(raised.value)
    assert "lc init" in str(raised.value)


def test_a_containerized_project_needs_no_host_venv(project: Path) -> None:
    """The host `.venv` is inert in containerized mode — the environment
    the verbs enter is `.lightcone/venv`, created by their own converge
    inside the image — so a clone is runnable without it."""
    (project / ".venv").rmdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "proj"\n\n[tool.lightcone.image]\napt-install = ["bc"]\n'
    )
    assert current_project(project) == project.resolve()


def test_the_wrong_directory_is_told_to_move_not_to_scaffold(tmp_path: Path) -> None:
    """The complaint that produced this split: standing in `$HOME`, being
    told to run `lc init` is advice to scaffold a project in your home
    directory. The wrong *place* deserves "go to the right one"."""
    with pytest.raises(ProjectError, match="is not a Lightcone project") as raised:
        current_project(tmp_path)
    message = str(raised.value)
    assert "cd to the root of one" in message
    assert "lc init" not in message


def test_the_default_is_the_working_directory(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(project)
    assert current_project() == project.resolve()


def test_a_declared_project_needs_only_what_git_carries(project: Path) -> None:
    """The weaker question, and the difference between the two: a clone
    holds the lock and no `.venv`, and the worker entry point builds one
    rather than refusing."""
    (project / ".venv").rmdir()
    assert declared_project(project) == project.resolve()
    with pytest.raises(ProjectError, match="not been built yet"):
        current_project(project)


def test_a_declared_project_still_needs_the_lock(project: Path) -> None:
    """It is weaker, not absent: without `uv.lock` there is no environment
    to converge and nothing to be exact about."""
    (project / "uv.lock").unlink()
    with pytest.raises(ProjectError, match="not been built yet") as raised:
        declared_project(project)
    assert "missing uv.lock" in str(raised.value)


def test_a_missing_spec_reads_as_an_empty_one(project: Path) -> None:
    (project / "astra.yaml").unlink()
    assert engine_run.read_spec(project) == {}
    assert engine_run.input_paths(project, {}) == []


# ---- declared inputs ------------------------------------------------------


def test_declared_file_inputs_become_read_paths(project: Path) -> None:
    spec = engine_run.read_spec(project)
    assert engine_run.input_paths(project, spec) == [(project / "data" / "local.csv").resolve()]


def test_an_input_declared_inside_a_sub_analysis_is_a_read_path_too(project: Path) -> None:
    """A denial the researcher cannot act on is worse than the access it
    withheld: the file *is* declared, just not at the top of the tree."""
    (project / "data" / "stage.csv").write_text("x\n")
    spec = {
        "inputs": [{"id": "local", "source": "data/local.csv"}],
        "analyses": {"stage": {"inputs": [{"id": "sub", "source": "data/stage.csv"}]}},
    }
    assert engine_run.input_paths(project, spec) == [
        (project / "data" / "local.csv").resolve(),
        (project / "data" / "stage.csv").resolve(),
    ]


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
    prefix = uv_prefix(project, sync=True)
    assert prefix[:2] == ["uv", "run"]
    assert "--locked" in prefix
    assert "--exact" in prefix
    assert prefix[prefix.index("--project") + 1] == str(project)
    assert prefix[-1] == "--"


def test_a_recipe_does_not_sync_where_a_probe_does(project: Path) -> None:
    """The one thing the two hops disagree about: a probe converges the
    environment it is about to describe, and a recipe must not, or every
    concurrent worker writes the same `.venv`."""
    assert "--no-sync" in uv_prefix(project, sync=False)
    assert "--exact" not in uv_prefix(project, sync=False)
