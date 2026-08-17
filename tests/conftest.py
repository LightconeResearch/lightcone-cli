"""Shared test fixtures for lightcone-cli tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def tools(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Fake every external tool convergence shells out to, so the suite is
    hermetic — no network, no real resolution, no subprocesses.

    Models each tool's observable effect: ``uv lock`` writes ``uv.lock``,
    ``uv sync`` materializes ``.venv``, ``git init`` makes ``.git``. The
    ``--check`` probes answer from existence — enough for the convergence
    tests; drift is exercised by tests that stub ``_run`` themselves, since
    only uv can really tell a stale lock from a current one.

    Returns the recorded argv lists, so a test can assert on *which* tools
    ran with *which* flags. :func:`uv_calls` narrows that to uv.
    """
    calls: list[list[str]] = []

    def fake_run(argv: list[str], *, cwd: Path) -> MagicMock:
        calls.append(list(argv))
        project = _project(argv) if "--project" in argv else cwd
        if argv[0] == "uv" and "--check" in argv:
            artifact = "uv.lock" if argv[1] == "lock" else ".venv"
            return MagicMock(returncode=0 if (project / artifact).exists() else 1)
        if argv[:2] == ["uv", "lock"]:
            # Only has to exist and be non-empty — layer 1 parses no lock.
            (project / "uv.lock").write_text("version = 1\n")
        elif argv[:2] == ["uv", "sync"]:
            (project / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
        elif argv[:2] == ["git", "init"]:
            (cwd / ".git").mkdir(exist_ok=True)
        return MagicMock(returncode=0, stdout="", stderr="")

    from lightcone.engine import project

    monkeypatch.setattr(project, "_run", fake_run)
    monkeypatch.setattr(project.shutil, "which", lambda name, path=None: f"/usr/bin/{name}")
    return calls


def _project(argv: list[str]) -> Path:
    """The project root a uv invocation was pointed at."""
    return Path(argv[argv.index("--project") + 1])


def uv_calls(calls: list[list[str]]) -> list[list[str]]:
    """Just the uv invocations, with the leading ``uv`` stripped."""
    return [c[1:] for c in calls if c[0] == "uv"]


def probes(calls: list[list[str]]) -> list[list[str]]:
    """Just the read-only ``--check`` probes."""
    return [c for c in uv_calls(calls) if "--check" in c]
