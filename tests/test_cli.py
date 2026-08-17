"""Tests for the redesigned lightcone CLI."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from lightcone.cli.commands import main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``~/.lightcone/`` to a temp dir so tests don't pollute the user's
    real config. The global config is auto-created on first ``lc`` invocation."""
    fake_home = tmp_path / "_home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    return fake_home


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



def test_init_creates_project(runner: CliRunner, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    result = runner.invoke(main, ["init", str(project), "--no-git", "--no-venv"])
    assert result.exit_code == 0, result.output
    assert (project / "astra.yaml").exists()
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


def test_init_creates_report_template(runner: CliRunner, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    result = runner.invoke(main, ["init", str(project), "--no-git", "--no-venv"])
    assert result.exit_code == 0, result.output

    myst_yml = (project / "myst.yml").read_text()
    assert "mystra.mjs" in myst_yml
    assert "index.md" in myst_yml

    index_md = (project / "index.md").read_text()
    assert index_md.startswith("# proj\n")
    # References must track the astra init boilerplate element ids.
    assert "{astra}`decisions.example_method`" in index_md
    assert "{astra:value}`outputs.main_result`" in index_md

    assert "_build/" in (project / ".gitignore").read_text()


def test_init_adopts_existing_project(runner: CliRunner, tmp_path: Path) -> None:
    """A directory that already holds an astra.yaml is converged, not rejected,
    and user-owned files are never overwritten."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "astra.yaml").write_text("# user spec\n")
    (project / ".gitignore").write_text("*.log\n")
    result = runner.invoke(main, ["init", str(project), "--no-git", "--no-venv"])
    assert result.exit_code == 0, result.output
    # User files untouched (gitignore gains the managed block, keeps content).
    assert (project / "astra.yaml").read_text() == "# user spec\n"
    gitignore = (project / ".gitignore").read_text()
    assert gitignore.startswith("*.log\n")
    assert "# lightcone-cli" in gitignore
    # Missing lightcone pieces were created.
    assert (project / "Containerfile").exists()
    assert (project / ".lightcone" / "lightcone.yaml").exists()


def test_init_is_idempotent(runner: CliRunner, tmp_path: Path) -> None:
    """A second run reports everything unchanged and rewrites nothing."""
    project = tmp_path / "proj"
    result = runner.invoke(main, ["init", str(project), "--no-git", "--no-venv"])
    assert result.exit_code == 0, result.output
    before = {p: p.read_text() for p in project.rglob("*") if p.is_file()}

    result = runner.invoke(
        main, ["init", str(project), "--no-git", "--no-venv", "--json"]
    )
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["converged"] is True
    assert report["created"] == []
    assert report["repaired"] == []
    assert {p: p.read_text() for p in project.rglob("*") if p.is_file()} == before

    # Gitignore block must not be duplicated across runs.
    assert (project / ".gitignore").read_text().count("# lightcone-cli") == 1


def test_init_check_reports_drift_without_writing(
    runner: CliRunner, tmp_path: Path
) -> None:
    project = tmp_path / "proj"
    result = runner.invoke(
        main, ["init", str(project), "--no-git", "--no-venv", "--check", "--json"]
    )
    assert result.exit_code == 1
    report = json.loads(result.output)
    assert report["converged"] is False
    assert "astra.yaml" in report["created"]
    assert not project.exists()  # --check writes nothing, not even the dir


def test_init_check_passes_on_converged_project(
    runner: CliRunner, tmp_path: Path
) -> None:
    project = tmp_path / "proj"
    result = runner.invoke(main, ["init", str(project), "--no-git", "--no-venv"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        main, ["init", str(project), "--no-git", "--no-venv", "--check"]
    )
    assert result.exit_code == 0, result.output


def test_init_warns_on_directory_copy(runner: CliRunner, tmp_path: Path) -> None:
    """A user Containerfile with a directory COPY is never rewritten, but
    the drift is surfaced through the warnings channel."""
    project = tmp_path / "proj"
    project.mkdir()
    custom = "FROM python:3.12-slim\nRUN apt-get update\nCOPY src/ /app/src/\n"
    (project / "Containerfile").write_text(custom)
    (project / "src").mkdir()
    (project / "src" / "a.py").write_text("a = 1\n")

    result = runner.invoke(
        main, ["init", str(project), "--no-git", "--no-venv", "--json"]
    )
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert (project / "Containerfile").read_text() == custom
    assert any("COPY/ADD of a directory" in w for w in report["warnings"])


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
        ["init", str(project), "--no-git", "--no-venv", "--scratch", "$SCRATCH", "--json"],
    )
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert any("lightcone.yaml" in w for w in report["warnings"])
    assert (project / ".lightcone" / "lightcone.yaml").read_text() == "target: [unclosed\n"

    # A non-mapping file must not crash the --scratch merge either.
    (project / ".lightcone" / "lightcone.yaml").write_text("local\n")
    result = runner.invoke(
        main,
        ["init", str(project), "--no-git", "--no-venv", "--scratch", "$SCRATCH", "--json"],
    )
    assert result.exit_code == 0, result.output


def test_init_points_spec_at_containerfile(runner: CliRunner, tmp_path: Path) -> None:
    """The scaffolded spec must reference the project Containerfile — pins
    the rewrite against drift in astra's boilerplate image name."""
    project = tmp_path / "proj"
    result = runner.invoke(main, ["init", str(project), "--no-git", "--no-venv"])
    assert result.exit_code == 0, result.output
    assert "container: Containerfile" in (project / "astra.yaml").read_text()


def test_engine_errors_render_cleanly(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ContainerBuildError from any command surfaces as a one-line CLI error
    (exit 1), not a traceback — the group boundary translates it."""
    from lightcone.cli import commands
    from lightcone.engine.container import ContainerBuildError

    def _boom(*args: object, **kwargs: object) -> Path:
        raise ContainerBuildError("COPY of a directory is not supported")

    monkeypatch.setattr(commands, "_project_root", _boom)
    result = runner.invoke(main, ["status"])
    assert result.exit_code == 1
    assert "not supported" in result.output
    assert "Traceback" not in result.output



def test_init_repairs_missing_piece(runner: CliRunner, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    result = runner.invoke(main, ["init", str(project), "--no-git", "--no-venv"])
    assert result.exit_code == 0, result.output
    (project / "Containerfile").unlink()

    result = runner.invoke(
        main, ["init", str(project), "--no-git", "--no-venv", "--json"]
    )
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert "Containerfile" in report["created"]
    assert (project / "Containerfile").exists()
    # The rest was left alone.
    assert "astra.yaml" in report["unchanged"]


def test_init_venv_uses_uv_when_available(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        calls.append(list(cmd))
        return MagicMock(returncode=0)

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(subprocess, "run", _fake_run)

    project = tmp_path / "proj"
    result = runner.invoke(main, ["init", str(project), "--no-git"])
    assert result.exit_code == 0, result.output

    assert ["uv", "venv", "--python", "3.12", ".venv"] in calls
    assert [
        "uv", "pip", "install", "--python", ".venv/bin/python", "-r", "requirements.txt",
    ] in calls


def test_init_venv_falls_back_to_python_when_uv_missing(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        calls.append(list(cmd))
        return MagicMock(returncode=0)

    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setattr(subprocess, "run", _fake_run)

    project = tmp_path / "proj"
    result = runner.invoke(main, ["init", str(project), "--no-git"])
    assert result.exit_code == 0, result.output

    assert ["python", "-m", "venv", ".venv"] in calls
    assert [
        ".venv/bin/python", "-m", "pip", "install", "-q", "-r", "requirements.txt",
    ] in calls


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


# ---- lc run (probe verb) ---------------------------------------------------


def _probe_project(tmp_path: Path, *, with_pyproject: bool = True) -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "astra.yaml").write_text(
        "outputs:\n  - id: best_fit\n    recipe:\n      command: echo hi\n"
    )
    if with_pyproject:
        (project / "pyproject.toml").write_text(
            '[project]\nname = "proj"\nversion = "0"\n'
        )
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
    project = _probe_project(tmp_path, with_pyproject=False)
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


# ---- lc materialize command building ---------------------------------------


def test_run_cmd_inserts_separator_before_targets() -> None:
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


def test_run_cmd_no_separator_when_no_targets() -> None:
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


def test_run_cmd_multiple_triggers_all_before_separator() -> None:
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


# ---- JupyterHub deployment paths ------------------------------------------


def test_run_cmd_uniform_across_backends() -> None:
    """One invocation shape for every backend: --shared-fs-usage drops
    software-deployment so spawned jobs run plain `python` from the
    worker's own environment (the worker image on a gateway, the
    driver's activated env locally / on SLURM) instead of embedding the
    driver's sys.executable. No gateway-specific flags exist."""
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


def test_init_scaffold_is_environment_agnostic(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scaffold is identical on and off a hub: the Containerfile
    carries no environment-specific content (pod identity is deployment
    config, not image content), and the image gets the execution stack
    — including dask-gateway — via a dedicated lightcone-cli layer."""
    monkeypatch.setenv("DASK_GATEWAY__ADDRESS", "http://proxy/services/dask-gateway")
    hub_project = tmp_path / "hub"
    result = runner.invoke(main, ["init", str(hub_project), "--no-git", "--no-venv"])
    assert result.exit_code == 0, result.output

    monkeypatch.delenv("DASK_GATEWAY__ADDRESS")
    local_project = tmp_path / "local"
    result = runner.invoke(main, ["init", str(local_project), "--no-git", "--no-venv"])
    assert result.exit_code == 0, result.output

    containerfile = (hub_project / "Containerfile").read_text()
    requirements = (hub_project / "requirements.txt").read_text()
    assert containerfile == (local_project / "Containerfile").read_text()
    assert requirements == (local_project / "requirements.txt").read_text()
    assert "useradd" not in containerfile and "USER" not in containerfile
    # The execution stack goes in the image, never the venv's
    # requirements — `lc` lives outside the project venv.
    assert "lightcone-cli" in containerfile
    assert "lightcone-cli" not in requirements


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


def test_ensure_images_none_runtime_returns_empty(tmp_path: Path) -> None:
    from lightcone.cli.commands import _ensure_images

    assert _ensure_images(tmp_path, runtime="none") == []
