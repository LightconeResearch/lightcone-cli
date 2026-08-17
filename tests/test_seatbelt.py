"""Seatbelt profile generation (runs on Linux; enforcement smoke lives
in macOS CI)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from lightcone.engine.boundary import ExecScope
from lightcone.engine.sandbox.policy import build_policy
from lightcone.engine.sandbox.seatbelt import generate_profile


@pytest.fixture
def profile(tmp_path: Path) -> str:
    project = tmp_path / "proj"
    (project / "results" / "u1" / "foo").mkdir(parents=True)
    scope = ExecScope(
        project_root=project,
        output_dir=project / "results" / "u1" / "foo",
        read_paths=(),
    )
    policy = build_policy(scope, env_prefix=project / ".venv")
    try:
        return generate_profile(policy)
    finally:
        import shutil

        shutil.rmtree(policy.tmp_home, ignore_errors=True)


class TestProfileShape:
    def test_deny_default(self, profile: str) -> None:
        assert "(version 1)" in profile
        assert "(deny default)" in profile

    def test_loopback_only_network(self, profile: str) -> None:
        """'denied' means non-loopback blocked, loopback intact — the
        spec's meaning; in-recipe LocalCluster keeps working."""
        assert '(allow network-outbound (remote ip "localhost:*"))' in profile
        assert '(deny network-outbound (remote ip "*:*"))' in profile
        assert "network-bind" in profile

    def test_ipc_for_multiprocessing(self, profile: str) -> None:
        assert "(allow ipc-posix-shm*)" in profile
        assert "(allow ipc-posix-sem*)" in profile

    def test_project_readable_output_writable(
        self, profile: str, tmp_path: Path
    ) -> None:
        assert str(tmp_path / "proj") in profile
        assert "file-write*" in profile

    def test_dyld_executable(self, profile: str) -> None:
        assert '"/usr/lib/dyld"' in profile


@pytest.mark.darwin
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS enforcement smoke")
class TestEnforcementSmoke:
    """Runs in the macOS CI workflow only."""

    def test_write_outside_denied(self, tmp_path: Path) -> None:
        import os

        from lightcone.engine.sandbox.exec_boundary import SandboxExecBoundary

        project = tmp_path / "proj"
        (project / "results" / "u1" / "foo").mkdir(parents=True)
        (project / "data.txt").write_text("v1\n")
        scope = ExecScope(
            project_root=project,
            output_dir=project / "results" / "u1" / "foo",
            read_paths=(),
        )
        boundary = SandboxExecBoundary()
        ok = boundary.execute(
            "echo hi > results/u1/foo/x.txt", scope, env=dict(os.environ)
        )
        assert ok.returncode == 0, ok.stderr
        denied = boundary.execute(
            "echo overwrite > data.txt", scope, env=dict(os.environ)
        )
        assert denied.returncode != 0
        assert (project / "data.txt").read_text() == "v1\n"

    def test_non_loopback_network_denied(self, tmp_path: Path) -> None:
        import os

        from lightcone.engine.sandbox.exec_boundary import SandboxExecBoundary

        project = tmp_path / "proj"
        (project / "results" / "u1" / "foo").mkdir(parents=True)
        scope = ExecScope(
            project_root=project,
            output_dir=project / "results" / "u1" / "foo",
            read_paths=(),
        )
        r = SandboxExecBoundary().execute(
            "curl --max-time 3 -sS https://1.1.1.1 && echo REACHED",
            scope,
            env=dict(os.environ),
        )
        assert "REACHED" not in r.stdout
