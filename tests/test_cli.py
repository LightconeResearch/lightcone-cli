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


def test_help_advertises_exactly_the_verbs_that_work(runner: CliRunner) -> None:
    """`lc --help` advertises only verbs that work — advertising others
    before they do would be a lie."""
    result = runner.invoke(main, ["--help"])
    for verb in ("init", "materialize", "run", "status"):
        assert f"  {verb}" in result.output
    for verb in ("verify", "build", "export"):
        assert f"  {verb}" not in result.output


def test_help_does_not_advertise_the_worker(runner: CliRunner) -> None:
    """The unit a run record names is machinery, not a verb: it makes the
    output unconditionally, commits nothing, and leaves the tree dirty by
    design — the state `lc materialize` refuses to start from."""
    assert "worker" not in runner.invoke(main, ["--help"]).output


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


def test_init_reports_what_it_created(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(main, ["init", str(tmp_path / "proj")])
    assert "created astra.yaml" in result.output
    assert "Project converged at" in result.output


def test_init_on_a_converged_project_says_so(runner: CliRunner, tmp_path: Path) -> None:
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
    # The reason travels as a warning, and the console renders those too.
    assert "not a directory" in result.output


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
    """A minimal project, with the CLI's cwd pointed at its root."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nname = "proj"\n')
    (root / "uv.lock").write_text("version = 1\n")
    (root / ".venv").mkdir()
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
    assert spawned[0]["project"] == project.resolve()


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


def test_a_signal_killed_command_reports_the_conventional_status(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Popen.returncode` is negative for a signal and `sys.exit(-9)`
    truncates to 247. A script testing for 137 (SIGKILL) has to see 137."""
    from lightcone.engine import run as engine_run
    from lightcone.engine.sandbox import Outcome
    from lightcone.engine.sandbox.model import Attestation

    monkeypatch.setattr(
        engine_run,
        "probe",
        lambda *a, **k: Outcome(
            returncode=-9, attestation=Attestation(mechanism="landlock", fs="declared")
        ),
    )
    assert runner.invoke(main, ["run", "sleep"]).exit_code == 137


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


def test_a_bare_run_is_refused_rather_than_opening_a_shell(
    runner: CliRunner, project: Path, spawned: list[dict[str, object]]
) -> None:
    """A probe is run by an agent far more often than by a person, and an
    agent handed an interactive shell waits forever for input nobody is
    going to type. Refusing is the only outcome that cannot hang."""
    result = runner.invoke(main, ["run"])
    assert result.exit_code != 0
    assert spawned == []


def test_outside_a_project_is_a_clean_error(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(main, ["run", "true"])
    assert result.exit_code == 1
    assert "not a Lightcone project" in result.output


def test_a_subdirectory_of_a_project_is_not_the_project(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No walk-up: `lc run` uses the directory it is invoked from, or
    errors."""
    nested = project / "sub"
    nested.mkdir()
    monkeypatch.chdir(nested)
    result = runner.invoke(main, ["run", "true"])
    assert result.exit_code == 1
    assert "not a Lightcone project" in result.output


def test_run_needs_no_spec_file(
    runner: CliRunner, project: Path, spawned: list[dict[str, object]]
) -> None:
    """The environment is what a probe needs; `astra.yaml` is not
    required to run a command in it."""
    assert not (project / "astra.yaml").exists()
    result = runner.invoke(main, ["run", "true"])
    assert result.exit_code == 0
    assert spawned[0]["project"] == project.resolve()


# ---- lc materialize -------------------------------------------------------


def _stub(monkeypatch: pytest.MonkeyPatch, **outcomes: object) -> list[tuple[str, object]]:
    """Record which engine entry point the flags reached, and with what."""
    from lightcone.engine import materialize as engine

    seen: list[tuple[str, object]] = []

    def record(name: str) -> object:
        def call(root: Path, targets: object, **kwargs: object) -> object:
            seen.append((name, (list(targets), kwargs)))
            return outcomes.get(name, engine.MaterializeReport())

        return call

    monkeypatch.setattr(engine, "check", record("check"))
    monkeypatch.setattr(engine, "materialize", record("materialize"))
    return seen


def test_check_reaches_check_mode_and_nothing_else(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _stub(monkeypatch)

    runner.invoke(main, ["materialize", "--check"])

    assert [name for name, _ in seen] == ["check"]


def test_targets_reach_the_engine(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _stub(monkeypatch)

    runner.invoke(main, ["materialize", "baseline/fit", "report"])

    assert seen == [("materialize", (["baseline/fit", "report"], {"refresh": False}))]


def test_refresh_reaches_both_modes(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--check` has to answer the question the run would ask, or the gate
    reports on a run nobody is going to make."""
    seen = _stub(monkeypatch)

    runner.invoke(main, ["materialize", "--refresh"])
    runner.invoke(main, ["materialize", "--check", "--refresh"])

    assert seen == [
        ("materialize", ([], {"refresh": True})),
        ("check", ([], {"refresh": True})),
    ]


def test_there_is_no_flag_to_stop_a_stale_output_being_remade(runner: CliRunner) -> None:
    """`--refresh` widens what a run does; nothing narrows it. An artifact
    that contradicts the analysis is remade, and deleting the directory is
    the user's own file operation if they want it gone instead."""
    output = runner.invoke(main, ["materialize", "--help"]).output
    for flag in ("--force", "--keep-going", "--no-refresh", "--skip"):
        assert flag not in output


def test_there_is_no_knob_for_how_much_of_the_machine_to_use(runner: CliRunner) -> None:
    """A run takes every core. How much of a machine, and which machine,
    is one question and it belongs to a declared execution backend."""
    assert "--jobs" not in runner.invoke(main, ["materialize", "--help"]).output


def test_a_run_with_nothing_to_do_exits_zero(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch)

    result = runner.invoke(main, ["materialize"])

    assert result.exit_code == 0
    assert "nothing to do" in result.output


def test_check_exits_nonzero_when_something_would_run(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An agent has to be able to ask "is this current?" and read the
    answer off the exit status."""
    from lightcone.engine.materialize import MaterializeReport

    _stub(monkeypatch, check=MaterializeReport(planned={"baseline/fit": "no manifest"}))

    result = runner.invoke(main, ["materialize", "--check"])

    assert result.exit_code == 1
    assert "would run baseline/fit" in result.output


def test_a_failure_exits_nonzero(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lightcone.engine.materialize import MaterializeReport

    _stub(
        monkeypatch,
        materialize=MaterializeReport(failed=["baseline/fit"], blocked=["baseline/report"]),
    )

    result = runner.invoke(main, ["materialize"])

    assert result.exit_code == 1
    assert "failed baseline/fit" in result.output
    assert "blocked baseline/report" in result.output


def test_the_json_report_is_machine_readable(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lightcone.engine.materialize import MaterializeReport

    _stub(monkeypatch, materialize=MaterializeReport(made=["baseline/fit"]))

    result = runner.invoke(main, ["materialize", "--json"])

    assert json.loads(result.output) == {
        "ok": True,
        "up_to_date": False,
        "made": ["baseline/fit"],
        "current": [],
        "behind": {},
        "failed": [],
        "blocked": [],
        "planned": {},
        "warnings": [],
        "notes": [],
    }


def test_an_engine_refusal_is_a_clean_error(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dirty tree, a lock that cannot be audited — the user sees the
    message, never a traceback."""
    from lightcone.engine import materialize as engine
    from lightcone.engine.project import ProjectError

    def refuse(root: Path, targets: object, **kwargs: object) -> object:
        raise ProjectError("uncommitted changes in the project")

    monkeypatch.setattr(engine, "materialize", refuse)

    result = runner.invoke(main, ["materialize"])

    assert result.exit_code == 1
    assert "uncommitted changes" in result.output
    assert "Traceback" not in result.output


# =============================================================================
# lc status
# =============================================================================


def _status_stub(monkeypatch: pytest.MonkeyPatch, report: object) -> None:
    from lightcone.engine import materialize as engine

    monkeypatch.setattr(engine, "status", lambda root: report)


def _report() -> object:
    from lightcone.engine.materialize import OutputStatus, StatusReport

    return StatusReport(
        outputs=[
            OutputStatus("baseline/first", "current", "", "3f2a1c8ffff", "sha256:one"),
            OutputStatus(
                "baseline/second",
                "behind",
                "made under an earlier environment",
                "3f2a1c8ffff",
                "sha256:two",
            ),
            OutputStatus("baseline/third", "stale", "the input `first` changed", "", ""),
        ]
    )


def test_status_shows_each_output_its_state_and_its_commit(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _status_stub(monkeypatch, _report())

    result = runner.invoke(main, ["status"])

    assert result.exit_code == 0
    for fragment in ("baseline/first", "current", "behind", "stale", "3f2a1c8"):
        assert fragment in result.output


def test_status_exits_zero_even_with_stale_outputs(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It reports; it does not gate. `lc materialize --check` is the gate,
    and two verbs answering the same question with different exit codes is
    how a script comes to depend on the wrong one."""
    _status_stub(monkeypatch, _report())

    assert runner.invoke(main, ["status"]).exit_code == 0


def test_status_json_is_machine_readable(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _status_stub(monkeypatch, _report())

    result = runner.invoke(main, ["status", "--json"])

    assert json.loads(result.output) == {
        "counts": {"current": 1, "behind": 1, "stale": 1},
        "outputs": [
            {
                "output": "baseline/first",
                "status": "current",
                "why": "",
                "git_sha": "3f2a1c8ffff",
                "data_version": "sha256:one",
            },
            {
                "output": "baseline/second",
                "status": "behind",
                "why": "made under an earlier environment",
                "git_sha": "3f2a1c8ffff",
                "data_version": "sha256:two",
            },
            {
                "output": "baseline/third",
                "status": "stale",
                "why": "the input `first` changed",
                "git_sha": "",
                "data_version": "",
            },
        ],
        "warnings": [],
    }


def test_status_has_exactly_one_flag(runner: CliRunner) -> None:
    """Minimal by decision: it answers one question, and every way of
    narrowing it is a way of getting a partial answer to that question."""
    # The options block alone: the prose above it points at
    # `lc materialize --check`, which is a different verb's flag.
    options = runner.invoke(main, ["status", "--help"]).output.partition("Options:")[2]
    assert "--json" in options
    for flag in ("--check", "--refresh", "--verbose", "--all"):
        assert flag not in options
