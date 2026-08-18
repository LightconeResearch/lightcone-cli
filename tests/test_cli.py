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
    # "already converged" straddles rich's wrap point once the tmp path is
    # long enough — assert on a fragment that cannot wrap.
    assert "nothing to do" in result.output


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


# ---- lc run ---------------------------------------------------------------


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A minimal project, with the CLI's cwd pointed inside it."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "astra.yaml").write_text("title: T\noutputs:\n  - id: best_fit\n    type: metric\n")
    (root / "pyproject.toml").write_text('[project]\nname = "proj"\n')
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Record what the CLI asks the engine to do, without running anything."""
    from lightcone.engine import run as engine_run
    from lightcone.engine.sandbox import Outcome
    from lightcone.engine.sandbox.model import Attestation

    calls: list[dict[str, object]] = []

    def fake_probe(project, command, **kwargs):  # type: ignore[no-untyped-def]
        calls.append({"project": project, "command": list(command), **kwargs})
        return Outcome(
            returncode=0,
            attestation=Attestation(mechanism="landlock", fs="declared", landlock_abi=4),
        )

    monkeypatch.setattr(engine_run, "probe", fake_probe)
    return calls


def test_run_is_advertised(runner: CliRunner) -> None:
    result = runner.invoke(main, ["--help"])
    assert "run" in result.output


def test_run_passes_the_command_through_untouched(
    runner: CliRunner, project: Path, spawned: list[dict[str, object]]
) -> None:
    """A probe's command has its own flags, and they belong to it — not
    to us. `--help` after the command must reach the command."""
    result = runner.invoke(main, ["run", "python", "-c", "print(1)", "--help"])
    assert result.exit_code == 0
    assert spawned[0]["command"] == ["python", "-c", "print(1)", "--help"]


def test_the_sandbox_is_on_by_default(
    runner: CliRunner, project: Path, spawned: list[dict[str, object]]
) -> None:
    runner.invoke(main, ["run", "true"])
    assert spawned[0]["sandboxed"] is True
    assert spawned[0]["require"] is False


def test_no_sandbox_reaches_the_engine(
    runner: CliRunner, project: Path, spawned: list[dict[str, object]]
) -> None:
    runner.invoke(main, ["run", "--no-sandbox", "true"])
    assert spawned[0]["sandboxed"] is False


def test_require_sandbox_reaches_the_engine(
    runner: CliRunner, project: Path, spawned: list[dict[str, object]]
) -> None:
    runner.invoke(main, ["run", "--require-sandbox", "true"])
    assert spawned[0]["require"] is True


def test_the_childs_exit_code_is_the_cli_s(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A probe is a proxy for the command; swallowing its exit code would
    make `lc run` useless in a script."""
    from lightcone.engine import run as engine_run
    from lightcone.engine.sandbox import Outcome
    from lightcone.engine.sandbox.model import Attestation

    monkeypatch.setattr(
        engine_run,
        "probe",
        lambda *a, **k: Outcome(
            returncode=42, attestation=Attestation(mechanism="none", fs="open")
        ),
    )
    assert runner.invoke(main, ["run", "false"]).exit_code == 42


def test_notes_are_rendered(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lightcone.engine import run as engine_run
    from lightcone.engine.sandbox import Outcome
    from lightcone.engine.sandbox.model import Attestation

    monkeypatch.setattr(
        engine_run,
        "probe",
        lambda *a, **k: Outcome(
            returncode=1,
            attestation=Attestation(mechanism="landlock", fs="declared"),
            notes=("blocked by lc sandbox: cannot execute /usr/bin/latex —",),
        ),
    )
    result = runner.invoke(main, ["run", "latex"])
    assert "blocked by lc sandbox" in result.output


def test_a_bare_run_announces_the_shell(
    runner: CliRunner, project: Path, spawned: list[dict[str, object]]
) -> None:
    """A shell that looks like your own but cannot write the project is
    worse than no shell if you do not know it is there."""
    result = runner.invoke(main, ["run"])
    assert "opening a shell" in result.output
    assert "(sandboxed)" in result.output
    assert spawned[0]["command"] == []


def test_an_unsandboxed_shell_does_not_claim_to_be_sandboxed(
    runner: CliRunner, project: Path, spawned: list[dict[str, object]]
) -> None:
    """The announcement is the only signal before an interactive shell
    takes the terminal, so it is the one line that must never overstate
    what is enforcing."""
    result = runner.invoke(main, ["run", "--no-sandbox"])
    assert "NOT sandboxed" in result.output


def test_the_rename_guard_renders_as_a_clean_error(runner: CliRunner, project: Path) -> None:
    result = runner.invoke(main, ["run", "best_fit"])
    assert result.exit_code == 1
    assert "lc materialize best_fit" in result.output


def test_outside_a_project_is_a_clean_error(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(main, ["run", "true"])
    assert result.exit_code == 1
    assert "lc init" in result.output


def test_a_signal_killed_command_reports_the_conventional_status() -> None:
    """`Popen.returncode` is negative for a signal and `sys.exit(-9)`
    truncates to 247. A script testing for 137 (SIGKILL) has to see 137."""
    from lightcone.cli.commands import _exit_status

    assert _exit_status(-9) == 137
    assert _exit_status(-15) == 143
    assert _exit_status(0) == 0
    assert _exit_status(42) == 42
