"""Shared test fixtures for lightcone-cli tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

#: Minimal lockfile body the faked ``uv lock`` writes. Its only job is to
#: exist and be non-empty — nothing in layer 1 parses a lock.
UV_LOCK_MIN = 'version = 1\nrequires-python = ">=3.12"\n'


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``~`` to a temp dir so tests can't touch the real home."""
    fake_home = tmp_path / "_home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    return fake_home


@pytest.fixture(autouse=True)
def fake_uv(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Fake the uv seam so convergence tests are hermetic — no network, no
    real resolution. Emulates the observable effects: ``uv lock`` writes
    ``uv.lock``, ``uv sync`` materializes ``.venv``.

    Returns the recorded argv lists, so a test can assert on *which* uv
    commands ran with *which* flags.
    """
    calls: list[list[str]] = []

    def fake_run_uv(args: list[str], *, cwd: Path) -> MagicMock:
        calls.append(list(args))
        project = Path(args[args.index("--project") + 1])
        if args[0] == "lock":
            (project / "uv.lock").write_text(UV_LOCK_MIN)
        elif args[0] == "sync":
            (project / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
        return MagicMock(returncode=0, stdout="", stderr="")

    from lightcone.engine import project

    monkeypatch.setattr(project, "_run_uv", fake_run_uv)
    monkeypatch.setattr(project.shutil, "which", lambda name, path=None: f"/usr/bin/{name}")
    return calls
