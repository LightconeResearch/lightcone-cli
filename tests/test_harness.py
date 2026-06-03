"""Tests for the harness registry."""
from __future__ import annotations

import pytest

from lightcone.cli.harness import (
    HARNESS_REGISTRY,
    available_harnesses,
    resolve_harness,
)


def test_registry_contains_claude() -> None:
    assert "claude" in HARNESS_REGISTRY


def test_claude_config_fields() -> None:
    cfg = HARNESS_REGISTRY["claude"]
    assert cfg.tool_id == "claude"
    assert cfg.tool_name == "Claude Code"
    assert cfg.prefix == ".claude"


def test_claude_has_hooks_and_settings() -> None:
    cfg = HARNESS_REGISTRY["claude"]
    assert cfg.has_hooks is True
    assert cfg.has_settings is True


def test_resolve_harness_returns_config() -> None:
    cfg = resolve_harness("claude")
    assert cfg.tool_id == "claude"


def test_resolve_harness_raises_on_unknown() -> None:
    with pytest.raises(ValueError, match="Unknown harness"):
        resolve_harness("nonexistent-tool")


def test_resolve_harness_error_lists_available() -> None:
    with pytest.raises(ValueError, match="claude"):
        resolve_harness("nonexistent-tool")


def test_available_harnesses_returns_sorted_list() -> None:
    harnesses = available_harnesses()
    assert isinstance(harnesses, list)
    assert "claude" in harnesses
    assert harnesses == sorted(harnesses)
