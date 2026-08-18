"""Tests for `lightcone.engine.run` — what `lc run` decides before it execs.

Discovery, the containerized refusal, the rename guard, the declared
inputs, and the uv hop. Nothing here spawns a command; the boundary is
tested in `test_sandbox_*`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lightcone.engine import run as engine_run
from lightcone.engine.project import ProjectError, find_project

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
    return root


# ---- discovery ------------------------------------------------------------


def test_discovery_walks_up_to_the_spec(project: Path) -> None:
    """The spec is what makes a directory a project, so it is what the
    walk-up looks for — not pyproject.toml, which would find any Python
    project at all."""
    nested = project / "a" / "b"
    nested.mkdir(parents=True)
    assert find_project(nested) == project.resolve()


def test_discovery_outside_a_project_says_so(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="not inside an ASTRA project"):
        find_project(tmp_path)


# ---- the rename guard (spec §4) -------------------------------------------


def test_an_output_id_is_refused_before_anything_execs(project: Path) -> None:
    """`lc run` used to mean "materialize these outputs". Trained fingers
    typing the old grammar must not get `command not found` — or worse,
    have the output name run as a program."""
    with pytest.raises(ProjectError, match="lc materialize best_fit"):
        engine_run.probe(project, ["best_fit"])


def test_a_command_that_is_not_an_output_passes_the_guard(project: Path) -> None:
    spec = engine_run.read_spec(project)
    engine_run._guard_output_name(["python"], spec)  # does not raise


def test_the_guard_only_looks_at_the_first_argument(project: Path) -> None:
    spec = engine_run.read_spec(project)
    engine_run._guard_output_name(["python", "best_fit"], spec)  # does not raise


# ---- mode detection (spec §1) ---------------------------------------------


def test_a_declared_system_layer_is_refused_not_silently_ignored(project: Path) -> None:
    """Declaring a system layer *is* the escalation to containerized
    mode. Running it in direct mode anyway would execute in a different
    world than the declaration asks for and attest something untrue."""
    (project / "pyproject.toml").write_text(
        '[project]\nname = "proj"\n\n[tool.lightcone.image]\nsystem-packages = ["r-base-core"]\n'
    )
    with pytest.raises(ProjectError, match="containerized mode"):
        engine_run.probe(project, ["true"])


def test_a_containerfile_extra_triggers_the_same_refusal(project: Path) -> None:
    (project / "Containerfile.extra").write_text("RUN echo hi\n")
    with pytest.raises(ProjectError, match="containerized mode"):
        engine_run.probe(project, ["true"])


def test_a_direct_mode_project_is_not_refused(project: Path) -> None:
    engine_run._refuse_containerized(project)  # does not raise


def test_unreadable_toml_is_an_error_not_a_guess(project: Path) -> None:
    (project / "pyproject.toml").write_text("[project\n")
    with pytest.raises(ProjectError, match="not valid TOML"):
        engine_run._refuse_containerized(project)


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
    """uv's own walk-up discovery is never trusted (spec §4), and a
    stale lock must be uv's loud error rather than a silent relock."""
    prefix = engine_run.uv_prefix(project)
    assert prefix[:2] == ["uv", "run"]
    assert "--locked" in prefix
    assert "--exact" in prefix
    assert prefix[prefix.index("--project") + 1] == str(project)
    assert prefix[-1] == "--"


# ---- --require-sandbox ----------------------------------------------------


def test_require_sandbox_refuses_where_nothing_can_enforce() -> None:
    from lightcone.engine.sandbox.boundary import Unavailable
    from lightcone.engine.sandbox.model import Capability

    backend = Unavailable(capability=Capability(kind="none", detail="kernel too old"))
    with pytest.raises(ProjectError, match="kernel too old"):
        engine_run._enforce_requirement(backend, require=True)


def test_without_the_flag_an_unenforced_run_is_allowed() -> None:
    from lightcone.engine.sandbox.boundary import Unavailable

    engine_run._enforce_requirement(Unavailable(), require=False)  # does not raise
