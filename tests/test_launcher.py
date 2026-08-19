"""The launcher: what delegates, what does not, and what crosses over."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from lightcone.cli.launcher import DELEGATED_ENV, TOOL_ENV_VERBS, maybe_delegate

#: Every verb that is not in :data:`TOOL_ENV_VERBS`. Spelled out rather
#: than derived, so that adding a command to the CLI fails
#: :func:`test_every_verb_has_a_routing_decision` until someone decides
#: which side of the boundary it belongs on.
DELEGATING_VERBS = {"run", "materialize", "status"}


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A directory the launcher will recognise, and stand in for its engine.

    The engine binary is written by hand rather than installed: what is
    under test is the hand-over, and a real ``lc`` in a real ``.venv``
    would cost a full resolve to assert exactly the same argv.
    """
    root = tmp_path / "proj"
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname = "proj"\n')
    (root / "uv.lock").write_text("version = 1\n")
    (root / ".venv" / "bin" / "lc").write_text("#!/bin/sh\n")
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def execs(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, list[str], dict[str, str]]]:
    """Capture ``os.execve``, emulating the process hand-over by raising.

    A real exec never returns, so a fake that did would let the test run
    on through code the caller can never reach.
    """
    calls: list[tuple[str, list[str], dict[str, str]]] = []

    def fake_execve(path: str, argv: list[str], env: dict[str, str]) -> None:
        calls.append((path, argv, env))
        raise SystemExit(0)

    monkeypatch.setattr(os, "execve", fake_execve)
    return calls


# =============================================================================
# Routing — who is handed over, and who is not
# =============================================================================


def test_a_verb_in_a_project_is_handed_to_its_own_engine(
    project: Path, execs: list[tuple[str, list[str], dict[str, str]]]
) -> None:
    with pytest.raises(SystemExit):
        maybe_delegate(["materialize", "--check"])

    path, argv, env = execs[0]
    assert path == str(project / ".venv" / "bin" / "lc")
    # The frozen interface: the argv verbatim, and one variable.
    assert argv == ["lc", "materialize", "--check"]
    assert env[DELEGATED_ENV] == "1"


def test_the_environment_is_converged_before_the_hand_over(
    project: Path, execs: list[tuple[str, list[str], dict[str, str]]], tools: list[list[str]]
) -> None:
    """The engine that is about to run is the one the lock describes.

    Syncing after the exec would be too late — the process making that
    decision would already be gone.
    """
    with pytest.raises(SystemExit):
        maybe_delegate(["status"])

    synced = [c for c in tools if c[:2] == ["uv", "sync"]]
    assert synced, tools
    assert "--locked" in synced[0] and "--project" in synced[0]


@pytest.mark.parametrize(
    ("argv", "why"),
    [
        ([], "no verb at all"),
        (["--help"], "a flag is not a verb"),
        (["--version"], "a flag is not a verb"),
        (["init"], "init is what *makes* the environment"),
        (["init", "--check"], "flags do not change which verb it is"),
    ],
)
def test_what_never_delegates(
    argv: list[str], why: str, project: Path, execs: list[tuple[str, ...]]
) -> None:
    maybe_delegate(argv)
    assert execs == [], why


def test_a_second_hand_over_never_happens(
    project: Path, execs: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The engine we exec runs this same code on the way in."""
    monkeypatch.setenv(DELEGATED_ENV, "1")
    maybe_delegate(["materialize"])
    assert execs == []


def test_a_directory_that_is_not_a_project_is_left_to_the_engine(
    tmp_path: Path, execs: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Declining is the whole response — the refusal is written once, in
    `current_project`, and a second copy here could only disagree with it."""
    monkeypatch.chdir(tmp_path)
    maybe_delegate(["materialize"])
    assert execs == []


def test_the_project_engine_does_not_hand_over_to_itself(
    project: Path, execs: list[tuple[str, ...]], tools: list[list[str]], monkeypatch
) -> None:
    """`uv run lc …` already arrives in the project's environment.

    Delegating would re-exec this same program to reach itself, and
    re-converge what uv converged a moment ago.
    """
    monkeypatch.setattr(sys, "executable", str(project / ".venv" / "bin" / "python"))
    maybe_delegate(["materialize"])

    assert execs == []
    assert [c for c in tools if c[:2] == ["uv", "sync"]] == []


def test_every_verb_has_a_routing_decision() -> None:
    """A command added without a routing decision would delegate by
    default — into a project engine that may be too old to know it, which
    fails far from its cause. The launcher never imports `commands`, so
    this test is the only thing binding the two."""
    from lightcone.cli.commands import main

    assert set(main.commands) == TOOL_ENV_VERBS | DELEGATING_VERBS


# =============================================================================
# Failing loudly
# =============================================================================


def test_an_environment_without_the_engine_is_a_refusal(
    project: Path, execs: list[tuple[str, ...]]
) -> None:
    """Never a fall-through to the tool env: continuing there is exactly
    the silent version skew the launcher exists to remove."""
    (project / ".venv" / "bin" / "lc").unlink()

    with pytest.raises(SystemExit) as raised:
        maybe_delegate(["materialize"])

    assert "uv add lightcone-cli" in str(raised.value)
    assert execs == []


def test_a_failed_converge_is_reported_here(
    project: Path, execs: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Click has not been imported yet, so `_EngineErrorGroup` cannot
    render this one and the launcher has to say it itself."""
    from lightcone.engine import project as project_module

    monkeypatch.setattr(
        project_module,
        "_run",
        lambda argv, *, cwd: type("P", (), {"returncode": 1, "stderr": "the lock is stale"})(),
    )
    with pytest.raises(SystemExit) as raised:
        maybe_delegate(["materialize"])

    assert "the lock is stale" in str(raised.value)
    assert execs == []


def test_a_uv_warning_reaches_the_user(
    project: Path,
    execs: list[tuple[str, ...]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The cache-on-another-filesystem warning is the one that must not be
    swallowed — nothing else would tell anyone their packages are being
    copied rather than linked."""
    from lightcone.engine import project as project_module

    monkeypatch.setattr(
        project_module,
        "_run",
        lambda argv, *, cwd: type(
            "P", (), {"returncode": 0, "stderr": "warning: Failed to hardlink files"}
        )(),
    )
    (project / ".venv" / "bin" / "lc").write_text("#!/bin/sh\n")

    with pytest.raises(SystemExit):
        maybe_delegate(["status"])

    assert "Failed to hardlink files" in capsys.readouterr().err


# =============================================================================
# What crosses the boundary
# =============================================================================


def test_ambient_uv_steering_does_not_cross(
    project: Path,
    execs: list[tuple[str, list[str], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Measured against uv 0.12.5: `UV_PROJECT_ENVIRONMENT` relocates the
    venv outright, so an engine handed one would converge and describe two
    different environments."""
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "/tmp/elsewhere")
    monkeypatch.setenv("UV_NO_BUILD_ISOLATION", "1")
    monkeypatch.setenv("UV_CACHE_DIR", "/scratch/uv")

    with pytest.raises(SystemExit):
        maybe_delegate(["materialize"])

    _, _, env = execs[0]
    assert "UV_PROJECT_ENVIRONMENT" not in env
    assert "UV_NO_BUILD_ISOLATION" not in env
    # Kept: where bytes are cached is placement, never content — and it is
    # what a site registry legitimately supplies.
    assert env["UV_CACHE_DIR"] == "/scratch/uv"


def test_the_hand_over_really_execs(project: Path) -> None:
    """The one test that runs a real `os.execve`.

    Everything above fakes the exec, which means none of it can catch an
    argv that is well-formed and unrunnable. Here the engine is a shell
    script that reports what it was actually given, in a real process.
    """
    (project / ".venv" / "bin" / "lc").write_text(
        '#!/bin/sh\necho "argv:$*"\necho "delegated:$LC_DELEGATED"\n'
    )
    (project / ".venv" / "bin" / "lc").chmod(0o755)

    driver = textwrap.dedent(
        """
        import sys
        from lightcone.engine import project
        project.sync = lambda root: []          # no uv in a subprocess
        from lightcone.cli.launcher import maybe_delegate
        maybe_delegate(["materialize", "--refresh"])
        print("NOT REACHED")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", driver],
        cwd=project,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parent.parent / "src")},
    )

    assert result.returncode == 0, result.stderr
    assert "argv:materialize --refresh" in result.stdout
    assert "delegated:1" in result.stdout
    assert "NOT REACHED" not in result.stdout
