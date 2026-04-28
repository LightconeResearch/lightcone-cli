"""Tests for the redesigned lightcone CLI."""
from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from lightcone.cli.commands import main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect ``~/.lightcone/`` to a temp dir so tests don't pollute the user's
    real config and can pass the main-group config check."""
    fake_home = tmp_path / "_home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    config = fake_home / ".lightcone" / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("container:\n  runtime: auto\n")


# ---- top-level ------------------------------------------------------------


def test_help_lists_core_commands(runner: CliRunner) -> None:
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    for cmd in ("init", "launch", "run", "status", "verify", "build", "setup"):
        assert cmd in result.output


def test_help_does_not_advertise_removed_commands(runner: CliRunner) -> None:
    result = runner.invoke(main, ["--help"])
    assert "  dev " not in result.output
    assert "  cluster " not in result.output


# ---- lc init --------------------------------------------------------------


def test_init_creates_project(runner: CliRunner, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    result = runner.invoke(main, ["init", str(project), "--no-git", "--no-venv"])
    assert result.exit_code == 0, result.output
    assert (project / "astra.yaml").exists()
    assert (project / "CLAUDE.md").exists()
    assert (project / ".gitignore").exists()
    assert (project / ".lightcone").is_dir()
    assert (project / "results").is_dir()
    assert (project / "universes").is_dir()


def test_init_refuses_when_astra_yaml_exists(
    runner: CliRunner, tmp_path: Path
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "astra.yaml").write_text("# already here\n")
    result = runner.invoke(main, ["init", str(project), "--no-git", "--no-venv"])
    assert result.exit_code != 0
    assert "already exists" in result.output


# ---- lc verify ------------------------------------------------------------


def test_verify_clean_project_returns_zero(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty project (no materialized outputs yet) is a clean state, not
    a verification failure."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "astra.yaml").write_text(
        "outputs:\n  - id: foo\n    recipe:\n      command: echo\n"
    )
    monkeypatch.chdir(project)
    result = runner.invoke(main, ["verify"])
    assert result.exit_code == 0
