"""Pure-function tests for the sandbox policy builder."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from lightcone.engine.boundary import ExecScope
from lightcone.engine.sandbox.policy import (
    EXEC_ALLOWLIST_V1,
    EXEC_ALLOWLIST_VERSION,
    build_policy,
)


@pytest.fixture
def scope(tmp_path: Path) -> ExecScope:
    project = tmp_path / "proj"
    (project / "results" / "u1" / "foo").mkdir(parents=True)
    return ExecScope(
        project_root=project,
        output_dir=project / "results" / "u1" / "foo",
        read_paths=(),
    )


def _cleanup(policy) -> None:  # type: ignore[no-untyped-def]
    shutil.rmtree(policy.tmp_home, ignore_errors=True)


def test_write_set(scope: ExecScope, tmp_path: Path) -> None:
    policy = build_policy(scope, env_prefix=tmp_path / "noenv")
    try:
        writes = {str(p) for p in policy.write}
        assert str(scope.output_dir.resolve()) in writes
        assert "/tmp" in writes
        assert str(policy.tmp_home) in writes
        # The project tree is NOT writable by default.
        assert str(scope.project_root.resolve()) not in writes
        assert policy.fs_scope == "declared"
    finally:
        _cleanup(policy)


def test_writable_project_escalation(scope: ExecScope, tmp_path: Path) -> None:
    escalated = ExecScope(
        project_root=scope.project_root,
        output_dir=scope.output_dir,
        read_paths=(),
        writable_project=True,
    )
    policy = build_policy(escalated, env_prefix=tmp_path / "noenv")
    try:
        assert str(scope.project_root.resolve()) in {str(p) for p in policy.write}
        assert policy.fs_scope == "project-rw"
    finally:
        _cleanup(policy)


def test_probe_scope_has_no_in_tree_write(tmp_path: Path) -> None:
    """Probes (no output dir) write only to the tmp scope — never
    in-tree."""
    project = tmp_path / "proj"
    project.mkdir()
    probe_scope = ExecScope(project_root=project, output_dir=None, read_paths=())
    policy = build_policy(probe_scope, env_prefix=tmp_path / "noenv")
    try:
        for p in policy.write:
            assert not str(p).startswith(str(project.resolve()))
    finally:
        _cleanup(policy)


def test_home_xdg_contract(scope: ExecScope, tmp_path: Path) -> None:
    """Fresh per-recipe HOME; matplotlib/astropy work on first import;
    bytecode caches redirect to the tmp scope (the approved §7
    amendment); the real $HOME is simply not granted."""
    policy = build_policy(scope, env_prefix=tmp_path / "noenv")
    try:
        home = Path(policy.env["HOME"])
        assert home == policy.tmp_home
        assert home.is_dir()
        for key in (
            "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
            "MPLCONFIGDIR", "PYTHONPYCACHEPREFIX",
        ):
            assert Path(policy.env[key]).is_dir()
            assert str(policy.env[key]).startswith(str(home))
        assert str(Path.home()) not in {str(p) for p in policy.read}
        assert str(Path.home()) not in {str(p) for p in policy.write}
    finally:
        _cleanup(policy)


def test_exec_allowlist_versioned(scope: ExecScope, tmp_path: Path) -> None:
    policy = build_policy(scope, env_prefix=tmp_path / "noenv")
    try:
        assert policy.exec_allowlist_version == EXEC_ALLOWLIST_VERSION == 1
        assert "bash" in EXEC_ALLOWLIST_V1 and "awk" in EXEC_ALLOWLIST_V1
    finally:
        _cleanup(policy)


@pytest.mark.skipif(sys.platform != "linux", reason="ELF loaders are Linux")
def test_elf_loader_tier_present(scope: ExecScope, tmp_path: Path) -> None:
    """Without the loader every dynamically linked binary fails EACCES —
    the loader tier must be in the exec set."""
    policy = build_policy(scope, env_prefix=tmp_path / "noenv")
    try:
        assert any("ld-linux" in str(p) or "ld-musl" in str(p) for p in policy.execute)
    finally:
        _cleanup(policy)


def test_venv_bin_and_real_interpreter_granted(tmp_path: Path) -> None:
    """The env bin dir gets a directory grant, and the symlink-resolved
    interpreter install root is granted too (realpath every policy
    path)."""
    project = tmp_path / "proj"
    (project / "out").mkdir(parents=True)
    venv_bin = project / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").symlink_to(sys.executable)
    scope = ExecScope(
        project_root=project, output_dir=project / "out", read_paths=()
    )
    policy = build_policy(scope, env_prefix=project / ".venv")
    try:
        execs = {str(p) for p in policy.execute}
        assert str(venv_bin.resolve()) in execs
        real_root = Path(sys.executable).resolve().parent.parent
        assert str(real_root) in execs
    finally:
        _cleanup(policy)


def test_all_paths_realpathed(scope: ExecScope, tmp_path: Path) -> None:
    policy = build_policy(scope, env_prefix=tmp_path / "noenv")
    try:
        for p in (*policy.read, *policy.write, *policy.execute):
            assert p == p.resolve(), p
    finally:
        _cleanup(policy)
