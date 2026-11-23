"""Tests for the CLI surface — flags, rendering, exit codes.

Convergence *semantics* are tested against the engine in
`tests/test_project.py`; this file covers only what the CLI adds on top.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner
from conftest import CLUSTER_ID

from lightcone.cli import commands
from lightcone.cli.commands import main

# ---- top level ------------------------------------------------------------


def test_help_advertises_exactly_the_verbs_that_work(runner: CliRunner) -> None:
    """`lc --help` advertises only verbs that work. The unit a run record
    names is machinery, not a verb: it makes a result unconditionally,
    commits nothing, and leaves the tree dirty by design — the state
    `lc materialize` refuses to start from."""
    result = runner.invoke(main, ["--help"])

    assert result.exit_code == 0
    listed = re.findall(r"^  (\S+)  ", result.output.partition("Commands:")[2], re.MULTILINE)
    assert listed == [
        "build", "compute", "fetch", "init", "materialize", "publish", "run", "status",
    ]


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


# ---- the palette and the banner ---------------------------------------------


@pytest.fixture
def at_a_terminal(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Have `lc` print as it does for a person: to a terminal with every
    colour. The console is built once, so the one built here is dropped
    again after the test."""
    for name, value in (("FORCE_COLOR", "1"), ("COLORTERM", "truecolor"), ("TERM", "xterm")):
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("NO_COLOR", raising=False)
    commands._console.cache_clear()
    yield
    commands._console.cache_clear()


def test_init_opens_with_the_banner_at_a_terminal(
    runner: CliRunner, tmp_path: Path, at_a_terminal: None
) -> None:
    """The mark, the tool's name and what it is, in the palette's gold, and
    the check marks in its Vert de Gris."""
    result = runner.invoke(main, ["init", str(tmp_path / "proj")])

    assert result.exit_code == 0, result.output
    assert "L I G H T C O N E" in result.output
    assert "THE EXECUTION LAYER FOR ASTRA" in result.output
    assert "\x1b[38;2;166;124;60m" in result.output
    assert "\x1b[38;2;88;142;136m✓" in result.output


def test_a_transcript_gets_no_banner(runner: CliRunner, tmp_path: Path) -> None:
    """An agent runs `lc init` more often than a person does, and reads
    every line of it: where the output is no terminal, there is no logo
    and no colour, only the report."""
    result = runner.invoke(main, ["init", str(tmp_path / "proj")])

    assert result.exit_code == 0, result.output
    assert "L I G H T C O N E" not in result.output
    assert "\x1b[" not in result.output
    assert "✓ created astra.yaml" in result.output


def test_a_dry_run_has_no_banner_even_at_a_terminal(
    runner: CliRunner, tmp_path: Path, at_a_terminal: None
) -> None:
    result = runner.invoke(main, ["init", "--check", str(tmp_path / "proj")])

    assert "L I G H T C O N E" not in result.output
    assert "would create" in result.output


def test_every_tone_the_marks_and_the_banner_name_is_in_the_palette() -> None:
    """rich leaves a tone it does not know unstyled, without a word: a
    misspelt one would print in the terminal's own colour, and no output a
    test captures would differ."""
    marks = (
        commands._DONE, commands._TODO, commands._WARN,
        commands._FAILED, commands._BEHIND, commands._QUIET,
    )
    named = set(re.findall(r"\[([a-z]+)\]", "".join((*marks, commands._BANNER))))

    assert named - {"bold"} == {"gold", "ink", "slate", "verdigris", "wax", "mushroom"}
    assert named - {"bold"} <= commands._PALETTE.keys()


def test_every_row_of_the_mark_is_as_wide() -> None:
    """The mark is ten braille characters a row, its blank cells included:
    a space in their place is narrower wherever braille comes from another
    font, and the name beside the mark would no longer line up."""
    from rich.text import Text

    rows = [line for line in Text.from_markup(commands._BANNER).plain.splitlines() if line]

    assert len(rows) == 6
    for row in rows:
        assert row[:2] == "  "
        assert all("\u2800" <= cell <= "\u28ff" for cell in row[2:12]), row
        assert row[12:13] in ("", " ")


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


@pytest.mark.parametrize("cluster", [CLUSTER_ID, "analysis"])
def test_run_passes_the_command_through_untouched(
    runner: CliRunner, project: Path, spawned: list[dict[str, object]], cluster: str,
) -> None:
    """A probe's command has its own flags, and they belong to it — not
    to us. `--help` after the command must reach the command."""
    result = runner.invoke(main, ["run", cluster, "--", "python", "-c", "print(1)", "--help"])
    assert result.exit_code == 0
    assert spawned[0]["command"] == ["python", "-c", "print(1)", "--help"]
    assert spawned[0]["project"] == project.resolve()
    assert spawned[0]["cluster_id"] == cluster


@pytest.mark.parametrize("argv", [["run"], ["materialize"], ["run", "bad:name", "true"],
                                 ["materialize", "first#baseline"]])
def test_execution_requires_an_allocation_identity_before_project_discovery(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(main, argv)
    assert result.exit_code == 2
    assert "CLUSTER" in result.output
    assert "not a Lightcone project" not in result.output


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
    assert runner.invoke(main, ["run", CLUSTER_ID, "--", "false"]).exit_code == 42


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
    assert runner.invoke(main, ["run", CLUSTER_ID, "--", "sleep"]).exit_code == 137


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
    result = runner.invoke(main, ["run", CLUSTER_ID, "--", "latex"])
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
    result = runner.invoke(main, ["run", CLUSTER_ID, "--", "true"])
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
    result = runner.invoke(main, ["run", CLUSTER_ID, "--", "true"])
    assert result.exit_code == 1
    assert "not a Lightcone project" in result.output


def test_run_needs_no_spec_file(
    runner: CliRunner, project: Path, spawned: list[dict[str, object]]
) -> None:
    """The environment is what a probe needs; `astra.yaml` is not
    required to run a command in it."""
    assert not (project / "astra.yaml").exists()
    result = runner.invoke(main, ["run", CLUSTER_ID, "--", "true"])
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


@pytest.mark.parametrize("cluster", [CLUSTER_ID, "analysis"])
def test_targets_reach_the_engine(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch, cluster: str,
) -> None:
    seen = _stub(monkeypatch)

    result = runner.invoke(main, ["materialize", cluster, "fit#baseline", "report"])
    assert result.exit_code == 0, result.output

    assert seen == [
        ("materialize", (["fit#baseline", "report"], {"refresh": False, "cluster_id": cluster}))
    ]


def test_refresh_reaches_both_modes(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--check` has to answer the question the run would ask, or the gate
    reports on a run nobody is going to make."""
    seen = _stub(monkeypatch)

    runner.invoke(main, ["materialize", CLUSTER_ID, "--refresh"])
    runner.invoke(main, ["materialize", "--check", "--refresh"])

    assert seen == [
        ("materialize", ([], {"refresh": True, "cluster_id": CLUSTER_ID})),
        ("check", ([], {"refresh": True})),
    ]


def test_there_is_no_flag_to_stop_a_stale_result_being_remade(runner: CliRunner) -> None:
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

    result = runner.invoke(main, ["materialize", CLUSTER_ID])

    assert result.exit_code == 0
    assert "nothing to do" in result.output


def test_check_exits_nonzero_when_something_would_run(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An agent has to be able to ask "is this up to date?" and read the
    answer off the exit status."""
    from lightcone.engine.materialize import MaterializeReport

    _stub(monkeypatch, check=MaterializeReport(planned={"fit#baseline": "no record"}))

    result = runner.invoke(main, ["materialize", "--check"])

    assert result.exit_code == 1
    assert "would run fit#baseline" in result.output


def test_check_treats_all_positionals_as_targets_without_querying_compute(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lightcone.engine import compute

    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("check contacted compute")

    monkeypatch.setattr(compute, "connect", unexpected)
    monkeypatch.setattr(compute, "validate_id", unexpected)
    seen = _stub(monkeypatch)
    result = runner.invoke(main, ["materialize", "--check", "first#baseline"])
    assert result.exit_code == 0
    assert seen == [("check", (["first#baseline"], {"refresh": False}))]


def test_check_explains_that_a_cluster_id_is_not_a_target(runner: CliRunner) -> None:
    result = runner.invoke(main, ["materialize", "--check", CLUSTER_ID])
    assert result.exit_code == 2
    assert "omit the ID" in result.output


@pytest.mark.parametrize("command", ["run", "materialize"])
@pytest.mark.parametrize("cluster", [CLUSTER_ID, "analysis"])
def test_execution_interrupt_explains_how_to_stop_remote_work(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch, command: str,
    cluster: str,
) -> None:
    from lightcone.engine import materialize as engine_materialize
    from lightcone.engine import run as engine_run

    def interrupt(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(engine_run, "probe", interrupt)
    monkeypatch.setattr(engine_materialize, "materialize", interrupt)
    args = [command, cluster, "--", "true"] if command == "run" else [command, cluster]
    result = runner.invoke(main, args)
    assert result.exit_code != 0
    target = cluster if cluster == CLUSTER_ID else "<full-id>"
    assert f"lc compute down {target}" in result.output
    if cluster != CLUSTER_ID:
        assert "cluster names can be reused" in result.output
        assert f"lc compute down {cluster}" not in result.output


def test_a_failure_exits_nonzero(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lightcone.engine.materialize import MaterializeReport

    _stub(
        monkeypatch,
        materialize=MaterializeReport(failed=["fit#baseline"], blocked=["report#baseline"]),
    )

    result = runner.invoke(main, ["materialize", CLUSTER_ID])

    assert result.exit_code == 1
    assert "failed fit#baseline" in result.output
    assert "blocked report#baseline" in result.output


def test_engine_prose_is_rendered_as_written(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A warning naming `[project].license` must reach the terminal with
    its brackets — rich would otherwise read them as a style tag and
    swallow the word, turning the remedy into "no .license"."""
    from lightcone.engine.materialize import MaterializeReport

    _stub(
        monkeypatch,
        materialize=MaterializeReport(warnings=["no [project].license declared"]),
    )

    result = runner.invoke(main, ["materialize", CLUSTER_ID])

    assert "no [project].license declared" in result.output


def test_the_json_report_is_machine_readable(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lightcone.engine.materialize import MaterializeReport

    _stub(monkeypatch, materialize=MaterializeReport(made=["fit#baseline"]))

    result = runner.invoke(main, ["materialize", CLUSTER_ID, "--json"])

    assert json.loads(result.output) == {
        "ok": True,
        "up_to_date": False,
        "made": ["fit#baseline"],
        "unchanged": [],
        "behind": {},
        "failed": [],
        "blocked": [],
        "planned": {},
        "warnings": [],
        "notes": [],
    }


def test_what_is_behind_is_said_with_what_remakes_it(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A result behind its code alone has no reason beside it. With
    --refresh, what is still behind is what the targets read."""
    from lightcone.engine.materialize import MaterializeReport

    _stub(monkeypatch, materialize=MaterializeReport(made=["plot"], behind={"fit": ""}))

    plain = runner.invoke(main, ["materialize", CLUSTER_ID]).output
    refreshed = runner.invoke(main, ["materialize", CLUSTER_ID, "plot", "--refresh"]).output

    assert "behind fit\n" in plain
    assert "1 behind — `--refresh` remakes them" in " ".join(plain.split())
    assert "1 behind, read by the targets" in " ".join(refreshed.split())


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

    result = runner.invoke(main, ["materialize", CLUSTER_ID])

    assert result.exit_code == 1
    assert "uncommitted changes" in result.output
    assert "Traceback" not in result.output


# =============================================================================
# lc status
# =============================================================================


def _status_stub(monkeypatch: pytest.MonkeyPatch, report: object) -> None:
    from lightcone.engine import materialize as engine

    monkeypatch.setattr(engine, "status", lambda root: report)


_ENV = "urn:lightcone:env:sha256:now"


def _astra(state: str, commit: str = "", environment: str = _ENV) -> dict[str, object]:
    """A status as ASTRA's checker reports it in JSON, cut to what the
    renderer reads."""
    if not commit:
        return {"state": state, "why": [{"code": "MISSING_RECORD"}]}
    record = {"generated_by": {"commit": commit, "environment": {"iri": environment}}}
    return {"state": state, "record": record}


def _report() -> object:
    from lightcone.engine.materialize import ResultStatus, StatusReport

    return StatusReport(
        results=[
            ResultStatus(
                "first#baseline", "ok", "", False, [], _astra("unchanged", "3f2a1c8ffff")
            ),
            ResultStatus(
                "second#baseline",
                "behind",
                "made under an earlier environment",
                False,
                [],
                _astra("unchanged", "3f2a1c8ffff", "urn:lightcone:env:sha256:old"),
            ),
            ResultStatus(
                "third#baseline",
                "missing",
                "no record is at the place",
                False,
                [],
                _astra("missing"),
            ),
        ],
        environment=_ENV,
        online=True,
    )


def test_status_shows_each_result_its_state_and_its_commit(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _status_stub(monkeypatch, _report())

    result = runner.invoke(main, ["status"])

    assert result.exit_code == 0
    for fragment in ("first#baseline", " ok ", "behind", "missing", "3f2a1c8"):
        assert fragment in result.output


def test_status_says_each_results_state_and_not_why(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The state is the answer at this level; the reasons are --json's and
    `lc materialize --check`'s. Nor is there a header in direct mode."""
    _status_stub(monkeypatch, _report())

    output = runner.invoke(main, ["status"]).output

    for fragment in ("earlier environment", "no record", "mode:", "sandbox:"):
        assert fragment not in output
    assert output.splitlines()[0].split()[:2] == ["·", "ok"]


def test_status_says_which_current_results_are_unfetched_and_who_holds_them(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What is current needs a fetch, not a run, and only that is said of
    it: a result a run makes again needs no fetching, wherever it is."""
    from dataclasses import replace

    from lightcone.engine.materialize import StatusReport

    report = _report()
    assert isinstance(report, StatusReport)
    first, second, third = report.results
    report.results = [
        replace(first, unfetched=True, remotes=["origin", "backup"]),
        replace(second, unfetched=True, remotes=["origin"]),
        third,
    ]
    _status_stub(monkeypatch, report)

    lines = runner.invoke(main, ["status"]).output.splitlines()

    assert lines[0].endswith("unfetched, on origin, backup")
    assert lines[1].endswith("unfetched, on origin")
    assert "unfetched" not in lines[2]
    assert lines[-1].endswith("2 unfetched — `lc fetch` brings them")


def test_status_says_nothing_of_fetching_where_no_result_is_unfetched(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _status_stub(monkeypatch, _report())

    output = runner.invoke(main, ["status"]).output

    assert "unfetched" not in output and "lc fetch" not in output


def test_status_says_where_a_containerized_projects_image_stands(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing else surfaces whether the image a run needs is built."""
    from lightcone.engine.materialize import StatusReport

    report = _report()
    assert isinstance(report, StatusReport)
    report.image = {
        "tag": "lc-env-0123456789abcdef",
        "state": "absent",
        "archive": ".datalad/environments/lc-env-0123456789abcdef/image",
    }
    _status_stub(monkeypatch, report)

    output = runner.invoke(main, ["status"]).output

    assert "lc-env-0123456789abcdef" in output
    assert "needs build" in output and "lc build" in output


def test_build_on_a_direct_project_is_an_explanatory_no_op(
    runner: CliRunner, project: Path
) -> None:
    """Not an error: `lc build` answers "what would building do here",
    and for a direct project the answer names the escalation."""
    result = runner.invoke(main, ["build"])

    assert result.exit_code == 0
    assert "direct mode" in result.output
    assert "[tool.lightcone.image]" in result.output


def test_status_exits_zero_whatever_its_results_are(
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
        "image": None,
        "environment": _ENV,
        "online": True,
        "counts": {"ok": 1, "behind": 1, "stale": 0, "missing": 1},
        "results": [
            {
                "result": "first#baseline",
                "state": "ok",
                "why": "",
                "unfetched": False,
                "remotes": [],
                "astra": _astra("unchanged", "3f2a1c8ffff"),
            },
            {
                "result": "second#baseline",
                "state": "behind",
                "why": "made under an earlier environment",
                "unfetched": False,
                "remotes": [],
                "astra": _astra("unchanged", "3f2a1c8ffff", "urn:lightcone:env:sha256:old"),
            },
            {
                "result": "third#baseline",
                "state": "missing",
                "why": "no record is at the place",
                "unfetched": False,
                "remotes": [],
                "astra": _astra("missing"),
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


# =============================================================================
# lc fetch
# =============================================================================


def _fetch_stub(monkeypatch: pytest.MonkeyPatch, report: object) -> list[tuple[str, ...]]:
    from lightcone.engine import materialize as engine

    asked: list[tuple[str, ...]] = []

    def fetch(root: Path, targets: tuple[str, ...]) -> object:
        asked.append(targets)
        return report

    monkeypatch.setattr(engine, "fetch", fetch)
    return asked


def test_fetch_takes_targets_and_no_cluster(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fetching is a transfer, not a run: it borrows no allocation."""
    from lightcone.engine.materialize import FetchReport

    asked = _fetch_stub(monkeypatch, FetchReport(fetched=["fit#baseline"]))

    result = runner.invoke(main, ["fetch", "fit#baseline", "summary"])

    assert result.exit_code == 0, result.output
    assert asked == [("fit#baseline", "summary")]
    assert "fetched fit#baseline" in result.output


def test_fetch_says_a_result_a_run_makes_again_is_not_fetched(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lightcone.engine.materialize import FetchReport

    _fetch_stub(monkeypatch, FetchReport(left={"fit#baseline": "stale"}))

    result = runner.invoke(main, ["fetch"])

    assert result.exit_code == 0
    assert "stale fit#baseline" in result.output and "lc materialize" in result.output
    assert "Nothing to fetch" in result.output


def test_fetch_exits_one_when_a_result_could_not_be_fetched(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lightcone.engine.materialize import FetchReport

    _fetch_stub(
        monkeypatch,
        FetchReport(
            fetched=["fit#baseline"],
            failed={"summary#baseline": "Unable to access these remotes: origin"},
        ),
    )

    result = runner.invoke(main, ["fetch"])

    assert result.exit_code == 1
    assert "summary#baseline" in result.output and "origin" in result.output
    assert "1 result(s) could not be fetched" in result.output


def test_fetch_json_is_machine_readable(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lightcone.engine.materialize import FetchReport

    _fetch_stub(monkeypatch, FetchReport(fetched=["fit#baseline"], present=["summary#baseline"]))

    result = runner.invoke(main, ["fetch", "--json"])

    assert json.loads(result.output) == {
        "ok": True,
        "fetched": ["fit#baseline"],
        "present": ["summary#baseline"],
        "left": {},
        "failed": {},
        "inputs": [],
        "warnings": [],
    }


# ---- lc publish, and the notes that lead to it -------------------------------


def _publish_stub(monkeypatch: pytest.MonkeyPatch, report: object) -> list[dict[str, object]]:
    from lightcone.engine import publish as engine

    asked: list[dict[str, object]] = []

    def publish(root: Path, url: str = "", **options: object) -> object:
        asked.append({"url": url, **options})
        return report

    monkeypatch.setattr(engine, "publish", publish)
    return asked


def _published(**fields: object) -> object:
    from lightcone.engine.publish import PublishReport

    return PublishReport(
        **{
            "target": "github.com/alice/analysis",
            "visibility": "private",
            "store": "GitHub LFS of github.com/alice/analysis",
            "branch": "main",
            "commit": "3f2a1c8ffff",
            **fields,
        }  # type: ignore[arg-type]
    )


def test_publish_hands_the_two_choices_to_the_engine_and_makes_neither(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = _publish_stub(monkeypatch, _published())

    runner.invoke(main, ["publish", "github.com/alice/analysis", "--private", "--data", "/store"])
    runner.invoke(main, ["publish", "--public", "--confirm-data", "/store", "--check"])
    runner.invoke(main, ["publish"])

    assert asked == [
        {"url": "github.com/alice/analysis", "visibility": "private", "data": "/store",
         "confirm": "", "check": False},
        {"url": "", "visibility": "public", "data": "", "confirm": "/store", "check": True},
        {"url": "", "visibility": "", "data": "", "confirm": "", "check": False},
    ]  # fmt: skip


def test_publish_takes_one_answer_to_who_can_read_the_project(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = _publish_stub(monkeypatch, _published())

    result = runner.invoke(main, ["publish", "--private", "--public"])

    assert result.exit_code == 2 and not asked
    assert "two answers to one question" in result.output


def test_a_first_publication_says_what_it_created_sent_and_how_to_clone_it(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _publish_stub(
        monkeypatch,
        _published(
            created=True,
            declared=True,
            sent={"results/fit.txt": "result", "results/all.txt": "result", "data/c.txt": "input"},
            commits=4,
        ),
    )

    result = runner.invoke(main, ["publish", "github.com/alice/analysis", "--private"])

    assert result.exit_code == 0
    assert "created  github.com/alice/analysis, private" in result.output
    assert "data     2 results, 1 input → GitHub LFS of" in result.output
    assert "commits  main at 3f2a1c8 → github.com/alice/analysis" in result.output
    assert "git clone https://github.com/alice/analysis" in result.output


def test_a_publication_with_nothing_to_send_says_so(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _publish_stub(monkeypatch, _published(uncommitted=2))

    result = runner.invoke(main, ["publish"])

    assert result.exit_code == 0
    assert "is up to date — nothing to send" in result.output
    assert "2 uncommitted change(s) are not published" in result.output


def test_what_could_not_be_sent_fails_the_publication_and_what_nobody_holds_does_not(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _publish_stub(monkeypatch, _published(nowhere=["results/old.txt"], commits=1))
    held_nowhere = runner.invoke(main, ["publish"])
    _publish_stub(monkeypatch, _published(failed={"results/big.h5": "over the quota [x]"}))
    refused = runner.invoke(main, ["publish"])

    assert held_nowhere.exit_code == 0
    assert "1 file(s) with bytes in neither this clone nor the" in held_nowhere.output
    assert "results/old.txt" in held_nowhere.output
    assert refused.exit_code == 1
    assert "results/big.h5 — over the quota [x]" in refused.output
    assert "publishing again" in refused.output


def test_check_exits_1_while_anything_is_unpublished(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _publish_stub(monkeypatch, _published())
    held = runner.invoke(main, ["publish", "--check"])
    _publish_stub(monkeypatch, _published(unsent={"results/fit.txt": "result"}, unpushed=2))
    lacking = runner.invoke(main, ["publish", "--check"])

    assert held.exit_code == 0 and "holds main at 3f2a1c8, and its data" in held.output
    assert lacking.exit_code == 1
    assert "1 result to send" in lacking.output and "results/fit.txt" in lacking.output
    assert "2 of main to send" in lacking.output


def test_check_says_a_project_is_not_online_and_exits_1(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lightcone.engine.publish import PublishReport

    _publish_stub(monkeypatch, PublishReport())

    result = runner.invoke(main, ["publish", "--check"])

    assert result.exit_code == 1
    assert "Not online yet — `lc publish github.com/OWNER/proj --private`" in result.output


def test_going_public_says_what_becomes_readable(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _publish_stub(monkeypatch, _published(made_public=True, license="CC-BY-4.0"))
    with_lfs = runner.invoke(main, ["publish", "--public"]).output
    _publish_stub(
        monkeypatch, _published(made_public=True, license="CC-BY-4.0", store="/cfs/lcr/store")
    )
    with_a_directory = runner.invoke(main, ["publish", "--public"]).output

    assert "license  CC-BY-4.0" in with_lfs
    assert "github.com/alice/analysis is public, and its data in" in with_lfs
    assert "and its data stays in /cfs/lcr/store" in " ".join(with_a_directory.split())


def test_publish_json_is_machine_readable(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _publish_stub(monkeypatch, _published(sent={"results/fit.txt": "result"}, commits=1))

    answer = json.loads(runner.invoke(main, ["publish", "--json"]).output)

    assert answer["ok"] and answer["published"]
    assert answer["sent"] == {"results/fit.txt": "result"}
    assert answer["target"] == "github.com/alice/analysis"


def test_status_ends_with_one_line_while_the_project_is_not_online(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """And says nothing of publication once it is, even privately."""
    from dataclasses import replace

    report = _report()
    _status_stub(monkeypatch, replace(report, online=False))  # type: ignore[type-var]
    offline = runner.invoke(main, ["status"]).output.splitlines()
    _status_stub(monkeypatch, report)
    online = runner.invoke(main, ["status"]).output

    assert offline[-1].startswith("Not online yet — `lc publish github.com/OWNER/proj --private`")
    assert "lc publish" not in online and "online" not in online


def test_init_ends_with_how_to_put_a_new_project_online_and_creates_nothing_there(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "analysis").mkdir()
    monkeypatch.chdir(tmp_path / "analysis")
    result = runner.invoke(main, ["init"])

    assert result.exit_code == 0
    assert result.output.rstrip().splitlines()[-3:] == [
        "  Not online yet. To back it up and share it, choose who can read it:",
        "    lc publish github.com/OWNER/analysis --private",
        "    lc publish github.com/OWNER/analysis --public",
    ]
    assert "lc publish" not in runner.invoke(main, ["init", "--check", str(tmp_path / "b")]).output


def test_init_of_another_directory_says_where_to_publish_it_from(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`lc publish` acts on the current directory, not the one `lc init` was given."""
    monkeypatch.chdir(tmp_path)
    below = runner.invoke(main, ["init", "analysis"]).output.rstrip().splitlines()
    monkeypatch.chdir(tmp_path / "analysis")
    beside = runner.invoke(main, ["init", str(tmp_path / "my b")]).output

    assert below[-3:] == [
        "  Not online yet. To back it up and share it, choose who can read it:",
        "    cd analysis && lc publish github.com/OWNER/analysis --private",
        "    cd analysis && lc publish github.com/OWNER/analysis --public",
    ]
    assert f"    cd '{(tmp_path / 'my b').resolve()}' && lc publish" in beside


def test_init_says_nothing_of_publishing_for_a_project_that_is_online(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whether it is online is the engine's to say, in its report."""
    from lightcone.engine import project
    from lightcone.engine.project import ConvergenceReport

    online = ConvergenceReport(online=True)
    monkeypatch.setattr(project, "converge", lambda directory, write: online)

    result = runner.invoke(main, ["init", str(tmp_path / "analysis")])

    assert result.exit_code == 0 and "lc publish" not in result.output
