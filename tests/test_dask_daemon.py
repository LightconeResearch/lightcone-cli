"""Tests for the session-scoped Dask scheduler daemon.

What we cover here:

- ``scheduler_key`` switches on ``SLURM_JOB_ID``,
- ``scheduler_dirs`` lays out the per-key directory under scratch,
- ``ensure_scheduler`` reuses a live scheduler (no spawn),
- ``ensure_scheduler`` spawns a daemon when nothing is alive, cleans
  stale state, and times out cleanly,
- ``stop`` is silently a no-op when there's no scheduler, and signals
  the recorded PID otherwise.

We mock the ``Popen`` of the daemon and the TCP probe — actually
spinning up Dask is reserved for the smoke test in test_dask_cluster.
"""

from __future__ import annotations

import json
import os
import signal
from pathlib import Path

import pytest

from lightcone.engine.dask_daemon import (
    SPAWN_WAIT_SECONDS,
    ensure_scheduler,
    scheduler_dirs,
    scheduler_key,
    stop,
)
from lightcone.engine.scratch import LIGHTCONE_SCRATCH_ENV, project_hash


@pytest.fixture
def project(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    p.mkdir()
    (p / "astra.yaml").write_text("outputs: []\n")
    return p


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("SLURM_JOB_ID", "DASK_SCHEDULER_ADDRESS"):
        monkeypatch.delenv(var, raising=False)
    import socket

    monkeypatch.setattr(socket, "gethostname", lambda: "unknown-host-x")


@pytest.fixture
def scratch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    s = tmp_path / "scratch"
    monkeypatch.setenv(LIGHTCONE_SCRATCH_ENV, str(s))
    return s


# ---- key + dirs ----------------------------------------------------------


def test_scheduler_key_defaults_to_project_hash(project: Path) -> None:
    assert scheduler_key(project) == project_hash(project)


def test_scheduler_key_uses_slurm_job_id(
    monkeypatch: pytest.MonkeyPatch, project: Path
) -> None:
    """Inside an allocation the natural lifecycle scope is the
    allocation, not the project — workers spawned via srun belong to
    SLURM_JOB_ID, and a switch of project mid-allocation should reuse
    the same workers."""
    monkeypatch.setenv("SLURM_JOB_ID", "12345")
    assert scheduler_key(project) == "slurm-12345"


def test_scheduler_dirs_layout(scratch: Path, project: Path) -> None:
    d = scheduler_dirs(project)
    assert d.root == scratch / ".lightcone" / "dask-scheduler" / project_hash(project)
    assert d.root.is_dir()
    assert d.spill.is_dir()
    # The lock-file paths must be under root so a daemon flock'ing
    # owner.lock from inside the scheduler keeps the kernel-level
    # liveness invariant we rely on.
    for f in (d.owner_lock, d.spawn_lock, d.scheduler_file, d.meta_file, d.log_file):
        assert f.parent == d.root


# ---- ensure_scheduler ----------------------------------------------------


def test_ensure_reuses_live_scheduler(
    monkeypatch: pytest.MonkeyPatch, scratch: Path, project: Path
) -> None:
    """Fast path: scheduler.json points to an address that responds —
    return it without going near the spawn lock."""
    d = scheduler_dirs(project)
    d.scheduler_file.write_text(json.dumps({"address": "tcp://live:8786"}))

    monkeypatch.setattr(
        "lightcone.engine.dask_daemon._probe", lambda addr: addr == "tcp://live:8786"
    )

    def _no_spawn(*a: object, **kw: object) -> None:
        raise AssertionError("must not spawn when an existing scheduler is live")

    monkeypatch.setattr("lightcone.engine.dask_daemon._spawn_daemon", _no_spawn)

    assert ensure_scheduler(project) == "tcp://live:8786"


def test_ensure_spawns_when_nothing_running(
    monkeypatch: pytest.MonkeyPatch, scratch: Path, project: Path
) -> None:
    """No scheduler.json on disk → spawn the daemon and wait for it to
    write the address. We simulate the daemon by writing scheduler.json
    from inside the spawn stub."""
    d = scheduler_dirs(project)
    spawned: dict[str, bool] = {}

    def _fake_spawn(_proj: Path, dirs: object) -> None:
        spawned["yes"] = True
        d.scheduler_file.write_text(json.dumps({"address": "tcp://fresh:7000"}))

    monkeypatch.setattr("lightcone.engine.dask_daemon._spawn_daemon", _fake_spawn)
    monkeypatch.setattr("lightcone.engine.dask_daemon._probe", lambda addr: True)

    assert ensure_scheduler(project) == "tcp://fresh:7000"
    assert spawned["yes"] is True


def test_ensure_clears_stale_address_before_spawn(
    monkeypatch: pytest.MonkeyPatch, scratch: Path, project: Path
) -> None:
    """A scheduler.json from a SIGKILL'd daemon points at a dead
    address — it must be cleared before spawn, otherwise a partial-read
    race between the new daemon writing the file and our caller reading
    it could yield the stale address."""
    d = scheduler_dirs(project)
    d.scheduler_file.write_text(json.dumps({"address": "tcp://dead:1"}))
    d.meta_file.write_text(json.dumps({"pid": 999999, "address": "tcp://dead:1"}))

    probe_calls = {"n": 0}

    def _probe(addr: str) -> bool:
        probe_calls["n"] += 1
        # First call (fast path) probes the dead address: fail.
        # Subsequent calls (post-spawn) succeed.
        return addr == "tcp://fresh:8000"

    def _fake_spawn(_proj: Path, dirs: object) -> None:
        # By the time we're called, stale files must be gone — that's
        # the actual invariant under test.
        assert not d.scheduler_file.exists()
        assert not d.meta_file.exists()
        d.scheduler_file.write_text(json.dumps({"address": "tcp://fresh:8000"}))

    monkeypatch.setattr("lightcone.engine.dask_daemon._probe", _probe)
    monkeypatch.setattr("lightcone.engine.dask_daemon._spawn_daemon", _fake_spawn)

    assert ensure_scheduler(project) == "tcp://fresh:8000"
    assert probe_calls["n"] >= 2  # at least the fast-path probe + post-spawn probe


def test_ensure_times_out_with_clear_error(
    monkeypatch: pytest.MonkeyPatch, scratch: Path, project: Path
) -> None:
    """If the daemon never writes scheduler.json, ensure must surface
    that with a pointer to the daemon's log — silent hang here would
    look like a wedged ``lc run`` to the user."""
    monkeypatch.setattr("lightcone.engine.dask_daemon._probe", lambda addr: False)
    monkeypatch.setattr("lightcone.engine.dask_daemon._spawn_daemon", lambda *a, **k: None)
    monkeypatch.setattr(
        "lightcone.engine.dask_daemon.SPAWN_WAIT_SECONDS", 0.3
    )

    with pytest.raises(RuntimeError, match=r"did not come up"):
        ensure_scheduler(project)
    # Sanity: the constant we mocked is what controls the budget.
    assert SPAWN_WAIT_SECONDS  # original (untouched) constant still imports


# ---- stop ----------------------------------------------------------------


def test_stop_is_silent_when_nothing_running(scratch: Path, project: Path) -> None:
    assert stop(project) is False


def test_stop_handles_corrupt_meta(scratch: Path, project: Path) -> None:
    d = scheduler_dirs(project)
    d.meta_file.write_text("not json")
    assert stop(project) is False


def test_stop_signals_recorded_pid(
    monkeypatch: pytest.MonkeyPatch, scratch: Path, project: Path
) -> None:
    d = scheduler_dirs(project)
    d.meta_file.write_text(json.dumps({"pid": 42}))

    sent: dict[str, tuple[int, int]] = {}

    def _kill(pid: int, sig: int) -> None:
        sent["call"] = (pid, sig)

    monkeypatch.setattr(os, "kill", _kill)
    assert stop(project) is True
    assert sent["call"] == (42, signal.SIGTERM)


def test_stop_silent_on_dead_pid(
    monkeypatch: pytest.MonkeyPatch, scratch: Path, project: Path
) -> None:
    """A PID from an old run that no longer exists is the common case
    after SIGKILL — stop must swallow ProcessLookupError and report
    ``False`` so the SessionEnd hook stays quiet."""
    d = scheduler_dirs(project)
    d.meta_file.write_text(json.dumps({"pid": 1234567}))

    def _kill(pid: int, sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(os, "kill", _kill)
    assert stop(project) is False
