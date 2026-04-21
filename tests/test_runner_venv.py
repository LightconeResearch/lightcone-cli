"""Tests for the venv execution backend."""

from __future__ import annotations

import subprocess
import sys

import pytest

from lightcone.engine.runner import ASTRAContainerRunner


@pytest.fixture()
def venv_project(tmp_path):
    """Create a project with a .venv and requirements.txt."""
    (tmp_path / "results" / "baseline").mkdir(parents=True)

    # Create a real venv
    subprocess.run(
        [sys.executable, "-m", "venv", str(tmp_path / ".venv")],
        check=True, capture_output=True,
    )

    return tmp_path


class TestVenvBackend:
    def test_venv_executes_command(self, venv_project):
        runner = ASTRAContainerRunner(
            project_root=str(venv_project),
            backend="venv",
        )
        result = runner.execute(
            command="python -c 'print(42)'",
            output_id="test",
            universe_id="baseline",
        )
        assert result.exit_code == 0
        assert result.metadata["backend"] == "venv"
        assert "42" in result.metadata["stdout"]

    def test_venv_metadata_includes_path(self, venv_project):
        runner = ASTRAContainerRunner(
            project_root=str(venv_project),
            backend="venv",
        )
        result = runner.execute(
            command="python -c 'pass'",
            output_id="test",
            universe_id="baseline",
        )
        assert result.metadata["venv_path"] == str(venv_project / ".venv")

    def test_venv_missing_returns_error(self, tmp_path):
        (tmp_path / "results" / "baseline").mkdir(parents=True)
        runner = ASTRAContainerRunner(
            project_root=str(tmp_path),
            backend="venv",
        )
        result = runner.execute(
            command="python -c 'pass'",
            output_id="test",
            universe_id="baseline",
        )
        assert result.exit_code == 1
        assert "No .venv found" in result.metadata["stderr"]

    def test_venv_installs_requirements(self, venv_project):
        # pip is always present in any venv — safe to use as a test dep
        (venv_project / "requirements.txt").write_text("pip\n")

        runner = ASTRAContainerRunner(
            project_root=str(venv_project),
            backend="venv",
        )
        result = runner.execute(
            command="python -c 'import pip; print(pip.__version__)'",
            output_id="test",
            universe_id="baseline",
        )
        assert result.exit_code == 0

    def test_venv_deps_hash_skips_reinstall(self, venv_project):
        (venv_project / "requirements.txt").write_text("pip\n")

        runner = ASTRAContainerRunner(
            project_root=str(venv_project),
            backend="venv",
        )
        # First run installs deps
        runner.execute(
            command="python -c 'pass'",
            output_id="test",
            universe_id="baseline",
        )
        marker = venv_project / ".venv" / ".deps-hash"
        assert marker.exists()
        first_hash = marker.read_text().strip()

        # Second run should skip install (hash unchanged)
        runner.execute(
            command="python -c 'pass'",
            output_id="test",
            universe_id="baseline",
        )
        assert marker.read_text().strip() == first_hash

    def test_venv_deps_hash_changes_on_new_requirements(self, venv_project):
        (venv_project / "requirements.txt").write_text("pip\n")

        runner = ASTRAContainerRunner(
            project_root=str(venv_project),
            backend="venv",
        )
        runner.execute(
            command="python -c 'pass'",
            output_id="test",
            universe_id="baseline",
        )
        marker = venv_project / ".venv" / ".deps-hash"
        first_hash = marker.read_text().strip()

        # Change requirements and create a new runner (cache is per-instance)
        (venv_project / "requirements.txt").write_text("pip\nsetuptools\n")
        runner2 = ASTRAContainerRunner(
            project_root=str(venv_project),
            backend="venv",
        )
        runner2.execute(
            command="python -c 'pass'",
            output_id="test",
            universe_id="baseline",
        )
        assert marker.read_text().strip() != first_hash


class TestContainerToVenvFallback:
    def test_docker_failure_falls_back_to_venv(self, venv_project):
        """When container execution fails, runner should fall back to venv."""
        runner = ASTRAContainerRunner(
            project_root=str(venv_project),
            backend="docker",
            default_container="nonexistent-image:latest",
        )
        result = runner.execute(
            command="python -c 'print(1)'",
            output_id="test",
            universe_id="baseline",
        )
        assert result.exit_code == 0
        assert result.metadata["backend"] == "venv"

    def test_docker_no_venv_falls_back_to_local(self, tmp_path):
        """When .venv is absent, docker backend falls back to local execution."""
        (tmp_path / "results" / "baseline").mkdir(parents=True)
        runner = ASTRAContainerRunner(
            project_root=str(tmp_path),
            backend="docker",
            default_container="nonexistent-image:latest",
        )
        result = runner.execute(
            command="python -c 'print(1)'",
            output_id="test",
            universe_id="baseline",
        )
        # Should not error — falls back to local when .venv is missing
        assert result.exit_code == 0
        assert result.metadata["backend"] == "local"

    def test_docker_no_container_no_venv_falls_back_to_local(self, tmp_path):
        """When no container and no .venv, docker backend runs locally."""
        (tmp_path / "results" / "baseline").mkdir(parents=True)
        runner = ASTRAContainerRunner(
            project_root=str(tmp_path),
            backend="docker",
        )
        result = runner.execute(
            command="python -c 'print(1)'",
            output_id="test",
            universe_id="baseline",
        )
        assert result.exit_code == 0
        assert result.metadata["backend"] == "local"
