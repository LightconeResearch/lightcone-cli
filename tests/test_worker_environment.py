"""Per-run recipe settings on a worker shared by several projects."""

from __future__ import annotations

import os
import pickle
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Literal

import pytest

from lightcone.engine import assets, container, plan, project, sandbox, worker

_LOCAL_SETTINGS = (
    "SLURM_JOB_ID",
    "SLURMD_NODENAME",
    "HOSTNAME",
    "TMPDIR",
    "XDG_RUNTIME_DIR",
    "JUPYTER_IMAGE_SPEC",
    "JPY_PARENT_PID",
    "DISPLAY",
    "SSH_AUTH_SOCK",
)


def _context(
    root: Path, mode: Literal["direct", "containerized"] = "direct"
) -> worker.RunContext:
    return worker.RunContext(
        env_version="test",
        head=("commit", "origin"),
        versions=assets.Versions(),
        runtime=container.Runtime(root, mode, root / ".venv"),
        uv_version="test",
    )


def _task(root: Path, name: str = "result") -> plan.Task:
    return plan.Task("baseline", name, root / f"{name}.txt", name, {}, {}, {}, "test")


def test_context_snapshots_the_driver_without_host_or_uv_install_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in _LOCAL_SETTINGS:
        monkeypatch.setenv(name, "driver")
    monkeypatch.setenv("LC_RECIPE_SETTING", "captured")
    monkeypatch.setenv("VIRTUAL_ENV", "/driver/venv")
    monkeypatch.setenv("UV_PYTHON", "3.11")
    monkeypatch.setenv("UV_CACHE_DIR", "/shared/uv")
    monkeypatch.setenv("UV_INDEX_PRIVATE_PASSWORD", "driver-secret")

    context = _context(tmp_path)
    monkeypatch.setenv("LC_RECIPE_SETTING", "later")

    assert context.environment["LC_RECIPE_SETTING"] == "captured"
    assert context.environment["UV_CACHE_DIR"] == "/shared/uv"
    assert context.environment["UV_INDEX_PRIVATE_PASSWORD"] == "driver-secret"
    assert not set(_LOCAL_SETTINGS) & context.environment.keys()
    assert "VIRTUAL_ENV" not in context.environment
    assert "UV_PYTHON" not in context.environment
    assert pickle.loads(pickle.dumps(context)).environment == context.environment
    assert "driver-secret" not in repr(context)


@pytest.mark.parametrize("mode", ["direct", "containerized"])
def test_recipe_uses_driver_settings_and_only_host_local_worker_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: Literal["direct", "containerized"]
) -> None:
    monkeypatch.setenv("LC_RECIPE_SETTING", "driver")
    monkeypatch.delenv("LC_OLD_CREDENTIAL", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    for name in _LOCAL_SETTINGS:
        monkeypatch.setenv(name, "driver")
    context = _context(tmp_path, mode)

    monkeypatch.setenv("LC_RECIPE_SETTING", "worker")
    monkeypatch.setenv("LC_OLD_CREDENTIAL", "stale-secret")
    monkeypatch.setenv("HTTPS_PROXY", "https://stale.invalid")
    for name in _LOCAL_SETTINGS:
        monkeypatch.setenv(name, "worker")
    monkeypatch.delenv("SSH_AUTH_SOCK")
    before = dict(os.environ)
    observed: dict[str, str] = {}

    def capture(
        backend: sandbox.Backend,
        policy: sandbox.Policy,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: dict[str, str],
        prefix: Sequence[str],
    ) -> sandbox.Outcome:
        observed.update(env)
        return sandbox.Outcome(1, sandbox.Attestation("none", "open"))

    monkeypatch.setattr(worker, "_gate", lambda *_: "")
    monkeypatch.setattr(container, "backend", lambda _: sandbox.Unavailable())
    monkeypatch.setattr(sandbox, "run", capture)
    worker.execute(tmp_path, _task(tmp_path), {}, context)

    assert observed["LC_RECIPE_SETTING"] == "driver"
    assert "LC_OLD_CREDENTIAL" not in observed
    assert "HTTPS_PROXY" not in observed
    assert "SSH_AUTH_SOCK" not in observed
    assert all(observed[name] == "worker" for name in _LOCAL_SETTINGS if name != "SSH_AUTH_SOCK")
    assert dict(os.environ) == before


def test_concurrent_runs_do_not_change_the_worker_or_each_others_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LC_RECIPE_SETTING", "first")
    first = _context(tmp_path)
    monkeypatch.setenv("LC_RECIPE_SETTING", "second")
    second = _context(tmp_path)
    monkeypatch.setenv("LC_RECIPE_SETTING", "worker")
    before = dict(os.environ)
    together = Barrier(2)
    observed: dict[str, str] = {}

    def capture(
        backend: sandbox.Backend,
        policy: sandbox.Policy,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: dict[str, str],
        prefix: Sequence[str],
    ) -> sandbox.Outcome:
        together.wait(timeout=5)
        observed[argv[-1]] = env["LC_RECIPE_SETTING"]
        assert dict(os.environ) == before
        return sandbox.Outcome(1, sandbox.Attestation("none", "open"))

    monkeypatch.setattr(worker, "_gate", lambda *_: "")
    monkeypatch.setattr(container, "backend", lambda _: sandbox.Unavailable())
    monkeypatch.setattr(sandbox, "run", capture)
    with ThreadPoolExecutor(max_workers=2) as pool:
        calls = [
            pool.submit(worker.execute, tmp_path, _task(tmp_path, "one"), {}, first),
            pool.submit(worker.execute, tmp_path, _task(tmp_path, "two"), {}, second),
        ]
        for call in calls:
            call.result(timeout=10)

    assert observed == {"one": "first", "two": "second"}
    assert dict(os.environ) == before


def test_explicit_child_environment_does_not_add_worker_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LC_STALE_VALUE", "worker")
    assert project.child_env({}) == {}
    assert project.child_env({"VIRTUAL_ENV": "/other", "UV_PYTHON": "3.11", "KEY": "value"}) == {
        "KEY": "value"
    }
