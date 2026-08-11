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
    for cmd in ("init", "run", "status", "verify", "build"):
        assert cmd in result.output


def test_help_does_not_advertise_removed_commands(runner: CliRunner) -> None:
    result = runner.invoke(main, ["--help"])
    assert "  dev " not in result.output
    assert "  cluster " not in result.output
    assert "  setup " not in result.output


def test_first_invocation_auto_creates_global_config(
    runner: CliRunner, _isolated_home: Path, tmp_path: Path
) -> None:
    config = _isolated_home / ".lightcone" / "config.yaml"
    assert not config.exists()
    # Any real subcommand triggers the group callback; ``init`` runs cleanly
    # without a pre-existing project.
    project = tmp_path / "proj"
    result = runner.invoke(
        main, ["init", str(project), "--no-git", "--no-venv"]
    )
    assert result.exit_code == 0, result.output
    assert config.exists()
    assert "runtime: auto" in config.read_text()


# ---- lc init --------------------------------------------------------------


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
    # lc run — and the one file in results/ that stays tracked by git.
    readme = (project / "results" / "README.md").read_text()
    assert "lc run" in readme
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
    assert ["uv", "pip", "install", "--python", ".venv/bin/python", "lightcone-cli"] in calls


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
    assert [".venv/bin/python", "-m", "pip", "install", "-q", "lightcone-cli"] in calls


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


# ---- lc run command building ------------------------------------------------


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
    config, not image content), and lightcone-cli in requirements.txt
    brings the whole execution stack — including dask-gateway — as
    normal dependencies."""
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
    assert "lightcone-cli" in requirements


def test_lightcone_requirement_pins_running_version() -> None:
    from importlib.metadata import version

    from lightcone.cli.commands import _lightcone_requirement

    req = _lightcone_requirement()
    v = version("lightcone-cli")
    if "dev" in v:
        # Dev builds aren't published — unpinned fallback.
        assert req.strip().splitlines()[-1] == "lightcone-cli"
    else:
        assert f"lightcone-cli=={v}" in req


def test_ensure_images_none_runtime_returns_empty(tmp_path: Path) -> None:
    from lightcone.cli.commands import _ensure_images

    assert _ensure_images(tmp_path, runtime="none") == []
