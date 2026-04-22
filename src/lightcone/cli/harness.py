"""Harness registry — maps canonical skills to per-tool install paths.

Kept deliberately leaf (no imports from :mod:`lightcone.cli.commands` or
:mod:`lightcone.engine`) so it can be used by both the CLI and the eval
harness without introducing an import cycle.
"""

from __future__ import annotations

import os
import re
import warnings
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HarnessConfig:
    """Configuration for a single agent-harness installation target."""

    tool_id: str
    tool_name: str
    prefix: str  # e.g. ".claude", ".codex"
    has_skills: bool = True
    has_agents: bool = True
    has_guides: bool = True
    # True only for Claude Code: copies hooks/ and scripts/ during lc init.
    # No other supported tool has an equivalent per-project agent automation hook system.
    has_hooks: bool = False
    # True only for Claude Code: writes settings.json + settings.local.json during lc init.
    # Other tools use editor-level or global config, not per-project files managed by lightcone-cli.
    has_settings: bool = False
    commands_local: str | None = None
    commands_global: str | None = None
    commands_local_ext: str = ""  # suffix for command files, e.g. ".prompt.md"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

HARNESS_REGISTRY: dict[str, HarnessConfig] = {
    "claude": HarnessConfig(
        tool_id="claude",
        tool_name="Claude Code",
        prefix=".claude",
        has_skills=True,
        has_agents=True,
        has_guides=True,
        has_hooks=True,
        has_settings=True,
        commands_local=".claude/commands",
    ),
    "codex": HarnessConfig(
        tool_id="codex",
        tool_name="Codex",
        prefix=".codex",
        has_skills=True,
        has_agents=True,
        has_guides=True,
        has_hooks=False,
        has_settings=False,
        commands_global="$CODEX_HOME/prompts",
    ),
    "cursor": HarnessConfig(
        tool_id="cursor",
        tool_name="Cursor",
        prefix=".cursor",
        has_skills=True,
        has_agents=True,
        has_guides=True,
        has_hooks=False,
        has_settings=False,
        commands_local=".cursor/commands",
    ),
    "github-copilot": HarnessConfig(
        tool_id="github-copilot",
        tool_name="GitHub Copilot",
        prefix=".github",
        has_skills=True,
        has_agents=True,
        has_guides=True,
        has_hooks=False,
        has_settings=False,
        commands_local=".github/prompts",
        commands_local_ext=".prompt.md",
    ),
    "opencode": HarnessConfig(
        tool_id="opencode",
        tool_name="OpenCode",
        prefix=".opencode",
        has_skills=True,
        has_agents=True,
        has_guides=True,
        has_hooks=False,
        has_settings=False,
        commands_local=".opencode/commands",
    ),
}

#: All tool IDs known to the registry, in registration order.
ALL_TOOL_IDS: tuple[str, ...] = tuple(HARNESS_REGISTRY)

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def resolve_harnesses(tool_ids: Sequence[str] | None) -> list[HarnessConfig]:
    """Return configured harnesses for *tool_ids*, defaulting to ``["claude"]``.

    Duplicate IDs are silently de-duplicated (order preserved).
    """
    ids: list[str] = list(dict.fromkeys(tool_ids)) if tool_ids else ["claude"]
    for tid in ids:
        if tid not in HARNESS_REGISTRY:
            raise ValueError(
                f"Unknown tool ID {tid!r}. "
                f"Valid options: {', '.join(repr(t) for t in ALL_TOOL_IDS)}"
            )
    return [HARNESS_REGISTRY[tid] for tid in ids]


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

#: Pattern matching $VAR or ${VAR} environment variable references.
_ENV_VAR_PATTERN = re.compile(r"\$\{?(\w+)\}?\b")


def resolve_global_commands_path(harness: HarnessConfig) -> str | None:
    """Expand environment variables in a harness's global commands path."""
    path = harness.commands_global
    if path is None:
        return None

    def _repl(match: re.Match[str]) -> str:
        var = match.group(1)
        # Strip trailing braces/parens for patterns like ${VAR}
        return os.environ.get(var, match.group(0))

    return _ENV_VAR_PATTERN.sub(_repl, path)


def ensure_dir(path: Path) -> None:
    """Create *path* and parents if they don't exist. Warns on error."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        warnings.warn(f"Cannot create directory {path}: {exc}", stacklevel=2)
