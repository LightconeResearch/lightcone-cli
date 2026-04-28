"""Tests for the scratch resolution layer.

Covers the precedence chain (env > project > site > tempdir), the
per-run directory layout, and the snakemake symlink swap that ensures
snakemake's workflow lock and metadata land on a filesystem that
honours ``flock``.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from lightcone.engine.scratch import (
    LIGHTCONE_SCRATCH_ENV,
    RunLockBusyError,
    acquire_run_lock,
    ensure_snakemake_symlink,
    prepare_run_dirs,
    project_hash,
    resolve_scratch_root,
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    p.mkdir()
    (p / "astra.yaml").write_text("outputs: []\n")
    return p


@pytest.fixture(autouse=True)
def _no_known_site(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent the developer's actual hostname from leaking site detection
    into resolution; tests pin the precedence chain explicitly."""
    import socket
    monkeypatch.setattr(socket, "gethostname", lambda: "unknown-host-x")


# ---- resolve_scratch_root -------------------------------------------------


def test_env_var_wins(monkeypatch: pytest.MonkeyPatch, project: Path, tmp_path: Path) -> None:
    target = tmp_path / "env-scratch"
    monkeypatch.setenv(LIGHTCONE_SCRATCH_ENV, str(target))
    # Even with a project-level config set, env var takes precedence.
    (project / ".lightcone").mkdir()
    (project / ".lightcone" / "lightcone.yaml").write_text(
        yaml.safe_dump({"scratch_root": str(tmp_path / "from-project")})
    )
    assert resolve_scratch_root(project) == target


def test_env_var_expands(monkeypatch: pytest.MonkeyPatch, project: Path, tmp_path: Path) -> None:
    monkeypatch.setenv("MYSCRATCH", str(tmp_path / "x"))
    monkeypatch.setenv(LIGHTCONE_SCRATCH_ENV, "$MYSCRATCH/sub")
    assert resolve_scratch_root(project) == tmp_path / "x" / "sub"


def test_project_config(monkeypatch: pytest.MonkeyPatch, project: Path, tmp_path: Path) -> None:
    monkeypatch.delenv(LIGHTCONE_SCRATCH_ENV, raising=False)
    target = tmp_path / "proj-scratch"
    (project / ".lightcone").mkdir()
    (project / ".lightcone" / "lightcone.yaml").write_text(
        yaml.safe_dump({"scratch_root": str(target)})
    )
    assert resolve_scratch_root(project) == target


def test_project_config_expands(
    monkeypatch: pytest.MonkeyPatch, project: Path, tmp_path: Path
) -> None:
    monkeypatch.delenv(LIGHTCONE_SCRATCH_ENV, raising=False)
    monkeypatch.setenv("PROJSCRATCH", str(tmp_path / "expanded"))
    (project / ".lightcone").mkdir()
    (project / ".lightcone" / "lightcone.yaml").write_text(
        yaml.safe_dump({"scratch_root": "$PROJSCRATCH"})
    )
    assert resolve_scratch_root(project) == tmp_path / "expanded"


def test_site_default_resolves_when_env_set(
    monkeypatch: pytest.MonkeyPatch, project: Path, tmp_path: Path
) -> None:
    monkeypatch.delenv(LIGHTCONE_SCRATCH_ENV, raising=False)
    monkeypatch.setenv("SCRATCH", str(tmp_path / "lustre"))
    import socket
    monkeypatch.setattr(socket, "gethostname", lambda: "perlmutter-login01")
    assert resolve_scratch_root(project) == tmp_path / "lustre"


def test_site_default_falls_through_when_env_missing(
    monkeypatch: pytest.MonkeyPatch, project: Path
) -> None:
    """If a known site's scratch_root is ``$SCRATCH`` and ``SCRATCH`` is
    not set, the unexpanded ``$SCRATCH`` mustn't become a literal path —
    we fall through to the tempdir fallback instead.
    """
    monkeypatch.delenv(LIGHTCONE_SCRATCH_ENV, raising=False)
    monkeypatch.delenv("SCRATCH", raising=False)
    import socket
    monkeypatch.setattr(socket, "gethostname", lambda: "perlmutter-login01")
    resolved = resolve_scratch_root(project)
    assert "$" not in str(resolved)
    assert resolved == Path(tempfile.gettempdir())


def test_fallback_to_tempdir(monkeypatch: pytest.MonkeyPatch, project: Path) -> None:
    monkeypatch.delenv(LIGHTCONE_SCRATCH_ENV, raising=False)
    assert resolve_scratch_root(project) == Path(tempfile.gettempdir())


# ---- prepare_run_dirs -----------------------------------------------------


def test_prepare_run_dirs_creates_layout(
    monkeypatch: pytest.MonkeyPatch, project: Path, tmp_path: Path
) -> None:
    monkeypatch.setenv(LIGHTCONE_SCRATCH_ENV, str(tmp_path / "scratch"))
    rd = prepare_run_dirs(project, run_id="42")
    assert rd.root == tmp_path / "scratch" / ".lightcone"
    assert rd.dask_local == rd.root / "dask" / "42"
    assert rd.lock_path == rd.root / "locks" / "42.lock"
    assert rd.snakemake_state.parent.parent == rd.root / "snakemake"
    # Every path that callers rely on must exist on return.
    assert rd.root.is_dir()
    assert rd.dask_local.is_dir()
    assert rd.lock_path.is_file()
    assert rd.snakemake_state.parent.is_dir()


def test_project_hash_is_path_keyed(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    assert project_hash(a) != project_hash(b)
    assert project_hash(a) == project_hash(a)


def test_prepare_run_dirs_separates_projects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two different projects sharing one scratch must not collide on
    snakemake state — that would let one project's stale lock files
    block another's runs."""
    monkeypatch.setenv(LIGHTCONE_SCRATCH_ENV, str(tmp_path / "scratch"))
    a = tmp_path / "proj-a"
    a.mkdir()
    b = tmp_path / "proj-b"
    b.mkdir()
    rd_a = prepare_run_dirs(a, run_id="x")
    rd_b = prepare_run_dirs(b, run_id="x")
    assert rd_a.snakemake_state != rd_b.snakemake_state


# ---- ensure_snakemake_symlink --------------------------------------------


def test_symlink_created_when_absent(project: Path, tmp_path: Path) -> None:
    target = tmp_path / "scratch" / "snakemake"
    ensure_snakemake_symlink(project, target)
    link = project / ".snakemake"
    assert link.is_symlink()
    assert link.resolve() == target.resolve()


def test_symlink_idempotent(project: Path, tmp_path: Path) -> None:
    target = tmp_path / "scratch" / "snakemake"
    ensure_snakemake_symlink(project, target)
    ensure_snakemake_symlink(project, target)  # second call is a no-op
    assert (project / ".snakemake").is_symlink()


def test_symlink_repoints(project: Path, tmp_path: Path) -> None:
    a = tmp_path / "scratch-a" / "snakemake"
    b = tmp_path / "scratch-b" / "snakemake"
    ensure_snakemake_symlink(project, a)
    ensure_snakemake_symlink(project, b)
    assert (project / ".snakemake").resolve() == b.resolve()


# ---- acquire_run_lock -----------------------------------------------------


def test_run_lock_clears_stale_snakemake_locks(
    monkeypatch: pytest.MonkeyPatch, project: Path, tmp_path: Path
) -> None:
    """A snakemake lock left by a prior crashed run must not block the
    next ``lc run`` — once we hold our project-level flock, those zero-
    byte sentinels are known-stale and safe to remove."""
    monkeypatch.setenv(LIGHTCONE_SCRATCH_ENV, str(tmp_path / "scratch"))
    rd = prepare_run_dirs(project, run_id="test")
    snake_locks = rd.snakemake_state / "locks"
    snake_locks.mkdir(parents=True)
    (snake_locks / "0.input.lock").touch()
    (snake_locks / "0.output.lock").touch()
    with acquire_run_lock(rd):
        assert not (snake_locks / "0.input.lock").exists()
        assert not (snake_locks / "0.output.lock").exists()


def test_run_lock_rejects_concurrent_holder(
    monkeypatch: pytest.MonkeyPatch, project: Path, tmp_path: Path
) -> None:
    """Two concurrent ``lc run`` invocations on the same project must
    not silently queue — the second one bails so the user sees the
    collision and decides what to do."""
    monkeypatch.setenv(LIGHTCONE_SCRATCH_ENV, str(tmp_path / "scratch"))
    rd = prepare_run_dirs(project, run_id="test")
    with acquire_run_lock(rd):
        with pytest.raises(RunLockBusyError):
            with acquire_run_lock(rd):
                pass  # pragma: no cover


def test_legacy_real_dir_backed_up(project: Path, tmp_path: Path) -> None:
    """A pre-existing real ``.snakemake/`` (left by a direct snakemake
    invocation) must be moved aside, not deleted — losing real job
    metadata silently is the worse failure mode."""
    legacy = project / ".snakemake"
    legacy.mkdir()
    (legacy / "marker").write_text("evidence")
    target = tmp_path / "scratch" / "snakemake"
    ensure_snakemake_symlink(project, target)
    assert (project / ".snakemake").is_symlink()
    backup = project / ".snakemake.legacy"
    assert backup.is_dir()
    assert (backup / "marker").read_text() == "evidence"
