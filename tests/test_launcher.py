"""Tests for the tool-env launcher (discover → scrub → converge → exec)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from conftest import make_project

from lightcone import launcher
from lightcone.launcher import TOOL_ENV_VERBS, maybe_delegate


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = make_project(tmp_path / "proj")
    monkeypatch.chdir(p)
    return p


@pytest.fixture
def exec_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, list[str], dict]]:
    calls: list[tuple[str, list[str], dict]] = []

    def fake_execve(path: str, argv: list[str], env: dict) -> None:
        calls.append((path, argv, env))
        raise SystemExit(0)  # exec never returns; emulate process handoff

    monkeypatch.setattr(os, "execve", fake_execve)
    return calls


@pytest.fixture
def sync_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_converge(root: Path) -> None:
        calls.append(["sync", str(root)])
        (root / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
        (root / ".venv" / "bin" / "lc").write_text("#!/bin/sh\n")

    monkeypatch.setattr(launcher, "_converge_direct", fake_converge)
    return calls


class TestRouting:
    def test_tool_env_verbs_never_delegate(
        self, project: Path, exec_calls: list, sync_calls: list
    ) -> None:
        for verb in sorted(TOOL_ENV_VERBS):
            maybe_delegate([verb])
        assert exec_calls == []
        assert sync_calls == []

    def test_already_delegated_returns(
        self,
        project: Path,
        exec_calls: list,
        sync_calls: list,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("LC_DELEGATED", "1")
        maybe_delegate(["materialize"])
        assert exec_calls == []

    def test_no_args_returns(self, project: Path, exec_calls: list) -> None:
        maybe_delegate([])
        maybe_delegate(["--help"])
        maybe_delegate(["--version"])
        assert exec_calls == []

    def test_no_project_returns(
        self, tmp_path: Path, exec_calls: list, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        maybe_delegate(["materialize"])
        assert exec_calls == []


class TestDelegation:
    def test_frozen_interface(
        self, project: Path, exec_calls: list, sync_calls: list
    ) -> None:
        """THE frozen contract: exec of <venv>/bin/lc with verbatim argv
        passthrough + LC_DELEGATED=1 — a tool-env launcher of any
        version must be able to delegate to an engine of any age, so
        nothing else may travel across this boundary."""
        with pytest.raises(SystemExit):
            maybe_delegate(["materialize", "-u", "baseline", "best_fit"])
        assert len(exec_calls) == 1
        path, argv, env = exec_calls[0]
        assert path == str(project / ".venv" / "bin" / "lc")
        assert argv == ["lc", "materialize", "-u", "baseline", "best_fit"]
        assert env["LC_DELEGATED"] == "1"

    def test_converges_before_exec(
        self, project: Path, exec_calls: list, sync_calls: list
    ) -> None:
        with pytest.raises(SystemExit):
            maybe_delegate(["run", "python", "-V"])
        assert sync_calls == [["sync", str(project)]]

    def test_scrubs_ambient_uv_env(
        self,
        project: Path,
        exec_calls: list,
        sync_calls: list,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Ambient UV_* steering must never reach the engine — a
        UV_PROJECT pointing elsewhere would converge the wrong env."""
        monkeypatch.setenv("UV_PROJECT", "/somewhere/else")
        monkeypatch.setenv("UV_INDEX_URL", "https://evil.example/simple")
        with pytest.raises(SystemExit):
            maybe_delegate(["materialize"])
        _, _, env = exec_calls[0]
        assert "UV_PROJECT" not in env
        assert "UV_INDEX_URL" not in env

    def test_missing_engine_after_sync_fails_loud(
        self,
        project: Path,
        exec_calls: list,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Never a PATH fallback: a synced env without the engine binary
        is a hard, explained error."""
        monkeypatch.setattr(launcher, "_converge_direct", lambda root: None)
        with pytest.raises(SystemExit) as exc:
            maybe_delegate(["materialize"])
        assert exc.value.code == 1
        assert "uv add lightcone-cli" in capsys.readouterr().err
        assert exec_calls == []

    def test_containerized_returns_to_click(
        self,
        tmp_path: Path,
        exec_calls: list,
        sync_calls: list,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Interim: the containerized branch falls through to Click's
        refusal (the podman runtime backend replaces this)."""
        p = make_project(tmp_path / "proj", containerized=True)
        monkeypatch.chdir(p)
        maybe_delegate(["materialize"])
        assert exec_calls == []
        assert sync_calls == []

    def test_environment_error_is_clean(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        p = make_project(tmp_path / "proj")
        (p / "uv.lock").unlink()
        monkeypatch.chdir(p)
        with pytest.raises(SystemExit) as exc:
            maybe_delegate(["materialize"])
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "uv lock" in err
        assert "Traceback" not in err
