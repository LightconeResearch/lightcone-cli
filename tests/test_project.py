"""Tests for `lightcone.engine.project` — what a project is, and how one
converges."""

from __future__ import annotations

from pathlib import Path

import pytest

from lightcone.engine.project import (
    SPEC_FILENAME,
    ConvergenceReport,
    ProjectError,
    converge,
    find_root,
    project_name,
)

#: Every path convergence is responsible for.
SCAFFOLD = (
    "astra.yaml",
    "universes/baseline.yaml",
    "src",
    "pyproject.toml",
    ".python-version",
    ".gitignore",
    "results/README.md",
    "myst.yml",
    "index.md",
    "uv.lock",
    ".venv",
)


# ---- discovery ------------------------------------------------------------


def test_find_root_finds_the_spec_in_place(tmp_path: Path) -> None:
    (tmp_path / SPEC_FILENAME).write_text("name: x\n")
    assert find_root(tmp_path) == tmp_path.resolve()


def test_find_root_walks_up(tmp_path: Path) -> None:
    (tmp_path / SPEC_FILENAME).write_text("name: x\n")
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    assert find_root(deep) == tmp_path.resolve()


def test_find_root_returns_the_nearest_ancestor(tmp_path: Path) -> None:
    """A sub-analysis nested inside a project is its own root."""
    (tmp_path / SPEC_FILENAME).write_text("name: outer\n")
    inner = tmp_path / "sub"
    inner.mkdir()
    (inner / SPEC_FILENAME).write_text("name: inner\n")
    assert find_root(inner) == inner.resolve()


def test_find_root_is_none_outside_a_project(tmp_path: Path) -> None:
    assert find_root(tmp_path) is None


def test_find_root_ignores_a_directory_named_like_the_spec(tmp_path: Path) -> None:
    (tmp_path / SPEC_FILENAME).mkdir()
    assert find_root(tmp_path) is None


@pytest.mark.parametrize(
    ("directory", "expected"),
    [
        ("My Analysis", "my-analysis"),
        ("SN_Cosmology", "sn_cosmology"),
        ("2024.results", "2024.results"),
        ("...", "analysis"),
    ],
)
def test_project_name_normalizes_the_directory(directory: str, expected: str) -> None:
    assert project_name(Path("/tmp") / directory) == expected


# ---- the report -----------------------------------------------------------


def test_report_is_converged_only_when_nothing_changed() -> None:
    assert ConvergenceReport().converged
    assert ConvergenceReport(unchanged=["astra.yaml"]).converged
    assert not ConvergenceReport(created=["astra.yaml"]).converged
    assert not ConvergenceReport(repaired=[".gitignore"]).converged


def test_warnings_do_not_make_a_project_unconverged() -> None:
    """Warnings are things convergence can see but must not fix — they
    must not turn a converged project into a failing `--check`."""
    assert ConvergenceReport(unchanged=["astra.yaml"], warnings=["heads up"]).converged


# ---- the scaffold ---------------------------------------------------------


def test_converge_creates_the_whole_scaffold(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    report = converge(project, git=False)

    assert not report.converged
    for rel in SCAFFOLD:
        assert (project / rel).exists(), f"missing {rel}"


def test_converge_scaffolds_a_uv_project(tmp_path: Path) -> None:
    """The environment is the uv project: a *virtual* pyproject (no
    [build-system]) carrying the engine in its own dependency list, plus
    the exact interpreter pin (spec §2)."""
    from lightcone.engine.constants import DEFAULT_PYTHON, DEFAULT_PYTHON_FLOOR

    project = tmp_path / "proj"
    converge(project, git=False)

    pyproject = (project / "pyproject.toml").read_text()
    assert 'name = "proj"' in pyproject
    assert "lightcone-cli" in pyproject
    assert f'requires-python = ">={DEFAULT_PYTHON_FLOOR}"' in pyproject
    assert "[build-system]" not in pyproject
    assert (project / ".python-version").read_text().strip() == DEFAULT_PYTHON


def test_converge_scaffolds_no_container_declaration(tmp_path: Path) -> None:
    """Containerized mode is *derived* from a declaration the user makes,
    never scaffolded — and ASTRA carries nothing about the environment, so
    the boilerplate's `container:` key is stripped (spec §1, §2)."""
    project = tmp_path / "proj"
    converge(project, git=False)

    assert not (project / "Containerfile").exists()
    assert "container:" not in (project / SPEC_FILENAME).read_text()
    assert "[tool.lightcone.image]" not in (project / "pyproject.toml").read_text()


def test_converge_gitignores_results_but_keeps_its_readme(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    converge(project, git=False)

    gitignore = (project / ".gitignore").read_text()
    assert ".venv/" in gitignore
    assert "results/*" in gitignore
    assert "!results/README.md" in gitignore


def test_converge_titles_the_report_after_the_directory(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    converge(project, git=False)
    assert (project / "index.md").read_text().startswith("# proj\n")


# ---- idempotency and adoption --------------------------------------------


def test_converge_is_idempotent(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    converge(project, git=False)
    before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}

    report = converge(project, git=False)
    assert report.converged
    assert {p: p.read_bytes() for p in project.rglob("*") if p.is_file()} == before


def test_converge_adopts_an_existing_project(tmp_path: Path) -> None:
    """A directory that already holds an astra.yaml is adopted, not
    rejected — and the user's own file is never overwritten."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / SPEC_FILENAME).write_text('name: "mine"\n')

    report = converge(project, git=False)
    assert (project / SPEC_FILENAME).read_text() == 'name: "mine"\n'
    assert "astra.yaml" in report.unchanged
    assert (project / "pyproject.toml").exists()


def test_converge_repairs_a_missing_piece(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    converge(project, git=False)
    (project / "myst.yml").unlink()

    report = converge(project, git=False)
    assert report.created == ["myst.yml"]
    assert (project / "myst.yml").exists()


def test_gitignore_repair_preserves_user_content_and_adds_the_entries(
    tmp_path: Path,
) -> None:
    """A repair only ever appends: the user's own ignores survive intact."""
    from lightcone.engine import templates

    project = tmp_path / "proj"
    project.mkdir()
    (project / ".gitignore").write_text("mine.txt\nbuild/\n")

    report = converge(project, git=False)
    assert ".gitignore" in report.repaired

    text = (project / ".gitignore").read_text()
    assert text.startswith("mine.txt\nbuild/\n")
    assert templates.missing_gitignore_entries(text) == []


def test_gitignore_repair_appends_only_what_is_missing(tmp_path: Path) -> None:
    """Entry-wise convergence, so a pattern the user already has is never
    duplicated — whoever put it there."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".gitignore").write_text("__pycache__/\n.venv/\n")

    converge(project, git=False)
    text = (project / ".gitignore").read_text()
    assert text.count("__pycache__/") == 1
    assert text.count(".venv/") == 1
    assert "results/*" in text


def test_gitignore_repair_is_idempotent(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".gitignore").write_text("mine.txt\n")

    converge(project, git=False)
    once = (project / ".gitignore").read_text()
    report = converge(project, git=False)

    assert ".gitignore" in report.unchanged
    assert (project / ".gitignore").read_text() == once


def test_gitignore_repair_reaches_a_file_that_has_the_header_already(
    tmp_path: Path,
) -> None:
    """The header is cosmetic: a project carrying it but missing an entry
    — an older lc wrote it, or a hand edit removed a line — still
    converges. This is the case a marker check would have skipped."""
    from lightcone.engine import templates

    project = tmp_path / "proj"
    project.mkdir()
    (project / ".gitignore").write_text(f"{templates.GITIGNORE_HEADER}\n.venv/\n")

    report = converge(project, git=False)
    text = (project / ".gitignore").read_text()

    assert ".gitignore" in report.repaired
    assert templates.missing_gitignore_entries(text) == []
    # Cosmetic, so still exactly one.
    assert text.count(templates.GITIGNORE_HEADER) == 1


def test_repair_of_an_empty_gitignore_adds_no_leading_blank_lines(
    tmp_path: Path,
) -> None:
    from lightcone.engine import templates

    project = tmp_path / "proj"
    project.mkdir()
    (project / ".gitignore").write_text("")

    converge(project, git=False)
    assert (project / ".gitignore").read_text() == templates.gitignore()


def test_converge_writes_no_agent_notes(tmp_path: Path) -> None:
    """`AGENTS.md` is deliberately not scaffolded — see the deviation note
    in CLAUDE.md."""
    project = tmp_path / "proj"
    converge(project, git=False)
    assert not (project / "AGENTS.md").exists()


# ---- write=False (check mode) --------------------------------------------


def test_check_mode_writes_nothing_at_all(tmp_path: Path) -> None:
    """Not even the project directory — a drift report must be free of
    side effects."""
    project = tmp_path / "proj"
    report = converge(project, write=False, git=False)

    assert not report.converged
    assert "astra.yaml" in report.created
    assert not project.exists()


def test_check_mode_agrees_with_a_real_run(tmp_path: Path) -> None:
    """Check mode is the same decisions with side effects off, so its
    report must match what a real run reports."""
    project = tmp_path / "proj"
    dry = converge(project, write=False, git=False)
    wet = converge(project, git=False)

    assert dry.as_dict() == wet.as_dict()
    assert converge(project, write=False, git=False).converged


# ---- warnings -------------------------------------------------------------


def test_warns_when_pyproject_lacks_the_engine(tmp_path: Path) -> None:
    """The engine belongs inside the experiment's lock; a pyproject we
    didn't write is the user's, so warn rather than edit (spec §2)."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname = "mine"\nversion = "0"\n')

    report = converge(project, git=False)
    assert any("does not depend on lightcone-cli" in w for w in report.warnings)
    assert 'name = "mine"' in (project / "pyproject.toml").read_text()


def test_warns_when_results_is_not_a_directory(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "results").write_text("not a directory\n")

    report = converge(project, git=False)
    assert any("results exists but is not a directory" in w for w in report.warnings)
    assert (project / "results").read_text() == "not a directory\n"


# ---- refusals -------------------------------------------------------------


def test_refuses_an_authored_containerfile(tmp_path: Path) -> None:
    """Images are generated from the lock; a hand-written Containerfile
    would be silently ignored. Refuse — before any write (spec §8)."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "Containerfile").write_text("FROM python:3.12-slim\n")

    with pytest.raises(ProjectError, match="delete or rename it"):
        converge(project, git=False)

    assert not (project / SPEC_FILENAME).exists()
    assert not (project / "pyproject.toml").exists()


def test_requires_uv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from lightcone.engine import project as project_mod

    monkeypatch.setattr(project_mod.shutil, "which", lambda name, path=None: None)
    with pytest.raises(ProjectError, match="uv is required"):
        converge(tmp_path / "proj", git=False)


# ---- the uv seam ----------------------------------------------------------


def test_locks_then_syncs_exactly(tmp_path: Path, fake_uv: list[list[str]]) -> None:
    """Converge once, with the flags the spec fixes: ``--locked --exact``
    (no drift, no additive sync) and ``--compile-bytecode`` (§4, §6)."""
    project = tmp_path / "proj"
    converge(project, git=False)

    assert [c[0] for c in fake_uv] == ["lock", "sync"]
    sync = fake_uv[1]
    for flag in ("--locked", "--exact", "--compile-bytecode"):
        assert flag in sync
    # uv's own walk-up discovery is never trusted (spec §4).
    assert fake_uv[0][-2:] == ["--project", str(project.resolve())]
    assert sync[-2:] == ["--project", str(project.resolve())]


def test_sync_false_still_locks(tmp_path: Path, fake_uv: list[list[str]]) -> None:
    project = tmp_path / "proj"
    converge(project, git=False, sync=False)

    assert [c[0] for c in fake_uv] == ["lock"]
    assert (project / "uv.lock").exists()
    assert not (project / ".venv").exists()


def test_check_mode_runs_no_uv(tmp_path: Path, fake_uv: list[list[str]]) -> None:
    converge(tmp_path / "proj", write=False, git=False)
    assert fake_uv == []


def test_surfaces_a_lock_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A silently broken lock would fail every later verb more
    confusingly — so the failure surfaces here, with uv's own stderr."""
    from unittest.mock import MagicMock

    from lightcone.engine import project as project_mod

    monkeypatch.setattr(
        project_mod,
        "_run_uv",
        lambda args, *, cwd: MagicMock(returncode=1, stdout="", stderr="no solution found"),
    )
    with pytest.raises(ProjectError, match="no solution found"):
        converge(tmp_path / "proj", git=False)
