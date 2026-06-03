"""Tests for harness-aware plugin installation via lc init."""
from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from lightcone.cli.commands import main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    fake_home = tmp_path / "_home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    return fake_home


def test_default_init_installs_to_dotclaude(runner: CliRunner, tmp_path: Path) -> None:
    """Without --harness the default claude harness installs skills to .claude/."""
    project = tmp_path / "proj"
    result = runner.invoke(
        main, ["init", str(project), "--no-git", "--no-venv", "--harness", "claude"]
    )
    assert result.exit_code == 0, result.output
    assert (project / ".claude").is_dir()
    assert (project / ".claude" / "skills").is_dir()


def test_harness_flag_installs_to_prefix(runner: CliRunner, tmp_path: Path) -> None:
    """--harness claude installs into .claude/."""
    project = tmp_path / "proj"
    result = runner.invoke(
        main, ["init", str(project), "--no-git", "--no-venv", "--harness", "claude"]
    )
    assert result.exit_code == 0, result.output
    assert (project / ".claude").is_dir()


def test_skills_and_agents_present_after_init(runner: CliRunner, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    result = runner.invoke(
        main, ["init", str(project), "--no-git", "--no-venv", "--harness", "claude"]
    )
    assert result.exit_code == 0, result.output
    assert (project / ".claude" / "skills").is_dir()
    assert (project / ".claude" / "agents").is_dir()


def test_settings_json_written_for_claude(runner: CliRunner, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    result = runner.invoke(
        main, ["init", str(project), "--no-git", "--no-venv", "--harness", "claude"]
    )
    assert result.exit_code == 0, result.output
    settings = project / ".claude" / "settings.json"
    assert settings.exists()
    import json
    data = json.loads(settings.read_text())
    assert "permissions" in data
    assert "hooks" in data


def test_agents_md_written_for_claude_harness(runner: CliRunner, tmp_path: Path) -> None:
    """AGENTS.md is always written as the harness-neutral project doc."""
    project = tmp_path / "proj"
    result = runner.invoke(
        main, ["init", str(project), "--no-git", "--no-venv", "--harness", "claude"]
    )
    assert result.exit_code == 0, result.output
    agents_md = project / "AGENTS.md"
    assert agents_md.exists()
    assert len(agents_md.read_text()) > 0


def test_claude_md_shim_written_only_for_claude(runner: CliRunner, tmp_path: Path) -> None:
    """CLAUDE.md shim is created for the Claude harness and links to AGENTS.md."""
    project = tmp_path / "proj"
    result = runner.invoke(
        main, ["init", str(project), "--no-git", "--no-venv", "--harness", "claude"]
    )
    assert result.exit_code == 0, result.output
    claude_md = project / "CLAUDE.md"
    assert claude_md.exists()
    content = claude_md.read_text()
    assert "AGENTS.md" in content


def test_prompt_shown_when_harness_not_provided(
    runner: CliRunner, tmp_path: Path
) -> None:
    """When --harness is omitted, the user is prompted to choose one."""
    project = tmp_path / "proj"
    # Provide "claude\n" as stdin input for the prompt.
    result = runner.invoke(
        main,
        ["init", str(project), "--no-git", "--no-venv"],
        input="claude\n",
    )
    assert result.exit_code == 0, result.output
    assert "harness" in result.output.lower()
    assert (project / ".claude").is_dir()


def test_unknown_harness_rejected(runner: CliRunner, tmp_path: Path) -> None:
    """--harness with an unregistered value is rejected by Click before init runs."""
    project = tmp_path / "proj"
    result = runner.invoke(
        main, ["init", str(project), "--no-git", "--no-venv", "--harness", "nonexistent"]
    )
    assert result.exit_code != 0


def test_agents_md_written_even_when_plugin_source_absent(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AGENTS.md and CLAUDE.md must be written even if the plugin bundle is missing."""
    from lightcone.cli import plugin as plugin_mod

    monkeypatch.setattr(plugin_mod, "get_plugin_source_dir", lambda: None)

    project = tmp_path / "proj"
    result = runner.invoke(
        main, ["init", str(project), "--no-git", "--no-venv", "--harness", "claude"]
    )
    assert result.exit_code == 0, result.output
    assert (project / "AGENTS.md").exists()
    assert (project / "CLAUDE.md").exists()
    assert "AGENTS.md" in (project / "CLAUDE.md").read_text()
