"""Unit tests for lightcone.engine.compute_adapter (ADR-0001 §5.1).

These tests do NOT require a running SLURM cluster or Docker.
Tests that require dagster-slurm to be installed are marked with
``needs_dagster_slurm`` and skipped automatically when the library is absent.
"""
from __future__ import annotations

import py_compile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from lightcone.engine.compute_adapter import (
    has_dagster_slurm,
    prepare_payload,
)
from lightcone.engine.slurm_utils import normalise_time_limit, resources_to_slurm_opts

needs_dagster_slurm = pytest.mark.skipif(
    not has_dagster_slurm(),
    reason="dagster-slurm not installed (uv sync --group slurm-next)",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _local_target() -> dict[str, Any]:
    return {"mode": "local"}


def _slurm_target() -> dict[str, Any]:
    return {
        "mode": "slurm",
        "ssh": {"host": "hpc.example.com", "user": "researcher", "key_path": "~/.ssh/id_rsa"},
        "queue": {"partition": "gpu", "account": "proj123", "qos": "debug"},
        "remote_base": "/scratch/researcher/lightcone-runs",
        "poll": {"timeout_seconds": 7200},
    }


# ---------------------------------------------------------------------------
# §5.1 test_target_yaml_to_compute_resource
# ---------------------------------------------------------------------------


@needs_dagster_slurm
class TestBuildComputeResource:
    def test_local_mode_constructs_without_error(self):
        from lightcone.engine.compute_adapter import build_compute_resource

        cr = build_compute_resource(_local_target())
        assert cr is not None

    def test_local_mode_is_correct_execution_mode(self):
        from dagster_slurm.resources.compute import ExecutionMode

        from lightcone.engine.compute_adapter import build_compute_resource

        cr = build_compute_resource(_local_target())
        assert cr.mode == ExecutionMode.LOCAL

    def test_slurm_mode_constructs_without_error(self):
        from lightcone.engine.compute_adapter import build_compute_resource

        cr = build_compute_resource(_slurm_target())
        assert cr is not None

    def test_slurm_mode_ssh_host(self):
        from lightcone.engine.compute_adapter import build_compute_resource

        cr = build_compute_resource(_slurm_target())
        assert cr.slurm.ssh.host == "hpc.example.com"

    def test_slurm_mode_queue_account(self):
        from lightcone.engine.compute_adapter import build_compute_resource

        cr = build_compute_resource(_slurm_target())
        assert cr.slurm.queue.account == "proj123"

    def test_docker_mode_raises(self):
        from lightcone.engine.compute_adapter import build_compute_resource

        with pytest.raises(ValueError, match="Docker targets"):
            build_compute_resource({"mode": "docker"})

    def test_compute_resource_is_constructed_lazily(self):
        """Constructing a ComputeResource must not open an SSH connection."""
        from lightcone.engine.compute_adapter import build_compute_resource

        # This would raise if a real connection attempt were made.
        cr = build_compute_resource(_slurm_target())
        assert cr is not None  # no exception = connection not opened


# ---------------------------------------------------------------------------
# §5.1 test_resources_dict_to_slurm_opts
# ---------------------------------------------------------------------------


class TestResourcestoSlurmOpts:
    def test_full_mapping(self):
        opts = resources_to_slurm_opts(
            {"cpus": 4, "memory": "8G", "gpus": 2, "nodes": 1, "time_limit": "30m"}
        )
        assert opts == {
            "cpus_per_task": 4,
            "mem": "8G",
            "gpus_per_node": 2,
            "nodes": 1,
            "time_limit": "00:30:00",
        }

    def test_empty_returns_empty(self):
        assert resources_to_slurm_opts(None) == {}
        assert resources_to_slurm_opts({}) == {}

    def test_partial_mapping(self):
        opts = resources_to_slurm_opts({"cpus": 8})
        assert opts == {"cpus_per_task": 8}
        assert "mem" not in opts
        assert "time_limit" not in opts

    def test_time_limit_hours(self):
        opts = resources_to_slurm_opts({"time_limit": "1h"})
        assert opts["time_limit"] == "01:00:00"

    def test_time_limit_colon_format(self):
        opts = resources_to_slurm_opts({"time_limit": "2:00:00"})
        assert opts["time_limit"] == "2:00:00"

    def test_time_limit_bare_int_minutes(self):
        opts = resources_to_slurm_opts({"time_limit": "120"})
        assert opts["time_limit"] == "02:00:00"

    def test_unknown_keys_ignored(self):
        opts = resources_to_slurm_opts({"cpus": 2, "unknown_key": "ignored"})
        assert "unknown_key" not in opts


class TestNormaliseTimeLimit:
    def test_hours(self):
        assert normalise_time_limit("2h") == "02:00:00"

    def test_minutes(self):
        assert normalise_time_limit("90m") == "01:30:00"

    def test_bare_int_minutes(self):
        assert normalise_time_limit(120) == "02:00:00"

    def test_bare_string_minutes(self):
        assert normalise_time_limit("45") == "00:45:00"

    def test_passthrough_hh_mm_ss(self):
        assert normalise_time_limit("03:30:00") == "03:30:00"


# ---------------------------------------------------------------------------
# §5.1 test_payload_wrapper_generation
# ---------------------------------------------------------------------------


class TestPayloadWrapperGeneration:
    def test_generated_file_compiles(self, tmp_path: Path):
        payload_path, ctx = prepare_payload(
            command="python scripts/compute.py",
            universe_id="baseline",
            output_id="photoz",
            project_root=tmp_path,
            params={"method": "bpz", "zmax": 3.0},
        )
        py_compile.compile(str(payload_path), doraise=True)

    def test_cli_args_present_in_payload(self, tmp_path: Path):
        payload_path, _ = prepare_payload(
            command="python scripts/compute.py",
            universe_id="baseline",
            output_id="photoz",
            project_root=tmp_path,
            params={"method": "bpz"},
        )
        source = payload_path.read_text()
        assert '"--method"' in source
        assert '"bpz"' in source

    def test_command_present_in_payload(self, tmp_path: Path):
        payload_path, _ = prepare_payload(
            command="python scripts/compute.py",
            universe_id="baseline",
            output_id="photoz",
            project_root=tmp_path,
        )
        source = payload_path.read_text()
        assert "python scripts/compute.py" in source

    def test_container_wrap_present_when_set(self, tmp_path: Path):
        payload_path, _ = prepare_payload(
            command="python scripts/compute.py",
            universe_id="baseline",
            output_id="photoz",
            project_root=tmp_path,
            container_wrap=["podman-hpc", "run", "--rm", "--gpu"],
        )
        source = payload_path.read_text()
        assert "podman-hpc" in source
        assert "CONTAINER_WRAP" in source

    def test_no_container_wrap_is_none(self, tmp_path: Path):
        payload_path, _ = prepare_payload(
            command="python scripts/compute.py",
            universe_id="baseline",
            output_id="photoz",
            project_root=tmp_path,
        )
        source = payload_path.read_text()
        assert "CONTAINER_WRAP = null" in source

    def test_pipes_materialization_reported(self, tmp_path: Path):
        payload_path, _ = prepare_payload(
            command="python scripts/compute.py",
            universe_id="baseline",
            output_id="photoz",
            project_root=tmp_path,
        )
        source = payload_path.read_text()
        assert "report_asset_materialization" in source

    def test_payload_path_layout(self, tmp_path: Path):
        _, ctx = prepare_payload(
            command="python scripts/compute.py",
            universe_id="u1",
            output_id="out1",
            project_root=tmp_path,
        )
        expected = tmp_path / "results" / ".payloads" / "u1__out1.py"
        assert ctx.payload_path == expected
        assert expected.exists()

    def test_payload_ctx_fields(self, tmp_path: Path):
        _, ctx = prepare_payload(
            command="cmd",
            universe_id="u1",
            output_id="o1",
            project_root=tmp_path,
        )
        assert ctx.universe_id == "u1"
        assert ctx.output_id == "o1"
        assert ctx.project_root == tmp_path

    def test_bool_true_param_becomes_flag(self, tmp_path: Path):
        payload_path, _ = prepare_payload(
            command="cmd",
            universe_id="baseline",
            output_id="out",
            project_root=tmp_path,
            params={"verbose": True},
        )
        source = payload_path.read_text()
        assert '"--verbose"' in source
        # True flag has no value pair, so False should NOT appear for this param
        assert '"True"' not in source

    def test_bool_false_param_omitted(self, tmp_path: Path):
        payload_path, _ = prepare_payload(
            command="cmd",
            universe_id="baseline",
            output_id="out",
            project_root=tmp_path,
            params={"dry_run": False},
        )
        source = payload_path.read_text()
        assert "--dry-run" not in source

    def test_payload_override_copies_file(self, tmp_path: Path):
        custom = tmp_path / "custom_payload.py"
        custom.write_text("# custom payload\nprint('hello')\n")

        payload_path, _ = prepare_payload(
            command="cmd",
            universe_id="baseline",
            output_id="out",
            project_root=tmp_path,
            payload_override=custom,
        )
        assert payload_path.read_text() == custom.read_text()

    def test_external_inputs_encoded_in_payload(self, tmp_path: Path):
        payload_path, _ = prepare_payload(
            command="python run.py",
            universe_id="baseline",
            output_id="out",
            project_root=tmp_path,
            external_inputs={"raw_catalog": "/data/raw.parquet"},
        )
        source = payload_path.read_text()
        assert "raw_catalog" in source
        assert "/data/raw.parquet" in source


# ---------------------------------------------------------------------------
# §5.1 test_extra_sbatch_directives_injection
# ---------------------------------------------------------------------------


class TestExtraSbatchDirectivesInjection:
    def test_constraint_appears_in_sbatch_block(self, tmp_path: Path):
        payload_path, _ = prepare_payload(
            command="python run.py",
            universe_id="baseline",
            output_id="out",
            project_root=tmp_path,
            extra_sbatch_directives=["--constraint=gpu"],
        )
        source = payload_path.read_text()
        assert "#SBATCH --constraint=gpu" in source

    def test_multiple_directives(self, tmp_path: Path):
        payload_path, _ = prepare_payload(
            command="python run.py",
            universe_id="baseline",
            output_id="out",
            project_root=tmp_path,
            extra_sbatch_directives=["--constraint=gpu", "--reservation=maint"],
        )
        source = payload_path.read_text()
        assert "#SBATCH --constraint=gpu" in source
        assert "#SBATCH --reservation=maint" in source

    def test_no_directives_no_sbatch_lines(self, tmp_path: Path):
        payload_path, _ = prepare_payload(
            command="python run.py",
            universe_id="baseline",
            output_id="out",
            project_root=tmp_path,
        )
        source = payload_path.read_text()
        assert "#SBATCH" not in source

    def test_file_parses_as_valid_python_with_directives(self, tmp_path: Path):
        payload_path, _ = prepare_payload(
            command="python run.py",
            universe_id="baseline",
            output_id="out",
            project_root=tmp_path,
            extra_sbatch_directives=["--constraint=gpu"],
        )
        py_compile.compile(str(payload_path), doraise=True)


# ---------------------------------------------------------------------------
# §5.1 test_stage_artifacts — local mode is a no-op
# ---------------------------------------------------------------------------


class TestStageArtifacts:
    def test_local_mode_noop(self, tmp_path: Path):
        from lightcone.engine.compute_adapter import stage_artifacts

        _, ctx = prepare_payload(
            command="cmd",
            universe_id="u1",
            output_id="o1",
            project_root=tmp_path,
        )
        # completed with no ssh_pool metadata → should return without error
        mock_completed = MagicMock()
        mock_completed.metadata = {}
        stage_artifacts(mock_completed, ctx)  # must not raise
