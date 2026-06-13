"""Pins the contract of the ``lc hook pretool`` skill-activation gate.

The gate denies the first ``Bash``/``Write``/``Edit`` call of a Claude session
inside a Lightcone/ASTRA project until the ``/lightcone`` skill is activated,
then opens for the rest of the session. The rules (ported from felt's
``cmd/hook.go``):

  - outside an lc project (no ``.lightcone/lightcone.yaml`` at cwd or any
    ancestor below ``$HOME``)          → pass
  - Skill tool activating /lightcone   → mark session, pass
  - Skill tool activating a sibling    → pass WITHOUT marking (no bypass)
  - non-Claude harness (foreign/empty
    transcript path)                   → mark, pass (a deny would deadlock)
  - already marked this session        → pass
  - read-only / non-gated tool         → pass
  - otherwise                          → deny

Tests target the pure ``decide()`` so they're fast and harness-free.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lightcone.cli import hooks


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A fake $HOME with a Claude transcript dir and an lc project under it."""
    home = (tmp_path / "home").resolve()
    (home / ".claude" / "projects").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    marker_dir = tmp_path / "tmp"
    marker_dir.mkdir()
    monkeypatch.setattr(hooks.tempfile, "gettempdir", lambda: str(marker_dir))

    proj = home / "work" / "proj"
    (proj / ".lightcone").mkdir(parents=True)
    (proj / ".lightcone" / "lightcone.yaml").write_text("target: local\n")

    return SimpleNamespace(
        home=home,
        proj=proj,
        claude_tp=str(home / ".claude" / "projects" / "abc" / "t.jsonl"),
    )


def _payload(env, tool="Bash", *, cwd=None, transcript="__claude__", skill=None, session="s1"):
    return {
        "session_id": session,
        "tool_name": tool,
        "cwd": cwd if cwd is not None else str(env.proj),
        "transcript_path": env.claude_tp if transcript == "__claude__" else transcript,
        "tool_input": {"skill": skill} if skill else {},
    }


def _is_deny(res):
    return res is not None and res["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_first_bash_in_project_is_denied(env):
    assert _is_deny(hooks.decide(_payload(env, "Bash")))


@pytest.mark.parametrize("tool", ["Bash", "Write", "Edit"])
def test_all_gated_tools_denied(env, tool):
    assert _is_deny(hooks.decide(_payload(env, tool, session=f"s-{tool}")))


def test_activating_lightcone_opens_gate(env):
    assert hooks.decide(_payload(env, "Skill", skill="lightcone")) is None
    assert hooks.decide(_payload(env, "Bash")) is None  # same session: now open


@pytest.mark.parametrize(
    "skill", ["lightcone", "lightcone:lightcone", "foo:lightcone", "lightcone@1.0"]
)
def test_namespaced_skill_names_open_gate(env, skill):
    sess = f"ns-{skill}"
    assert hooks.decide(_payload(env, "Skill", skill=skill, session=sess)) is None
    assert hooks.decide(_payload(env, "Bash", session=sess)) is None


def test_sibling_skill_does_not_open_gate(env):
    # Activating /astra must NOT let the agent skip /lightcone.
    assert hooks.decide(_payload(env, "Skill", skill="astra", session="sib")) is None
    assert _is_deny(hooks.decide(_payload(env, "Bash", session="sib")))


def test_walk_up_from_subdirectory(env):
    deep = env.proj / "src" / "deep"
    deep.mkdir(parents=True)
    assert _is_deny(hooks.decide(_payload(env, "Bash", cwd=str(deep), session="deep")))


@pytest.mark.parametrize("tool", ["Read", "Glob", "Grep", "WebFetch", "TodoWrite"])
def test_read_only_tools_pass(env, tool):
    assert hooks.decide(_payload(env, tool, session=f"ro-{tool}")) is None


def test_non_claude_session_passes_and_marks(env):
    # Empty transcript = not Claude: never deny (no Skill tool to satisfy it).
    assert hooks.decide(_payload(env, "Bash", transcript="", session="codex")) is None
    # And the marker was written, so a later call also passes.
    assert hooks.decide(_payload(env, "Bash", transcript="", session="codex")) is None


def test_foreign_transcript_path_treated_as_non_claude(env):
    assert (
        hooks.decide(
            _payload(env, "Bash", transcript="/home/x/.codex/sessions/y.jsonl", session="f")
        )
        is None
    )


def test_outside_project_passes(env, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    assert hooks.decide(_payload(env, "Bash", cwd=str(outside), session="out")) is None


def test_global_lightcone_config_is_not_a_project(env):
    # The GLOBAL ~/.lightcone/config.yaml must never count as a project root.
    (env.home / ".lightcone").mkdir(parents=True, exist_ok=True)
    (env.home / ".lightcone" / "config.yaml").write_text("container: {}\n")
    (env.home / ".lightcone" / "lightcone.yaml").write_text("decoy: true\n")
    work = env.home / "loose"
    work.mkdir()
    assert hooks.decide(_payload(env, "Bash", cwd=str(work), session="glob")) is None


def test_activation_writes_a_marker_file(env):
    marker = hooks._marker_path("mk")
    assert not marker.exists()
    hooks.decide(_payload(env, "Skill", skill="lightcone", session="mk"))
    assert marker.exists()


def test_missing_cwd_passes(env):
    assert hooks.decide(_payload(env, "Bash", cwd="", session="nocwd")) is None
