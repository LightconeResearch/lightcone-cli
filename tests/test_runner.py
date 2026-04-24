"""Tests for ASTRA Container Runner."""
from __future__ import annotations

from lightcone.engine.runner import (
    ASTRAContainerRunner,
    translate_resources_to_docker_flags,
)


class TestResourceTranslation:
    def test_translate_cpus(self):
        flags = translate_resources_to_docker_flags({"cpus": 4})
        assert "--cpus=4" in flags

    def test_translate_memory(self):
        flags = translate_resources_to_docker_flags({"memory": "16GB"})
        assert "--memory=16gb" in flags

    def test_translate_gpus(self):
        flags = translate_resources_to_docker_flags({"gpus": 1})
        assert "--gpus=1" in flags

    def test_translate_empty(self):
        flags = translate_resources_to_docker_flags({})
        assert flags == []

    def test_translate_time_limit(self):
        flags = translate_resources_to_docker_flags({"time_limit": "2h"})
        assert isinstance(flags, list)


class TestDockerRunner:
    def test_execute_venv_fallback(self, tmp_path):
        """Without a container runtime, execute falls back to venv."""
        import subprocess
        import sys

        # Create a minimal .venv for the fallback
        subprocess.run(
            [sys.executable, "-m", "venv", str(tmp_path / ".venv")],
            check=True, capture_output=True,
        )

        runner = ASTRAContainerRunner(
            project_root=str(tmp_path),
            backend="docker",
        )
        result = runner.execute(
            command="python -c 'print(1)'",
            output_id="test_out",
            universe_id="baseline",
        )
        # Should succeed via venv fallback
        assert result.exit_code == 0
        assert result.metadata.get("backend") == "venv"

    def test_execute_with_container_string(self, tmp_path):
        """Runner stores default_container from init."""
        runner = ASTRAContainerRunner(
            project_root=str(tmp_path),
            backend="docker",
            default_container="myimage:latest",
        )
        assert runner.default_container == "myimage:latest"


# ---------------------------------------------------------------------------
# build_recipe_shell_command
# ---------------------------------------------------------------------------


class TestBuildRecipeShellCommand:
    """The shell command lifted out of the sbatch context.

    Same string content the old _podman_hpc_run_command + symlink loop
    produced, but standalone — used by both the Parsl-backed _run_slurm
    and (eventually) any direct-shell debugging.
    """

    def test_no_container_no_external_inputs(self, tmp_path):
        from lightcone.engine.runner import build_recipe_shell_command

        cmd = build_recipe_shell_command(
            command="python scripts/train.py",
            container=None,
            container_runtime=None,
            project_root=tmp_path,
            resources={},
            cwd=str(tmp_path),
            external_inputs=None,
        )
        assert cmd.endswith("python scripts/train.py")
        assert f"cd {tmp_path}" in cmd

    def test_no_container_with_external_inputs(self, tmp_path):
        from lightcone.engine.runner import build_recipe_shell_command

        cmd = build_recipe_shell_command(
            command="python scripts/analyze.py",
            container=None,
            container_runtime=None,
            project_root=tmp_path,
            resources={},
            cwd=str(tmp_path),
            external_inputs={"sim": "/pscratch/sim"},
        )
        assert "mkdir -p data" in cmd
        assert "ln -sfn /pscratch/sim data/sim" in cmd
        assert "python scripts/analyze.py" in cmd

    def test_podman_hpc_basic(self, tmp_path):
        from lightcone.engine.runner import build_recipe_shell_command

        cmd = build_recipe_shell_command(
            command="python scripts/train.py",
            container="ghcr.io/proj/ml:latest",
            container_runtime="podman-hpc",
            project_root=tmp_path,
            resources={"cpus": 4},
            cwd=str(tmp_path),
            external_inputs=None,
        )
        assert "podman-hpc run --rm" in cmd
        assert f"-v {tmp_path}:/workspace" in cmd
        assert "-w /workspace" in cmd
        assert "ghcr.io/proj/ml:latest" in cmd
        assert "python scripts/train.py" in cmd

    def test_podman_hpc_with_gpu(self, tmp_path):
        from lightcone.engine.runner import build_recipe_shell_command

        cmd = build_recipe_shell_command(
            command="python train.py",
            container="img:1.0",
            container_runtime="podman-hpc",
            project_root=tmp_path,
            resources={"gpus": 1},
            cwd=str(tmp_path),
            external_inputs=None,
        )
        assert "--gpu" in cmd

    def test_podman_hpc_multinode_adds_mpi(self, tmp_path):
        from lightcone.engine.runner import build_recipe_shell_command

        cmd = build_recipe_shell_command(
            command="python train.py",
            container="img:1.0",
            container_runtime="podman-hpc",
            project_root=tmp_path,
            resources={"nodes": 2},
            cwd=str(tmp_path),
            external_inputs=None,
        )
        assert "--mpi" in cmd

    def test_podman_hpc_external_inputs_become_volume_mounts(self, tmp_path):
        from lightcone.engine.runner import build_recipe_shell_command

        cmd = build_recipe_shell_command(
            command="python analyze.py",
            container="img:1.0",
            container_runtime="podman-hpc",
            project_root=tmp_path,
            resources={},
            cwd=str(tmp_path),
            external_inputs={"sim": "/pscratch/sim"},
        )
        assert "-v /pscratch/sim:/workspace/data/sim:ro" in cmd

    def test_cwd_changes_to_subanalysis_dir(self, tmp_path):
        from lightcone.engine.runner import build_recipe_shell_command

        sub = tmp_path / "sub"
        sub.mkdir()
        cmd = build_recipe_shell_command(
            command="python sub_script.py",
            container=None,
            container_runtime=None,
            project_root=tmp_path,
            resources={},
            cwd=str(sub),
            external_inputs=None,
        )
        assert f"cd {sub}" in cmd
