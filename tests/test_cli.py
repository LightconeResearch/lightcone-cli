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


def test_init_writes_marketplace_settings(
    runner: CliRunner, tmp_path: Path
) -> None:
    import json

    project = tmp_path / "proj"
    result = runner.invoke(main, ["init", str(project), "--no-git", "--no-venv"])
    assert result.exit_code == 0, result.output

    settings = json.loads((project / ".claude" / "settings.json").read_text())
    # Marketplace is registered so Claude Code can offer the plugin.
    assert settings["extraKnownMarketplaces"]["lightcone-research"]["source"] == {
        "source": "github",
        "repo": "LightconeResearch/agent-skills",
    }
    assert settings["enabledPlugins"] == {"lightcone@lightcone-research": True}
    # The CLI writes no permission policy — that belongs to the harness.
    assert "permissions" not in settings
    # Hooks no longer live in settings.json — the plugin carries them.
    assert "hooks" not in settings
    # No skills/agents/scripts are copied into the project anymore.
    assert not (project / ".claude" / "skills").exists()
    assert not (project / ".claude" / "hooks.json").exists()


# ---- lc init (convergent on an existing ASTRA project) --------------------


def test_init_is_idempotent_on_fresh_scaffold(
    runner: CliRunner, tmp_path: Path
) -> None:
    """Running `lc init` twice on the same dir must not error — the second run
    converges rather than refusing that astra.yaml now exists."""
    project = tmp_path / "proj"
    first = runner.invoke(main, ["init", str(project), "--no-git", "--no-venv"])
    assert first.exit_code == 0, first.output
    scaffold_astra = (project / "astra.yaml").read_text()

    second = runner.invoke(main, ["init", str(project), "--no-git", "--no-venv"])
    assert second.exit_code == 0, second.output
    assert "Nothing to do" in second.output
    # The spec is never rewritten.
    assert (project / "astra.yaml").read_text() == scaffold_astra


def test_init_layers_integration_onto_existing_project(
    runner: CliRunner, tmp_path: Path
) -> None:
    import json

    project = tmp_path / "proj"
    project.mkdir()
    (project / "astra.yaml").write_text("# existing ASTRA project\n")

    result = runner.invoke(main, ["init", str(project)])
    assert result.exit_code == 0, result.output

    # Integration bits are layered on...
    assert (project / ".lightcone" / "lightcone.yaml").exists()
    assert (project / "results").is_dir()
    assert (project / "myst.yml").exists()
    assert (project / "index.md").exists()
    settings = json.loads((project / ".claude" / "settings.json").read_text())
    assert settings["enabledPlugins"] == {"lightcone@lightcone-research": True}
    assert "lightcone-research" in settings["extraKnownMarketplaces"]

    # ...but the scaffold/venv/git steps are skipped when astra.yaml pre-exists.
    assert not (project / "Containerfile").exists()
    assert not (project / ".venv").exists()
    assert not (project / ".git").exists()
    # The existing spec is left untouched.
    assert (project / "astra.yaml").read_text() == "# existing ASTRA project\n"


def test_init_converges_and_preserves_existing_files(
    runner: CliRunner, tmp_path: Path
) -> None:
    import json

    project = tmp_path / "proj"
    project.mkdir()
    (project / "astra.yaml").write_text("# existing\n")

    # Pre-existing settings.json with unrelated content must survive the merge.
    claude_dir = project / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(
        json.dumps({"permissions": {"allow": ["Bash(ls:*)"]}})
    )
    # Pre-existing report file must not be clobbered.
    (project / "index.md").write_text("# my real report\n")

    result = runner.invoke(main, ["init", str(project)])
    assert result.exit_code == 0, result.output
    assert "skipped (already present) index.md" in result.output

    settings = json.loads((claude_dir / "settings.json").read_text())
    assert settings["permissions"] == {"allow": ["Bash(ls:*)"]}
    assert settings["enabledPlugins"] == {"lightcone@lightcone-research": True}
    assert (project / "index.md").read_text() == "# my real report\n"

    # Re-running is a clean no-op — nothing new added.
    again = runner.invoke(main, ["init", str(project)])
    assert again.exit_code == 0, again.output
    assert "Nothing to do" in again.output


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
