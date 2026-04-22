"""Tests for src/lightcone/cli/harness.py — harness registry module."""

from pathlib import Path

import pytest

from lightcone.cli.harness import (
    ALL_TOOL_IDS,
    HARNESS_REGISTRY,
    ensure_dir,
    resolve_global_commands_path,
    resolve_harnesses,
)

# ------ resolve_harnesses ------


class TestResolveHarnesses:
    def test_default_returns_claude(self):
        result = resolve_harnesses(None)
        assert len(result) == 1
        assert result[0].tool_id == "claude"

    def test_default_empty_list_returns_claude(self):
        result = resolve_harnesses(())
        assert len(result) == 1
        assert result[0].tool_id == "claude"

    def test_single_tool(self):
        result = resolve_harnesses(("codex",))
        assert len(result) == 1
        assert result[0].tool_id == "codex"

    def test_multiple_tools(self):
        result = resolve_harnesses(("claude", "codex", "cursor"))
        assert len(result) == 3
        assert [r.tool_id for r in result] == ["claude", "codex", "cursor"]

    def test_duplicate_tools_preserved(self):
        result = resolve_harnesses(("claude", "claude"))
        assert len(result) == 2

    def test_invalid_tool_raises(self):
        with pytest.raises(ValueError, match="Unknown tool"):
            resolve_harnesses(("nonexistent",))

    def test_mixed_valid_invalid_raises(self):
        with pytest.raises(ValueError, match="Unknown tool"):
            resolve_harnesses(("claude", "bogus"))


# ------ HARNESS_REGISTRY ------


class TestHarnessRegistry:
    def test_all_tools_present(self):
        expected = {"claude", "codex", "cursor", "github-copilot", "opencode"}
        assert set(HARNESS_REGISTRY.keys()) == expected
        assert set(ALL_TOOL_IDS) == expected

    def test_claude_has_hooks_and_settings(self):
        h = HARNESS_REGISTRY["claude"]
        assert h.has_hooks is True
        assert h.has_settings is True
        assert h.has_skills is True
        assert h.has_agents is True
        assert h.has_guides is True

    def test_codex_no_hooks_or_settings(self):
        h = HARNESS_REGISTRY["codex"]
        assert h.has_hooks is False
        assert h.has_settings is False
        assert h.has_skills is True
        assert h.has_agents is True
        assert h.has_guides is True

    def test_only_claude_has_hooks(self):
        for tid, h in HARNESS_REGISTRY.items():
            if tid == "claude":
                assert h.has_hooks is True
            else:
                assert h.has_hooks is False

    def test_only_claude_has_settings(self):
        for tid, h in HARNESS_REGISTRY.items():
            if tid == "claude":
                assert h.has_settings is True
            else:
                assert h.has_settings is False

    def test_codex_has_global_commands(self):
        h = HARNESS_REGISTRY["codex"]
        assert h.commands_global is not None
        assert "CODEX_HOME" in h.commands_global

    def test_claude_no_global_commands(self):
        h = HARNESS_REGISTRY["claude"]
        assert h.commands_global is None

    def test_github_copilot_has_suffix(self):
        h = HARNESS_REGISTRY["github-copilot"]
        assert h.commands_local_ext == ".prompt.md"


# ------ resolve_global_commands_path ------


class TestResolveGlobalCommandsPath:
    def test_claude_returns_none(self):
        assert resolve_global_commands_path(HARNESS_REGISTRY["claude"]) is None

    def test_codex_unresolved_when_env_unset(self):
        h = HARNESS_REGISTRY["codex"]
        # CODEX_HOME may or may not be set; just check the function doesn't crash
        result = resolve_global_commands_path(h)
        assert result is not None

    def test_env_expansion_unresolved(self):
        """When CODEX_HOME is unset, path is returned with placeholder intact."""
        h = HARNESS_REGISTRY["codex"]
        result = resolve_global_commands_path(h)
        assert result is not None
        assert "CODEX_HOME" in result


# ------ ensure_dir ------


class TestEnsureDir:
    def test_creates_new_directory(self, tmp_path: Path):
        new_dir = tmp_path / "a" / "b" / "c"
        ensure_dir(new_dir)
        assert new_dir.is_dir()

    def test_noop_on_existing(self, tmp_path: Path):
        existing = tmp_path / "a"
        existing.mkdir()
        ensure_dir(existing)
        assert existing.is_dir()

    def test_warns_on_permission_error(self, capsys, tmp_path: Path):
        """ensure_dir prints a warning on OSError instead of raising."""
        import errno
        from unittest.mock import patch

        target = tmp_path / "nope"
        with patch.object(Path, "mkdir") as mock_mkdir:
            mock_mkdir.side_effect = OSError(errno.EACCES, "Permission denied")
            ensure_dir(target)
        out = capsys.readouterr().out
        assert "warning" in out.lower()
