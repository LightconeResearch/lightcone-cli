"""The launcher: what delegates, what does not, and what crosses over."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lightcone.cli.launcher import DELEGATED_ENV, TOOL_ENV_VERBS, maybe_delegate

#: What the `execs` fixture records: the exec'd path, its argv, its env.
Execs = list[tuple[str, list[str], dict[str, str]]]

#: Every verb that is not in :data:`TOOL_ENV_VERBS`. Spelled out rather
#: than derived, so that adding a command to the CLI fails
#: :func:`test_every_verb_has_a_routing_decision` until someone decides
#: which side of the boundary it belongs on.
DELEGATING_VERBS = {"run", "materialize", "status"}


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A built project, standing in for its engine.

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
def execs(monkeypatch: pytest.MonkeyPatch) -> Execs:
    """Capture ``os.execve``, emulating the process hand-over by raising.

    A real exec never returns, so a fake that did would let the test run
    on through code the caller can never reach.
    """
    calls: Execs = []

    def fake_execve(path: str, argv: list[str], env: dict[str, str]) -> None:
        calls.append((path, argv, env))
        raise SystemExit(0)

    monkeypatch.setattr(os, "execve", fake_execve)
    return calls


@pytest.fixture
def uv_says(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Make every external tool answer with one canned result.

    ``MagicMock`` rather than a hand-rolled stand-in, as everywhere else in
    the suite: the moment the code under test reads a field a bespoke fake
    did not think to define, it fails on the fake instead of on the code.
    """

    def answer(returncode: int = 0, stderr: str = "") -> None:
        from lightcone.engine import project as project_module

        def fake(argv: list[str], *, cwd: Path) -> MagicMock:
            # The read-only probe always reports drift, so the sync under
            # test actually runs; the canned result is the sync's.
            if "--check" in argv:
                return MagicMock(returncode=1, stdout="", stderr="")
            return MagicMock(returncode=returncode, stdout="", stderr=stderr)

        monkeypatch.setattr(project_module, "_run", fake)

    return answer


# =============================================================================
# Routing — who is handed over, and who is not
# =============================================================================


def test_a_verb_in_a_project_is_handed_to_its_own_engine(project: Path, execs: Execs) -> None:
    with pytest.raises(SystemExit):
        maybe_delegate(["materialize", "--check"])

    path, argv, env = execs[0]
    assert path == str(project / ".venv" / "bin" / "lc")
    # The frozen interface: the argv verbatim, and one variable.
    assert argv == ["lc", "materialize", "--check"]
    assert env[DELEGATED_ENV] == "1"


def test_the_environment_is_converged_before_the_hand_over(
    project: Path, execs: Execs, tools: list[list[str]]
) -> None:
    """The engine that is about to run is the one the lock describes.

    Syncing after the exec would be too late — the process making that
    decision would already be gone.
    """
    with pytest.raises(SystemExit):
        maybe_delegate(["status"])

    assert [c for c in tools if c[0] == "uv"], tools


def test_a_converged_environment_is_verified_not_re_synced(
    project: Path, execs: Execs, tools: list[list[str]]
) -> None:
    """Against a converged environment the probe costs ~15 ms where the
    sync costs ~100 ms, because `--compile-bytecode` re-stamps every file
    whether or not anything moved. The launcher runs on every delegating
    verb, so it asks before it writes."""
    with pytest.raises(SystemExit):
        maybe_delegate(["status"])

    uv = [c for c in tools if c[0] == "uv"]
    assert uv and all("--check" in c for c in uv), f"converged should only probe: {uv}"


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
def test_what_never_delegates(argv: list[str], why: str, project: Path, execs: Execs) -> None:
    maybe_delegate(argv)
    assert execs == [], why


def test_nothing_is_stat_ed_before_a_verb_is_found(
    project: Path, execs: Execs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`lc --help` and shell completion are the hot path, and they run
    wherever the user happens to be standing."""

    def forbidden() -> Path:
        raise AssertionError("the working directory was read before the verb check")

    monkeypatch.setattr(Path, "cwd", staticmethod(forbidden))
    maybe_delegate(["--help"])
    assert execs == []


def test_a_second_hand_over_never_happens(
    project: Path, execs: Execs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The engine we exec runs this same code on the way in."""
    monkeypatch.setenv(DELEGATED_ENV, "1")
    maybe_delegate(["materialize"])
    assert execs == []


def test_a_directory_that_is_not_a_project_is_left_to_the_engine(
    tmp_path: Path, execs: Execs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Declining is the whole response — the refusal is written once, in
    `current_project`, and a second copy here could only disagree with it."""
    monkeypatch.chdir(tmp_path)
    maybe_delegate(["materialize"])
    assert execs == []


def test_a_uv_project_that_is_not_ours_is_never_converged(
    tmp_path: Path, execs: Execs, tools: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A uv project without an lc of its own must not be written to.

    `uv sync --exact` *uninstalls* whatever the lock does not name, so
    converging before checking for an engine would have `lc status`, typed
    in the wrong checkout, rewrite someone else's environment on its way
    to reporting that lc is not installed there.
    """
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "theirs"\n')
    (tmp_path / "uv.lock").write_text("version = 1\n")
    (tmp_path / ".venv" / "bin").mkdir(parents=True)  # a venv, but no `lc` in it
    monkeypatch.chdir(tmp_path)

    maybe_delegate(["status"])

    assert execs == []
    assert [c for c in tools if c[0] == "uv"] == [], "their environment was left alone"


def test_the_project_engine_does_not_hand_over_to_itself(
    project: Path, execs: Execs, tools: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`uv run lc …` already arrives in the project's environment.

    Asked of `sys.prefix`, which *is* the venv root. `sys.executable`
    cannot answer it: `.venv/bin/python` is a symlink out to the base
    interpreter, so resolving it names the interpreter's own directory and
    the comparison is false for the one case it was written for.
    """
    monkeypatch.setattr(sys, "prefix", str(project / ".venv"))
    maybe_delegate(["materialize"])

    assert execs == []
    assert [c for c in tools if c[0] == "uv"] == [], "nothing was converged either"


def test_every_verb_has_a_routing_decision() -> None:
    """A command added without a routing decision would delegate by
    default — into a project engine that may be too old to know it, which
    fails far from its cause. The launcher never imports `commands`, so
    this test is the only thing binding the two."""
    from lightcone.cli.commands import main

    assert set(main.commands) == TOOL_ENV_VERBS | DELEGATING_VERBS


def test_the_group_takes_no_option_with_a_value() -> None:
    """Verb detection is "the first argument that is not a flag".

    That holds only while every group-level option is a flag. Add one that
    takes a value and routing breaks both ways at once:
    `lc --log-level debug init` would read `debug` as the verb and
    delegate `init`, while `lc --log-level init materialize` would read
    `init` and refuse to delegate `materialize`.
    """
    from lightcone.cli.commands import main

    valued = [p.name for p in main.params if not getattr(p, "is_flag", False)]
    assert valued == [], f"group options taking a value break verb detection: {valued}"


# =============================================================================
# Failing loudly
# =============================================================================


def test_a_failed_converge_is_reported_here(
    project: Path, execs: Execs, uv_says: Callable[..., None]
) -> None:
    """Click has not been imported yet, so `_EngineErrorGroup` cannot
    render this one and the launcher has to say it itself."""
    uv_says(returncode=1, stderr="the lock is stale")

    with pytest.raises(SystemExit) as raised:
        maybe_delegate(["materialize"])

    assert "the lock is stale" in str(raised.value)
    assert execs == []


def test_a_uv_warning_reaches_the_user(
    project: Path,
    execs: Execs,
    uv_says: Callable[..., None],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The cache-on-another-filesystem warning is the one that must not be
    swallowed — nothing else would tell anyone their packages are being
    copied rather than linked."""
    uv_says(returncode=0, stderr="warning: Failed to hardlink files")

    with pytest.raises(SystemExit):
        maybe_delegate(["status"])

    assert "Failed to hardlink files" in capsys.readouterr().err
    assert execs, "and the hand-over still happened"


def test_an_engine_pruned_by_the_converge_is_not_exec_d(
    project: Path, execs: Execs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--exact` uninstalls what the lock does not name.

    A project that dropped `lightcone-cli` from its dependencies has no
    engine to hand over to once the environment agrees with its lock — and
    `execve` on the file that used to be there is a traceback, not an
    error message.
    """
    from lightcone.engine import project as project_module

    def prune(argv: list[str], *, cwd: Path) -> MagicMock:
        (project / ".venv" / "bin" / "lc").unlink(missing_ok=True)
        return MagicMock(returncode=1 if "--check" in argv else 0, stdout="", stderr="")

    monkeypatch.setattr(project_module, "_run", prune)

    maybe_delegate(["materialize"])
    assert execs == []


# =============================================================================
# What crosses the boundary
# =============================================================================


def test_ambient_uv_steering_does_not_cross(
    project: Path, execs: Execs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "/tmp/elsewhere")
    monkeypatch.setenv("UV_NO_BUILD_ISOLATION", "1")
    monkeypatch.setenv("UV_NO_CONFIG", "1")
    monkeypatch.setenv("UV_CACHE_DIR", "/scratch/uv")

    with pytest.raises(SystemExit):
        maybe_delegate(["materialize"])

    _, _, env = execs[0]
    for name in ("UV_PROJECT_ENVIRONMENT", "UV_NO_BUILD_ISOLATION", "UV_NO_CONFIG"):
        assert name not in env
    assert env["UV_CACHE_DIR"] == "/scratch/uv", "placement is kept"


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
        from lightcone.engine import project
        project.converge_environment = lambda root: []   # no uv in a subprocess
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
