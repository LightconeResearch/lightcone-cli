"""``lc hook`` — integration hooks for agent harnesses (plugin glue).

These subcommands emit the machine envelopes a harness expects at the
PreToolUse / SessionStart boundary. They are adapter commands, not
human-facing surfaces.

``lc hook pretool`` is the skill-activation gate. Inside a Lightcone/ASTRA
project it denies the first ``Bash``/``Write``/``Edit`` call of a session
until the ``/lightcone`` skill has been activated, then opens for the rest
of the session. Modeled directly on felt's ``felt hook pretool`` (see
``LightconeResearch/felt`` ``cmd/hook.go``); the rules are pinned by the
tests in ``tests/test_hook_pretool.py``.

Scope today is **Claude Code only** — other harnesses are detected and
passed through silently (a deny would deadlock a loop that has no Skill
tool to satisfy it). The hook is fail-open throughout: any parse or IO
error passes rather than blocks, because losing the gate is cheaper than
wedging a session.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import click

# Activating `/lightcone` — the entry-point skill — opens the gate. We gate on
# this one skill, not on `astra`/`lc-cli` directly: `/lightcone` is the lay of
# the land that routes to the rest, so one activation orients the agent.
_GATE_SKILL = "lightcone"

# Only these tools are denied before activation. Read-only tools (Read, Glob,
# Grep, …) always pass so the agent can orient first; the hooks.json matcher
# also scopes invocation to Bash|Write|Edit|Skill, but we re-check here so the
# gate is correct independent of how it's wired.
_GATED_TOOLS = {"Bash", "Write", "Edit"}

_DENY_REASON = (
    "Activate the /lightcone skill first. You're in a Lightcone/ASTRA project "
    "but haven't loaded the lightcone skill this session. Call the Skill tool "
    'with skill: "lightcone" before running commands or editing files. '
    "/lightcone is the entry point: it gives you the lay of the land and points "
    "you to the right skill for what you're doing (astra, lc-cli, lc-from-paper, "
    "…). Reading files into context is not the same as loading the skill."
)


def find_project_root(start: Path) -> Path | None:
    """Walk up from ``start`` for an lc project marker.

    The marker is ``.lightcone/lightcone.yaml`` (written by ``lc init``). We
    stop before ``$HOME`` and at the filesystem root so the *global* config at
    ``~/.lightcone/config.yaml`` is never mistaken for a project root.
    """
    try:
        home = Path.home().resolve()
    except (OSError, RuntimeError):
        home = None
    try:
        cur = start.resolve()
    except OSError:
        return None
    for d in (cur, *cur.parents):
        if home is not None and d == home:
            break
        if (d / ".lightcone" / "lightcone.yaml").is_file():
            return d
    return None


def is_claude_session(transcript_path: str) -> bool:
    """Claude Code writes session transcripts under ``~/.claude/projects/``.

    Other harnesses (Codex, pi, …) don't, and have no Skill tool to satisfy a
    deny — so we treat a missing or foreign transcript path as non-Claude and
    never deny those sessions.
    """
    if not transcript_path:
        return False
    prefix = str(Path.home() / ".claude" / "projects") + os.sep
    return transcript_path.startswith(prefix)


def _marker_path(session_id: str) -> Path:
    return Path(tempfile.gettempdir()) / f"lc-skill-reminded-{session_id}"


def _skill_matches(skill: str) -> bool:
    """True if a Skill activation targets ``/lightcone``.

    Tolerates plugin namespacing and versioning: ``lightcone``,
    ``lightcone:lightcone``, ``foo:lightcone``, ``lightcone@1.0``.
    """
    if not skill:
        return False
    name = skill.split("@", 1)[0].split(":")[-1]
    return name == _GATE_SKILL


def decide(payload: dict) -> dict | None:
    """Pure gate logic. Returns a deny envelope, or ``None`` to pass.

    Order mirrors felt's ``runPreToolHook``:
      1. no cwd / not in an lc project        → pass
      2. Skill tool: mark iff it's /lightcone → pass
      3. non-Claude harness                   → mark, pass (no deadlock)
      4. already reminded this session        → pass
      5. non-gated tool (read-only, etc.)     → pass
      6. otherwise                            → deny
    """
    cwd = payload.get("cwd") or ""
    if not cwd or find_project_root(Path(cwd)) is None:
        return None

    session_id = payload.get("session_id") or ""
    tool_name = payload.get("tool_name") or ""
    transcript_path = payload.get("transcript_path") or ""
    marker = _marker_path(session_id)

    if tool_name == "Skill":
        if _skill_matches((payload.get("tool_input") or {}).get("skill") or ""):
            _touch(marker)
        return None

    if not is_claude_session(transcript_path):
        _touch(marker)
        return None

    if marker.exists():
        return None

    if tool_name not in _GATED_TOOLS:
        return None

    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": _DENY_REASON,
        }
    }


def _touch(path: Path) -> None:
    try:
        path.touch()
    except OSError:
        pass


@click.group("hook")
def hook_group() -> None:
    """Integration hooks for agent harnesses (plugin glue)."""


@hook_group.command("pretool")
def pretool() -> None:
    """PreToolUse gate: deny gated tools until ``/lightcone`` is activated."""
    try:
        payload = json.load(sys.stdin)
    except Exception:
        # Can't parse the payload: fail open. Losing the gate beats blocking.
        return
    if not isinstance(payload, dict):
        return
    envelope = decide(payload)
    if envelope is not None:
        json.dump(envelope, sys.stdout)
