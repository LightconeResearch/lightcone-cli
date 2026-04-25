"""Tests for ASTRA Container Runner — local/docker/venv backends only.

SLURM-specific tests moved to :mod:`tests.test_pilots`; the runner no
longer knows about sbatch.
"""
from __future__ import annotations

from lightcone.engine.runner import (
    ASTRAContainerRunner,
    translate_resources_to_docker_flags,
)


class TestResourceTranslation:
    def test_translate_cpus(self):
        assert "--cpus=4" in translate_resources_to_docker_flags({"cpus": 4})

    def test_translate_memory(self):
        assert "--memory=16gb" in translate_resources_to_docker_flags({"memory": "16GB"})

    def test_translate_gpus(self):
        assert "--gpus=1" in translate_resources_to_docker_flags({"gpus": 1})

    def test_translate_empty(self):
        assert translate_resources_to_docker_flags({}) == []


class TestDockerRunner:
    def test_execute_venv_fallback(self, tmp_path):
        """Without a container runtime, execute falls back to venv."""
        import subprocess
        import sys

        subprocess.run(
            [sys.executable, "-m", "venv", str(tmp_path / ".venv")],
            check=True, capture_output=True,
        )
        runner = ASTRAContainerRunner(project_root=str(tmp_path), backend="docker")
        result = runner.execute(
            command="python -c 'print(1)'",
            output_id="test_out", universe_id="baseline",
        )
        assert result.exit_code == 0
        assert result.metadata.get("backend") == "venv"

    def test_execute_with_container_string(self, tmp_path):
        runner = ASTRAContainerRunner(
            project_root=str(tmp_path), backend="docker",
            default_container="myimage:latest",
        )
        assert runner.default_container == "myimage:latest"
