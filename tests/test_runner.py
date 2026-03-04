"""Tests for ASTRA Container Runner."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from prism.dagster.runner import (
    ASTRAContainerRunner,
    _check_sacct,
    _normalise_time_limit,
    _parse_sbatch_job_id,
    _podman_hpc_run_command,
    _shell_quote,
    generate_sbatch_script,
    translate_resources_to_docker_flags,
    translate_resources_to_slurm_directives,
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
    def test_execute_local_fallback(self, tmp_path):
        """Without Docker, execute falls back to local subprocess."""
        runner = ASTRAContainerRunner(
            project_root=str(tmp_path),
            backend="docker",
        )
        result = runner.execute(
            command="python -c 'print(1)'",
            output_id="test_out",
            universe_id="baseline",
        )
        # Should succeed via local fallback
        assert result.exit_code == 0
        assert result.metadata.get("backend") == "local"

    def test_execute_with_container_string(self, tmp_path):
        """Runner stores default_container from init."""
        runner = ASTRAContainerRunner(
            project_root=str(tmp_path),
            backend="docker",
            default_container="myimage:latest",
        )
        assert runner.default_container == "myimage:latest"


# ---------------------------------------------------------------------------
# SLURM resource translation
# ---------------------------------------------------------------------------


class TestSlurmResourceTranslation:
    def test_translate_cpus(self):
        dirs = translate_resources_to_slurm_directives({"cpus": 4})
        assert "--cpus-per-task=4" in dirs

    def test_translate_memory(self):
        dirs = translate_resources_to_slurm_directives({"memory": "16GB"})
        assert "--mem=16GB" in dirs

    def test_translate_gpus(self):
        dirs = translate_resources_to_slurm_directives({"gpus": 1})
        assert "--gpus=1" in dirs

    def test_translate_nodes(self):
        dirs = translate_resources_to_slurm_directives({"nodes": 2})
        assert "--nodes=2" in dirs

    def test_translate_time_limit_hours(self):
        dirs = translate_resources_to_slurm_directives({"time_limit": "2h"})
        assert "--time=02:00:00" in dirs

    def test_translate_time_limit_minutes(self):
        dirs = translate_resources_to_slurm_directives({"time_limit": "30m"})
        assert "--time=00:30:00" in dirs

    def test_translate_time_limit_int(self):
        dirs = translate_resources_to_slurm_directives({"time_limit": 90})
        assert "--time=01:30:00" in dirs

    def test_translate_time_limit_passthrough(self):
        dirs = translate_resources_to_slurm_directives({"time_limit": "01:30:00"})
        assert "--time=01:30:00" in dirs

    def test_translate_empty(self):
        dirs = translate_resources_to_slurm_directives({})
        assert dirs == []

    def test_scheduler_config_account(self):
        dirs = translate_resources_to_slurm_directives(
            {}, scheduler_config={"account": "m1234"},
        )
        assert "--account=m1234" in dirs

    def test_scheduler_config_full(self):
        dirs = translate_resources_to_slurm_directives(
            {"cpus": 4},
            scheduler_config={
                "account": "m1234",
                "partition": "gpu",
                "qos": "regular",
                "constraint": "gpu&hbm80g",
            },
        )
        assert "--account=m1234" in dirs
        assert "--partition=gpu" in dirs
        assert "--qos=regular" in dirs
        assert "--constraint=gpu&hbm80g" in dirs
        assert "--cpus-per-task=4" in dirs

    # -- Default walltime from resource_limits --

    def test_default_walltime_from_resource_limits(self):
        """When no time_limit and resource_limits has max_walltime_minutes, use it."""
        dirs = translate_resources_to_slurm_directives(
            {"cpus": 4},
            resource_limits={"max_walltime_minutes": 120},
        )
        assert "--time=02:00:00" in dirs

    def test_default_walltime_fallback_30(self):
        """When no time_limit and resource_limits has no max_walltime_minutes, default to 30m."""
        dirs = translate_resources_to_slurm_directives(
            {},
            resource_limits={},
        )
        assert "--time=00:30:00" in dirs

    def test_explicit_time_limit_overrides_resource_limits(self):
        """Explicit time_limit in resources takes precedence over resource_limits default."""
        dirs = translate_resources_to_slurm_directives(
            {"time_limit": "4h"},
            resource_limits={"max_walltime_minutes": 120},
        )
        time_directives = [d for d in dirs if d.startswith("--time=")]
        assert time_directives == ["--time=04:00:00"]

    def test_no_resource_limits_no_default(self):
        """Backward compat: no resource_limits param means no default walltime injected."""
        dirs = translate_resources_to_slurm_directives({})
        assert dirs == []


# ---------------------------------------------------------------------------
# Time limit normalisation
# ---------------------------------------------------------------------------


class TestNormaliseTimeLimit:
    def test_hours(self):
        assert _normalise_time_limit("2h") == "02:00:00"
        assert _normalise_time_limit("12H") == "12:00:00"

    def test_minutes(self):
        assert _normalise_time_limit("30m") == "00:30:00"
        assert _normalise_time_limit("90M") == "01:30:00"

    def test_bare_int(self):
        assert _normalise_time_limit(120) == "02:00:00"
        assert _normalise_time_limit(45) == "00:45:00"

    def test_bare_string_number(self):
        assert _normalise_time_limit("60") == "01:00:00"

    def test_passthrough(self):
        assert _normalise_time_limit("01:30:00") == "01:30:00"


# ---------------------------------------------------------------------------
# sbatch script generation
# ---------------------------------------------------------------------------


class TestGenerateSbatchScript:
    def test_podman_hpc_basic(self, tmp_path):
        script = generate_sbatch_script(
            command="python scripts/train.py",
            container="ghcr.io/proj/ml:latest",
            container_runtime="podman-hpc",
            project_root=tmp_path,
            output_id="trained_model",
            universe_id="baseline",
            resources={"cpus": 4, "memory": "16GB"},
            scheduler_config={"account": "m1234", "partition": "gpu"},
        )
        assert "#!/bin/bash" in script
        assert "#SBATCH --job-name=prism_trained_model_baseline" in script
        assert "#SBATCH --account=m1234" in script
        assert "#SBATCH --partition=gpu" in script
        assert "#SBATCH --cpus-per-task=4" in script
        assert "#SBATCH --mem=16GB" in script
        assert "podman-hpc run --rm" in script
        assert f"-v {tmp_path}:/workspace" in script
        assert "-w /workspace" in script
        assert "ghcr.io/proj/ml:latest" in script
        assert "python scripts/train.py" in script
        # No --image directive for podman-hpc
        assert "#SBATCH --image=" not in script

    def test_podman_hpc_with_gpu(self, tmp_path):
        script = generate_sbatch_script(
            command="python scripts/train.py",
            container="ghcr.io/proj/ml:latest",
            container_runtime="podman-hpc",
            project_root=tmp_path,
            output_id="train",
            universe_id="baseline",
            resources={"cpus": 4, "gpus": 1},
            scheduler_config={"account": "m1234"},
        )
        assert "--gpu" in script
        assert "#SBATCH --gpus=1" in script

    def test_podman_hpc_with_mpi_flags(self, tmp_path):
        script = generate_sbatch_script(
            command="python scripts/train.py",
            container="ghcr.io/proj/ml:latest",
            container_runtime="podman-hpc",
            project_root=tmp_path,
            output_id="train",
            universe_id="baseline",
            resources={},
            scheduler_config={
                "account": "m1234",
                "container_flags": ["--mpi", "--nccl"],
            },
        )
        assert "--mpi" in script
        assert "--nccl" in script

    def test_podman_hpc_with_custom_flags(self, tmp_path):
        """Custom container_flags like --scratch pass through to podman-hpc."""
        script = generate_sbatch_script(
            command="python scripts/train.py",
            container="ghcr.io/proj/ml:latest",
            container_runtime="podman-hpc",
            project_root=tmp_path,
            output_id="train",
            universe_id="baseline",
            resources={},
            scheduler_config={
                "account": "m1234",
                "container_flags": ["--scratch", "--cfs"],
            },
        )
        assert "--scratch" in script
        assert "--cfs" in script

    def test_no_container(self, tmp_path):
        script = generate_sbatch_script(
            command="python scripts/train.py",
            container=None,
            container_runtime="podman-hpc",
            project_root=tmp_path,
            output_id="train",
            universe_id="baseline",
            resources={"cpus": 4},
            scheduler_config={"account": "m1234"},
        )
        assert "podman-hpc" not in script
        assert "python scripts/train.py" in script

    def test_output_and_error_files(self, tmp_path):
        script = generate_sbatch_script(
            command="echo hello",
            container=None,
            container_runtime="podman-hpc",
            project_root=tmp_path,
            output_id="test",
            universe_id="baseline",
            resources={},
        )
        assert "#SBATCH --output=results/.slurm/test_baseline.out" in script
        assert "#SBATCH --error=results/.slurm/test_baseline.err" in script

    def test_qos_and_constraint(self, tmp_path):
        script = generate_sbatch_script(
            command="echo hello",
            container="img:1.0",
            container_runtime="podman-hpc",
            project_root=tmp_path,
            output_id="test",
            universe_id="baseline",
            resources={},
            scheduler_config={
                "account": "m1234",
                "qos": "debug",
                "constraint": "gpu&hbm80g",
            },
        )
        assert "#SBATCH --qos=debug" in script
        assert "#SBATCH --constraint=gpu&hbm80g" in script

    def test_default_walltime_in_sbatch_script(self, tmp_path):
        """generate_sbatch_script passes resource_limits through to directives."""
        script = generate_sbatch_script(
            command="python train.py",
            container=None,
            container_runtime="podman-hpc",
            project_root=tmp_path,
            output_id="test",
            universe_id="baseline",
            resources={"cpus": 4},
            scheduler_config={"account": "m1234"},
            resource_limits={"max_walltime_minutes": 360},
        )
        assert "#SBATCH --time=06:00:00" in script

    def test_sbatch_script_default_walltime_30(self, tmp_path):
        """generate_sbatch_script uses 30-minute fallback when
        resource_limits has no max_walltime_minutes."""
        script = generate_sbatch_script(
            command="python train.py",
            container=None,
            container_runtime="podman-hpc",
            project_root=tmp_path,
            output_id="test",
            universe_id="baseline",
            resources={},
            scheduler_config={"account": "m1234"},
            resource_limits={},
        )
        assert "#SBATCH --time=00:30:00" in script


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_parse_sbatch_job_id(self):
        assert _parse_sbatch_job_id("Submitted batch job 12345\n") == "12345"
        assert _parse_sbatch_job_id("Submitted batch job 99999999") == "99999999"
        assert _parse_sbatch_job_id("some random output") is None
        assert _parse_sbatch_job_id("") is None

    def test_shell_quote(self):
        # Uses shlex.quote — simple strings need no quoting
        assert _shell_quote("hello") == "hello"
        # Strings with special chars get single-quoted
        assert "'" in _shell_quote("it's") or "\\" in _shell_quote("it's")
        assert _shell_quote("echo hi there") == "'echo hi there'"


# ---------------------------------------------------------------------------
# SLURM runner integration (mocked)
# ---------------------------------------------------------------------------


class TestSlurmRunner:
    def test_slurm_submit_sbatch_not_found(self, tmp_path):
        """When sbatch is not found, return exit code 127."""
        runner = ASTRAContainerRunner(
            project_root=str(tmp_path),
            backend="slurm",
            target_config={
                "scheduler": {"container_runtime": "podman-hpc", "account": "m1234"},
            },
        )
        result = runner.execute(
            command="python train.py",
            output_id="model",
            universe_id="baseline",
        )
        assert result.exit_code == 127
        assert "sbatch" in result.metadata.get("stderr", "")

    @patch("prism.dagster.runner.subprocess.run")
    @patch("prism.dagster.runner._poll_slurm_job")
    def test_slurm_submit_and_poll(self, mock_poll, mock_run, tmp_path):
        """Test the full submit + poll flow with mocked subprocess."""
        # Mock sbatch submission
        mock_submit = MagicMock()
        mock_submit.returncode = 0
        mock_submit.stdout = "Submitted batch job 12345\n"
        mock_submit.stderr = ""
        mock_run.return_value = mock_submit

        # Mock successful poll
        mock_poll.return_value = (0, {"slurm_state": "COMPLETED", "elapsed": "00:05:00"})

        runner = ASTRAContainerRunner(
            project_root=str(tmp_path),
            backend="slurm",
            target_config={
                "scheduler": {
                    "container_runtime": "podman-hpc",
                    "account": "m1234",
                },
            },
        )
        result = runner.execute(
            command="python train.py",
            output_id="model",
            universe_id="baseline",
            container="ghcr.io/proj/ml:latest",
        )

        assert result.exit_code == 0
        assert result.metadata["backend"] == "slurm"
        assert result.metadata["slurm_job_id"] == "12345"
        assert result.metadata["container_runtime"] == "podman-hpc"
        assert result.metadata["slurm_state"] == "COMPLETED"

    @patch("prism.dagster.runner.subprocess.run")
    def test_slurm_submit_failure(self, mock_run, tmp_path):
        """sbatch returns non-zero exit code."""
        mock_submit = MagicMock()
        mock_submit.returncode = 1
        mock_submit.stdout = ""
        mock_submit.stderr = "sbatch: error: invalid account"
        mock_run.return_value = mock_submit

        runner = ASTRAContainerRunner(
            project_root=str(tmp_path),
            backend="slurm",
            target_config={
                "scheduler": {"container_runtime": "podman-hpc", "account": "m1234"},
            },
        )
        result = runner.execute(
            command="python train.py",
            output_id="model",
            universe_id="baseline",
        )
        assert result.exit_code == 1
        assert "invalid account" in result.metadata["stderr"]

    @patch("prism.dagster.runner.subprocess.run")
    def test_slurm_creates_script_file(self, mock_run, tmp_path):
        """Verify that sbatch script is written to results/.slurm/."""
        mock_submit = MagicMock()
        mock_submit.returncode = 1
        mock_submit.stdout = ""
        mock_submit.stderr = "error"
        mock_run.return_value = mock_submit

        runner = ASTRAContainerRunner(
            project_root=str(tmp_path),
            backend="slurm",
            target_config={
                "scheduler": {
                    "container_runtime": "podman-hpc",
                    "account": "m1234",
                },
            },
        )
        runner.execute(
            command="python train.py",
            output_id="model",
            universe_id="baseline",
            container="img:1.0",
        )

        script_path = tmp_path / "results" / ".slurm" / "model_baseline.sh"
        assert script_path.exists()
        content = script_path.read_text()
        assert "#!/bin/bash" in content
        assert "podman-hpc run" in content

    @patch("prism.dagster.runner.subprocess.run")
    def test_slurm_forwards_universe_and_params(self, mock_run, tmp_path):
        """Universe ID and decision params must appear in the sbatch script command."""
        mock_submit = MagicMock()
        mock_submit.returncode = 1  # fail fast; we only care about the script
        mock_submit.stdout = ""
        mock_submit.stderr = "error"
        mock_run.return_value = mock_submit

        runner = ASTRAContainerRunner(
            project_root=str(tmp_path),
            backend="slurm",
            target_config={
                "scheduler": {"container_runtime": "podman-hpc", "account": "m1234"},
            },
        )
        runner.execute(
            command="python scripts/train.py",
            output_id="model",
            universe_id="experiment1",
            container="img:1.0",
            params={"method": "npe", "lr": "0.001"},
        )

        script_path = tmp_path / "results" / ".slurm" / "model_experiment1.sh"
        content = script_path.read_text()
        assert "--universe experiment1" in content
        assert "--method npe" in content
        assert "--lr 0.001" in content


# ---------------------------------------------------------------------------
# sacct exit-code correctness
# ---------------------------------------------------------------------------


class TestCheckSacct:
    def test_completed_returns_zero(self):
        stdout = "12345|COMPLETED|0:0|00:05:00|nid001\n"
        with patch("prism.dagster.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=stdout, stderr="")
            exit_code, meta = _check_sacct("12345")
        assert exit_code == 0
        assert meta["slurm_state"] == "COMPLETED"

    def test_cancelled_returns_nonzero(self):
        """CANCELLED jobs report 0:0 in sacct but should be treated as failure."""
        stdout = "12345|CANCELLED|0:0|00:01:00|nid001\n"
        with patch("prism.dagster.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=stdout, stderr="")
            exit_code, meta = _check_sacct("12345")
        assert exit_code != 0
        assert meta["slurm_state"] == "CANCELLED"

    def test_timeout_returns_nonzero(self):
        stdout = "12345|TIMEOUT|0:0|04:00:00|nid001\n"
        with patch("prism.dagster.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=stdout, stderr="")
            exit_code, meta = _check_sacct("12345")
        assert exit_code != 0

    def test_failed_returns_nonzero(self):
        stdout = "12345|FAILED|1:0|00:02:00|nid001\n"
        with patch("prism.dagster.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=stdout, stderr="")
            exit_code, meta = _check_sacct("12345")
        assert exit_code != 0

    def test_running_returns_none(self):
        stdout = "12345|RUNNING|0:0|00:01:00|nid001\n"
        with patch("prism.dagster.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=stdout, stderr="")
            exit_code, meta = _check_sacct("12345")
        assert exit_code is None
        assert meta == {}


# ---------------------------------------------------------------------------
# External inputs
# ---------------------------------------------------------------------------


class TestExternalInputs:
    def test_podman_hpc_with_external_inputs(self, tmp_path):
        """External inputs produce read-only volume mounts in podman-hpc command."""
        cmd = _podman_hpc_run_command(
            command="python scripts/analyze.py",
            container="ghcr.io/proj/ml:latest",
            project_root=tmp_path,
            resources={},
            scheduler_config={"account": "m1234"},
            external_inputs={
                "sim_data": "/pscratch/sd/f/francois/sim_42",
                "obs_data": "/global/cfs/cdirs/m1234/obs",
            },
        )
        assert "-v /global/cfs/cdirs/m1234/obs:/workspace/data/obs_data:ro" in cmd
        assert "-v /pscratch/sd/f/francois/sim_42:/workspace/data/sim_data:ro" in cmd
        # Volume mounts should come before the container image
        img_pos = cmd.index("ghcr.io/proj/ml:latest")
        assert cmd.index("/workspace/data/obs_data:ro") < img_pos
        assert cmd.index("/workspace/data/sim_data:ro") < img_pos

    def test_podman_hpc_no_external_inputs(self, tmp_path):
        """No external inputs means no extra volume mounts."""
        cmd = _podman_hpc_run_command(
            command="python scripts/analyze.py",
            container="ghcr.io/proj/ml:latest",
            project_root=tmp_path,
            resources={},
            scheduler_config={},
        )
        assert "/workspace/data/" not in cmd

    def test_sbatch_external_inputs_with_container(self, tmp_path):
        """generate_sbatch_script forwards external_inputs to podman-hpc."""
        script = generate_sbatch_script(
            command="python scripts/analyze.py",
            container="ghcr.io/proj/ml:latest",
            container_runtime="podman-hpc",
            project_root=tmp_path,
            output_id="result",
            universe_id="baseline",
            resources={},
            scheduler_config={"account": "m1234"},
            external_inputs={"sim_data": "/pscratch/sim"},
        )
        assert "-v /pscratch/sim:/workspace/data/sim_data:ro" in script

    def test_sbatch_external_inputs_no_container(self, tmp_path):
        """Without container, external inputs create symlinks."""
        script = generate_sbatch_script(
            command="python scripts/analyze.py",
            container=None,
            container_runtime="podman-hpc",
            project_root=tmp_path,
            output_id="result",
            universe_id="baseline",
            resources={},
            scheduler_config={"account": "m1234"},
            external_inputs={
                "sim_data": "/pscratch/sim",
                "obs_data": "/global/cfs/obs",
            },
        )
        assert "mkdir -p data" in script
        assert "ln -sfn /global/cfs/obs data/obs_data" in script
        assert "ln -sfn /pscratch/sim data/sim_data" in script
        # No podman-hpc in the script
        assert "podman-hpc" not in script

    def test_sbatch_no_external_inputs_no_symlinks(self, tmp_path):
        """Without external inputs, no symlink section appears."""
        script = generate_sbatch_script(
            command="python scripts/analyze.py",
            container=None,
            container_runtime="podman-hpc",
            project_root=tmp_path,
            output_id="result",
            universe_id="baseline",
            resources={},
        )
        assert "mkdir -p data" not in script
        assert "ln -sfn" not in script
