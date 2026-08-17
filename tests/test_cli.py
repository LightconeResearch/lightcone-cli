"""Tests for the lightcone CLI surface."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner
from conftest import PYPROJECT_MIN, PYTHON_VERSION_MIN, UV_LOCK_MIN

from lightcone.cli.commands import main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``~`` to a temp dir so tests can't touch the user's real
    home."""
    fake_home = tmp_path / "_home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    return fake_home


@pytest.fixture(autouse=True)
def _fake_uv(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Fake the uv seam so init tests are hermetic (no network, no real
    resolution). Emulates the observable effects: ``uv lock`` writes
    uv.lock, ``uv sync`` materializes .venv."""
    calls: list[list[str]] = []

    def fake_run_uv(args: list[str], *, cwd: Path) -> MagicMock:
        calls.append(list(args))
        if args[0] == "lock":
            project = Path(args[args.index("--project") + 1])
            (project / "uv.lock").write_text(UV_LOCK_MIN)
        elif args[0] == "sync":
            project = Path(args[args.index("--project") + 1])
            (project / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
        return MagicMock(returncode=0, stdout="", stderr="")

    from lightcone.cli import commands

    monkeypatch.setattr(commands, "_run_uv", fake_run_uv)
    monkeypatch.setattr(commands.shutil, "which", lambda name: f"/usr/bin/{name}")
    return calls


# ---- top-level ------------------------------------------------------------


def test_help_lists_core_commands(runner: CliRunner) -> None:
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    for cmd in ("init", "materialize", "run", "status", "verify", "build"):
        assert cmd in result.output


def test_help_does_not_advertise_removed_commands(runner: CliRunner) -> None:
    result = runner.invoke(main, ["--help"])
    assert "  dev " not in result.output
    assert "  cluster " not in result.output
    assert "  setup " not in result.output


def test_engine_errors_render_cleanly(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ProjectEnvironmentError from any command surfaces as a one-line
    CLI error (exit 1), not a traceback — the group boundary translates
    it."""
    from lightcone.cli import commands
    from lightcone.engine.environment import ProjectEnvironmentError

    def _boom(*args: object, **kwargs: object) -> Path:
        raise ProjectEnvironmentError("no uv.lock — run `uv lock`")

    monkeypatch.setattr(commands, "_project_root", _boom)
    result = runner.invoke(main, ["status"])
    assert result.exit_code == 1
    assert "uv.lock" in result.output
    assert "Traceback" not in result.output


# ---- lc init --------------------------------------------------------------


def test_init_creates_project(runner: CliRunner, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    result = runner.invoke(main, ["init", str(project), "--no-git"])
    assert result.exit_code == 0, result.output
    assert (project / "astra.yaml").exists()
    assert (project / "pyproject.toml").exists()
    assert (project / ".python-version").exists()
    assert (project / "uv.lock").exists()
    assert (project / ".venv").is_dir()
    assert (project / "AGENTS.md").exists()
    assert (project / ".gitignore").exists()
    assert (project / ".lightcone").is_dir()
    assert (project / "results").is_dir()
    assert (project / "universes").is_dir()
    # The README is the durable hint that outputs materialize here via
    # lc materialize — and the one file in results/ that stays tracked by git.
    readme = (project / "results" / "README.md").read_text()
    assert "lc materialize" in readme
    gitignore = (project / ".gitignore").read_text()
    assert "results/*" in gitignore
    assert "!results/README.md" in gitignore
    assert ".venv/" in gitignore
    assert ".lightcone/image/" in gitignore


def test_init_uv_scaffold_content(runner: CliRunner, tmp_path: Path) -> None:
    """The scaffolded uv project: virtual (no build-system), the engine
    inside the experiment's lock, an exact interpreter pin."""
    project = tmp_path / "proj"
    result = runner.invoke(main, ["init", str(project), "--no-git"])
    assert result.exit_code == 0, result.output

    pyproject = (project / "pyproject.toml").read_text()
    assert "lightcone-cli" in pyproject
    assert "[build-system]" not in pyproject
    assert "[tool.uv]" in pyproject

    pin = (project / ".python-version").read_text().strip()
    assert pin.count(".") == 2  # exact patch, e.g. 3.12.12

    agents = (project / "AGENTS.md").read_text()
    assert "uv add" in agents
    assert "lc materialize" in agents


def test_init_no_containerfile_scaffolded(runner: CliRunner, tmp_path: Path) -> None:
    """v6: images are generated from the lock — no authored Containerfile,
    no requirements.txt."""
    project = tmp_path / "proj"
    result = runner.invoke(main, ["init", str(project), "--no-git"])
    assert result.exit_code == 0, result.output
    assert not (project / "Containerfile").exists()
    assert not (project / "requirements.txt").exists()
    # And the astra boilerplate's container: line is stripped.
    assert "container:" not in (project / "astra.yaml").read_text()


def test_init_refuses_authored_containerfile(
    runner: CliRunner, tmp_path: Path
) -> None:
    """The user's own file operation is the consent to migrate — init
    refuses with instructions, even under --check."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "Containerfile").write_text("FROM python:3.12-slim\n")
    for extra in ([], ["--check"]):
        result = runner.invoke(main, ["init", str(project), "--no-git", *extra])
        assert result.exit_code != 0
        assert "delete or rename" in result.output
    assert (project / "Containerfile").read_text() == "FROM python:3.12-slim\n"


def test_init_invokes_uv_lock_and_sync(
    runner: CliRunner, tmp_path: Path, _fake_uv: list[list[str]]
) -> None:
    project = tmp_path / "proj"
    result = runner.invoke(main, ["init", str(project), "--no-git"])
    assert result.exit_code == 0, result.output
    assert ["lock", "--project", str(project)] in _fake_uv
    assert [
        "sync", "--locked", "--exact", "--compile-bytecode",
        "--project", str(project),
    ] in _fake_uv


def test_init_no_sync_skips_venv(
    runner: CliRunner, tmp_path: Path, _fake_uv: list[list[str]]
) -> None:
    project = tmp_path / "proj"
    result = runner.invoke(main, ["init", str(project), "--no-git", "--no-sync"])
    assert result.exit_code == 0, result.output
    assert (project / "uv.lock").exists()
    assert not (project / ".venv").exists()
    assert not any(args[0] == "sync" for args in _fake_uv)


def test_init_requires_uv(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lightcone.cli import commands

    monkeypatch.setattr(commands.shutil, "which", lambda _: None)
    result = runner.invoke(main, ["init", str(tmp_path / "proj"), "--no-git"])
    assert result.exit_code != 0
    assert "uv is required" in result.output


def test_init_adopts_existing_project(runner: CliRunner, tmp_path: Path) -> None:
    """A directory that already holds an astra.yaml is converged, not rejected,
    and user-owned files are never overwritten."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "astra.yaml").write_text("# user spec\n")
    (project / ".gitignore").write_text("*.log\n")
    (project / "pyproject.toml").write_text(PYPROJECT_MIN)
    result = runner.invoke(main, ["init", str(project), "--no-git"])
    assert result.exit_code == 0, result.output
    # User files untouched (gitignore gains the managed block, keeps content).
    assert (project / "astra.yaml").read_text() == "# user spec\n"
    assert (project / "pyproject.toml").read_text() == PYPROJECT_MIN
    gitignore = (project / ".gitignore").read_text()
    assert gitignore.startswith("*.log\n")
    assert "# lightcone-cli" in gitignore
    # Missing lightcone pieces were created.
    assert (project / ".lightcone" / "lightcone.yaml").exists()
    assert (project / ".python-version").exists()


def test_init_warns_when_pyproject_lacks_engine(
    runner: CliRunner, tmp_path: Path
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "p"\nversion = "0"\ndependencies = ["numpy"]\n'
    )
    result = runner.invoke(main, ["init", str(project), "--no-git", "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert any("lightcone-cli" in w for w in report["warnings"])


def test_init_is_idempotent(runner: CliRunner, tmp_path: Path) -> None:
    """A second run reports everything unchanged and rewrites nothing."""
    project = tmp_path / "proj"
    result = runner.invoke(main, ["init", str(project), "--no-git"])
    assert result.exit_code == 0, result.output
    before = {p: p.read_text() for p in project.rglob("*") if p.is_file()}

    result = runner.invoke(main, ["init", str(project), "--no-git", "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["converged"] is True
    assert report["created"] == []
    assert report["repaired"] == []
    assert {p: p.read_text() for p in project.rglob("*") if p.is_file()} == before

    # Managed blocks must not be duplicated across runs.
    assert (project / ".gitignore").read_text().count("# lightcone-cli") == 1
    assert (project / "AGENTS.md").read_text().count("<!-- lightcone-cli -->") == 1


def test_init_check_reports_drift_without_writing(
    runner: CliRunner, tmp_path: Path
) -> None:
    project = tmp_path / "proj"
    result = runner.invoke(
        main, ["init", str(project), "--no-git", "--check", "--json"]
    )
    assert result.exit_code == 1
    report = json.loads(result.output)
    assert report["converged"] is False
    assert "astra.yaml" in report["created"]
    assert "pyproject.toml" in report["created"]
    assert "uv.lock" in report["created"]
    assert not project.exists()  # --check writes nothing, not even the dir


def test_init_check_passes_on_converged_project(
    runner: CliRunner, tmp_path: Path
) -> None:
    project = tmp_path / "proj"
    result = runner.invoke(main, ["init", str(project), "--no-git"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(main, ["init", str(project), "--no-git", "--check"])
    assert result.exit_code == 0, result.output


def test_init_survives_malformed_lightcone_yaml(
    runner: CliRunner, tmp_path: Path
) -> None:
    """'Safe to re-run at any time' includes a corrupted project config:
    warn and leave it alone rather than crashing with a YAML traceback."""
    project = tmp_path / "proj"
    (project / ".lightcone").mkdir(parents=True)
    (project / ".lightcone" / "lightcone.yaml").write_text("target: [unclosed\n")

    result = runner.invoke(
        main,
        ["init", str(project), "--no-git", "--scratch", "$SCRATCH", "--json"],
    )
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert any("lightcone.yaml" in w for w in report["warnings"])
    assert (project / ".lightcone" / "lightcone.yaml").read_text() == "target: [unclosed\n"

    # A non-mapping file must not crash the --scratch merge either.
    (project / ".lightcone" / "lightcone.yaml").write_text("local\n")
    result = runner.invoke(
        main,
        ["init", str(project), "--no-git", "--scratch", "$SCRATCH", "--json"],
    )
    assert result.exit_code == 0, result.output


def test_init_repairs_missing_piece(runner: CliRunner, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    result = runner.invoke(main, ["init", str(project), "--no-git"])
    assert result.exit_code == 0, result.output
    (project / ".python-version").unlink()

    result = runner.invoke(main, ["init", str(project), "--no-git", "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert ".python-version" in report["created"]
    assert (project / ".python-version").exists()
    # The rest was left alone.
    assert "astra.yaml" in report["unchanged"]


def test_lightcone_requirement_pins_running_version() -> None:
    from importlib.metadata import version

    from lightcone.cli.commands import _lightcone_requirement

    req = _lightcone_requirement()
    v = version("lightcone-cli")
    if "dev" in v:
        # Dev builds aren't published — unpinned fallback.
        assert req == "lightcone-cli"
    else:
        assert req == f"lightcone-cli=={v}"


# ---- lc run (probe verb) ---------------------------------------------------


def _probe_project(tmp_path: Path, *, with_env: bool = True) -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "astra.yaml").write_text(
        "outputs:\n  - id: best_fit\n    recipe:\n      command: echo hi\n"
    )
    if with_env:
        (project / "pyproject.toml").write_text(PYPROJECT_MIN)
        (project / "uv.lock").write_text(UV_LOCK_MIN)
        (project / ".python-version").write_text(PYTHON_VERSION_MIN)
    return project


def test_run_rename_guard_fires_on_output_id(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`lc run <output_id>` is the old pipeline grammar — it must error
    with the materialize hint before exec'ing anything."""
    project = _probe_project(tmp_path)
    monkeypatch.chdir(project)
    result = runner.invoke(main, ["run", "best_fit"])
    assert result.exit_code != 0
    assert "lc materialize best_fit" in result.output
    assert "materialized, not run" in result.output


def test_run_requires_uv_project(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _probe_project(tmp_path, with_env=False)
    monkeypatch.chdir(project)
    result = runner.invoke(main, ["run", "python", "-V"])
    assert result.exit_code != 0
    assert "pyproject.toml" in result.output


def test_run_probes_through_uv_run(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The probe delegates to `uv run --locked --exact` from the project
    root — byte-for-byte the recipe environment."""
    project = _probe_project(tmp_path)
    monkeypatch.chdir(project)
    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        calls.append(list(cmd))
        return MagicMock(returncode=0)

    monkeypatch.setattr(subprocess, "run", _fake_run)
    result = runner.invoke(main, ["run", "python", "-V"])
    assert result.exit_code == 0, result.output
    assert calls, "probe never exec'd"
    argv = calls[0]
    assert argv[:5] == ["uv", "run", "--locked", "--exact", "--project"]
    assert argv[-3:] == ["--", "python", "-V"]


def test_run_refuses_containerized_interim(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _probe_project(tmp_path)
    (project / "pyproject.toml").write_text(
        PYPROJECT_MIN + "\n[tool.lightcone.image]\n"
    )
    monkeypatch.chdir(project)
    result = runner.invoke(main, ["run", "python", "-V"])
    assert result.exit_code != 0
    assert "containerized" in result.output


# ---- lc build --------------------------------------------------------------


def test_build_direct_mode_is_explanatory_noop(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _probe_project(tmp_path)
    monkeypatch.chdir(project)
    result = runner.invoke(main, ["build"])
    assert result.exit_code == 0, result.output
    assert "direct mode" in result.output
    assert "[tool.lightcone.image]" in result.output


# ---- lc verify ------------------------------------------------------------


def test_verify_clean_project_returns_zero(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty project (no materialized outputs yet) is a clean state, not
    a verification failure."""
    project = _probe_project(tmp_path)
    monkeypatch.chdir(project)
    result = runner.invoke(main, ["verify"])
    assert result.exit_code == 0


# ---- lc status header ------------------------------------------------------


def test_status_header_lines(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _probe_project(tmp_path)
    monkeypatch.chdir(project)
    result = runner.invoke(main, ["status"])
    assert result.exit_code == 0, result.output
    assert "mode:" in result.output
    assert "direct" in result.output
    assert "image:" in result.output
    assert "sandbox:" in result.output


# ---- lc materialize command building ---------------------------------------


def test_materialize_cmd_inserts_separator_before_targets() -> None:
    """Regression test for issue #87.

    snakemake's --rerun-triggers uses nargs=+ so it greedily consumes the
    first positional target path as an extra trigger value, producing:
        error: argument --rerun-triggers: invalid choice:
        'results/baseline/map_fit/.lightcone-manifest.json'
    A '--' separator between the trigger values and target paths terminates
    argparse flag processing and prevents this.
    """
    from lightcone.cli.commands import _build_snakemake_cmd

    targets = ["results/baseline/map_fit/.lightcone-manifest.json"]
    cmd = _build_snakemake_cmd(
        snakefile_path=Path("/proj/.lightcone/Snakefile"),
        project=Path("/proj"),
        n="4",
        rerun_triggers="code,input,mtime,params",
        targets=targets,
        force=False,
        has_outputs=True,
    )

    assert "--" in cmd, "missing '--' separator; first target will be consumed as a trigger value"
    sep_idx = cmd.index("--")
    rt_idx = cmd.index("--rerun-triggers")
    assert sep_idx > rt_idx, "'--' must appear after --rerun-triggers"
    target_idx = cmd.index(targets[0])
    assert target_idx > sep_idx, "target path must appear after '--'"


def test_materialize_cmd_no_separator_when_no_targets() -> None:
    """When no targets are supplied snakemake runs 'rule all'; '--' is unnecessary."""
    from lightcone.cli.commands import _build_snakemake_cmd

    cmd = _build_snakemake_cmd(
        snakefile_path=Path("/proj/.lightcone/Snakefile"),
        project=Path("/proj"),
        n="4",
        rerun_triggers="code,input,mtime,params",
        targets=[],
        force=False,
        has_outputs=False,
    )

    assert "--" not in cmd


def test_materialize_cmd_multiple_triggers_all_before_separator() -> None:
    """All four trigger tokens must precede the '--' separator."""
    from lightcone.cli.commands import _build_snakemake_cmd

    targets = ["results/baseline/out/.lightcone-manifest.json"]
    cmd = _build_snakemake_cmd(
        snakefile_path=Path("/proj/.lightcone/Snakefile"),
        project=Path("/proj"),
        n="1",
        rerun_triggers="code,input,mtime,params",
        targets=targets,
        force=False,
        has_outputs=True,
    )

    sep_idx = cmd.index("--")
    for trigger in ("code", "input", "mtime", "params"):
        assert trigger in cmd, f"trigger '{trigger}' missing from cmd"
        assert cmd.index(trigger) < sep_idx, f"trigger '{trigger}' must come before '--'"


def test_materialize_cmd_shape() -> None:
    """One invocation shape: --shared-fs-usage drops software-deployment
    so spawned jobs run plain `python` from the worker's own environment
    instead of embedding the driver's sys.executable."""
    from lightcone.cli.commands import _build_snakemake_cmd

    cmd = _build_snakemake_cmd(
        snakefile_path=Path("/p/.lightcone/Snakefile"),
        project=Path("/p"),
        n="4",
        rerun_triggers="mtime",
        targets=["results/u/foo/.lightcone-manifest.json"],
        force=False,
        has_outputs=True,
    )
    j = cmd.index("--shared-fs-usage")
    values = cmd[j + 1 : cmd.index("--rerun-triggers")]
    assert "software-deployment" not in values
    assert "persistence" in values and "input-output" in values
    assert "--latency-wait" not in cmd
    # nargs=+ flags must never swallow the positional targets.
    assert cmd.index("--") < cmd.index("results/u/foo/.lightcone-manifest.json")


def test_materialize_refuses_containerized_interim(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _probe_project(tmp_path)
    (project / "pyproject.toml").write_text(
        PYPROJECT_MIN + "\n[tool.lightcone.image]\n"
    )
    monkeypatch.chdir(project)
    result = runner.invoke(main, ["materialize"])
    assert result.exit_code != 0
    assert "containerized" in result.output
