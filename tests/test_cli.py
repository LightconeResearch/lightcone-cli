"""Tests for the redesigned lightcone CLI."""
from __future__ import annotations

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


@pytest.fixture(autouse=True)
def _no_real_claude_plugin_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent tests from shelling out to a real ``claude plugin install``.

    ``lc init`` calls ``shutil.which("claude")`` to decide whether to register
    the marketplace and install the plugin via the Claude Code CLI. In CI and
    on dev machines that have ``claude`` on PATH, the unmocked behavior would
    write to the user's actual ``~/.claude/`` config. We default to the
    soft-fail branch (print a hint, continue); tests that want to exercise
    the install path override ``shutil.which`` and ``subprocess.run`` locally.
    """
    real_which = shutil.which

    def fake_which(name: str, *args: object, **kwargs: object) -> str | None:
        if name == "claude":
            return None
        return real_which(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(shutil, "which", fake_which)


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


def test_init_writes_settings_with_only_permissions(
    runner: CliRunner, tmp_path: Path
) -> None:
    """The new ``lc init`` no longer copies skills/agents/scripts into the
    project. ``.claude/settings.json`` carries only the permissions tier; the
    plugin (skills, agents, hooks) lives in the user's ``~/.claude/`` after
    ``claude plugin install``.
    """
    import json

    project = tmp_path / "proj"
    result = runner.invoke(main, ["init", str(project), "--no-git", "--no-venv"])
    assert result.exit_code == 0, result.output

    settings = json.loads((project / ".claude" / "settings.json").read_text())
    assert set(settings.keys()) == {"permissions"}, (
        "settings.json should only carry the permissions tier; the plugin "
        "ships hooks via claude plugin install"
    )
    # No per-project copies of plugin assets:
    assert not (project / ".claude" / "skills").exists()
    assert not (project / ".claude" / "agents").exists()
    assert not (project / ".claude" / "scripts").exists()
    assert not (project / ".claude" / "guides").exists()
    assert not (project / ".claude" / "templates").exists()


def test_init_invokes_claude_plugin_when_available(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ``claude`` is on PATH, ``lc init`` shells out to register the
    marketplace and install the plugin — both commands idempotent.
    """
    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        calls.append(list(cmd))
        return MagicMock(returncode=0)

    # `claude` resolves, `uv` does not (so the venv branch falls back to
    # python -m venv, but we pass --no-venv anyway).
    monkeypatch.setattr(
        shutil, "which", lambda name: "/usr/bin/claude" if name == "claude" else None
    )
    monkeypatch.setattr(subprocess, "run", _fake_run)

    project = tmp_path / "proj"
    result = runner.invoke(main, ["init", str(project), "--no-git", "--no-venv"])
    assert result.exit_code == 0, result.output

    # The marketplace-add call's last arg is the marketplace root, which is
    # either the bundled wheel path or the dev repo root — both end in a
    # `.claude-plugin/marketplace.json`. We only assert the verb shape.
    add_calls = [c for c in calls if c[:3] == ["claude", "plugin", "marketplace"]]
    install_calls = [c for c in calls if c[:3] == ["claude", "plugin", "install"]]
    assert add_calls, f"expected `claude plugin marketplace add` invocation, got {calls}"
    assert add_calls[0][3] == "add"
    assert install_calls == [["claude", "plugin", "install", "lightcone@lightcone-cli"]]


def test_init_prints_hint_when_claude_missing(
    runner: CliRunner, tmp_path: Path
) -> None:
    """When ``claude`` is missing (autouse fixture default), ``lc init`` still
    succeeds and prints a manual-install hint instead of hard-failing.

    This is the path Codex / other-agent users hit — and we don't want a
    missing ``claude`` CLI to break ``lc init`` for them.
    """
    project = tmp_path / "proj"
    result = runner.invoke(main, ["init", str(project), "--no-git", "--no-venv"])
    assert result.exit_code == 0, result.output
    assert "claude CLI not found" in result.output
    assert "claude plugin marketplace add" in result.output
    assert "claude plugin install lightcone@lightcone-cli" in result.output


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
