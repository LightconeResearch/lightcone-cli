"""Tests for the CLI surface — flags, rendering, exit codes.

Convergence *semantics* are tested against the engine in
`tests/test_project.py`; this file covers only what the CLI adds on top.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from lightcone.cli.commands import main

# ---- top level ------------------------------------------------------------


def test_help_lists_the_implemented_verb(runner: CliRunner) -> None:
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "init" in result.output


def test_help_does_not_advertise_unbuilt_verbs(runner: CliRunner) -> None:
    """Layer 1 ships `lc init` alone. The other verbs return with their
    layers — advertising them before they work would be a lie the whole
    rebuild is meant to avoid."""
    result = runner.invoke(main, ["--help"])
    for verb in ("materialize", "status", "verify", "build", "export"):
        assert f"  {verb}" not in result.output


def test_engine_errors_render_cleanly(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ProjectError from anywhere in the engine surfaces as a one-line
    CLI error (exit 1), not a traceback — the group boundary translates
    it."""
    from lightcone.engine import project

    monkeypatch.setattr(project.shutil, "which", lambda name, path=None: None)

    result = runner.invoke(main, ["init", str(tmp_path / "proj")])
    assert result.exit_code == 1
    assert "uv is required" in result.output
    assert "Traceback" not in result.output


# ---- lc init: flags reach the engine --------------------------------------


def test_init_creates_a_project(runner: CliRunner, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    result = runner.invoke(main, ["init", str(project)])
    assert result.exit_code == 0, result.output
    assert (project / "astra.yaml").exists()
    assert (project / ".venv").exists()
    assert (project / ".git").exists()


def test_init_defaults_to_the_current_directory(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(main, ["init"])
    assert result.exit_code == 0
    assert (tmp_path / "astra.yaml").exists()


# ---- rendering ------------------------------------------------------------


def test_run_reports_what_it_created(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(main, ["init", str(tmp_path / "proj")])
    assert "created astra.yaml" in result.output
    assert "Project converged at" in result.output


def test_run_on_a_converged_project_says_so(runner: CliRunner, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    assert runner.invoke(main, ["init", str(project)]).exit_code == 0

    result = runner.invoke(main, ["init", str(project)])
    assert result.exit_code == 0
    assert "already converged" in result.output


def test_blocked_items_are_rendered(runner: CliRunner, tmp_path: Path) -> None:
    """A blocked item is why a run can end unconverged, so it has to be
    visible — its reason alone (carried as a warning) doesn't say which
    item is missing."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "results").write_text("not a directory\n")

    result = runner.invoke(main, ["init", str(project)])
    assert result.exit_code == 0
    assert "blocked results/" in result.output
    # Rich wraps on the terminal width, so assert on an unwrappable fragment.
    assert "✗" in result.output


def test_run_surfaces_warnings(runner: CliRunner, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname = "mine"\nversion = "0"\n')

    result = runner.invoke(main, ["init", str(project)])
    assert result.exit_code == 0
    assert "does not depend on lightcone-cli" in result.output


# ---- --check / --json (the agent-facing surface) --------------------------


def test_check_reports_drift_without_writing(runner: CliRunner, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    result = runner.invoke(main, ["init", str(project), "--check"])
    assert result.exit_code == 1
    assert "would create" in result.output
    assert not project.exists()


def test_check_passes_on_a_converged_project(runner: CliRunner, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    assert runner.invoke(main, ["init", str(project)]).exit_code == 0

    result = runner.invoke(main, ["init", str(project), "--check"])
    assert result.exit_code == 0
    # Rich wraps on the terminal width, so assert on an unwrappable fragment.
    assert "nothing to do" in result.output


def test_json_report_is_machine_readable(runner: CliRunner, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    result = runner.invoke(main, ["init", str(project), "--json"])
    assert result.exit_code == 0

    # Parsed straight off stdout: --json suppresses the banner.
    payload = json.loads(result.output)
    assert payload["converged"] is False
    assert "astra.yaml" in payload["created"]
    assert payload["warnings"] == []

    payload = json.loads(runner.invoke(main, ["init", str(project), "--json"]).output)
    assert payload["converged"] is True
    assert payload["created"] == [] and payload["repaired"] == []


def test_check_json_writes_nothing_and_exits_nonzero(
    runner: CliRunner, tmp_path: Path
) -> None:
    """`--check --json` together are the agent form: a drift report with no
    side effects and an exit code to branch on."""
    project = tmp_path / "proj"
    result = runner.invoke(main, ["init", str(project), "--check", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["converged"] is False
    assert not project.exists()
