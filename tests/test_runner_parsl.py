"""Integration tests for the Parsl-backed SLURM runner.

These tests use ``WorkQueueExecutor + LocalProvider`` so they exercise
the real Parsl plumbing (bash_app, futures, BashExitFailure handling)
without needing a SLURM cluster.
"""
from __future__ import annotations

import pytest

from lightcone.engine.runner import ASTRAContainerRunner


@pytest.mark.usefixtures("parsl_local_pilot")
class TestRunSlurmViaLocalPilot:
    def test_trivial_command_succeeds(self, tmp_path):
        runner = ASTRAContainerRunner(
            project_root=str(tmp_path),
            backend="slurm",
            target_config={"pilots": {"cpu": {"nodes": 1, "walltime": "5m"}}},
        )
        result = runner.execute(
            command="echo hello",
            output_id="greeting",
            universe_id="baseline",
        )
        assert result.exit_code == 0
        assert result.metadata["backend"] == "slurm"
        assert result.metadata["executor"] == "cpu"
        assert "hello" in result.metadata.get("stdout", "")

    def test_failing_command_returns_nonzero(self, tmp_path):
        runner = ASTRAContainerRunner(
            project_root=str(tmp_path),
            backend="slurm",
            target_config={"pilots": {"cpu": {"nodes": 1, "walltime": "5m"}}},
        )
        result = runner.execute(
            command="exit 7",
            output_id="failing",
            universe_id="baseline",
        )
        assert result.exit_code == 7

    def test_universe_param_is_forwarded(self, tmp_path):
        runner = ASTRAContainerRunner(
            project_root=str(tmp_path),
            backend="slurm",
            target_config={"pilots": {"cpu": {"nodes": 1, "walltime": "5m"}}},
        )
        result = runner.execute(
            command="echo got=$1; shift; echo got=$1",
            output_id="check",
            universe_id="exp1",
            params={"method": "npe"},
        )
        assert result.exit_code == 0
        # _build_cli_args appends '--universe exp1 --method npe' to the command
        assert "exp1" in result.metadata.get("stdout", "")

    def test_pilot_routing_for_unconfigured_executor_raises(self, tmp_path):
        """Recipe asks for gpus but only the cpu pilot is loaded → PilotRoutingError."""
        from lightcone.engine.parsl_backend import PilotRoutingError

        runner = ASTRAContainerRunner(
            project_root=str(tmp_path),
            backend="slurm",
            # Same single-cpu pilot fixture loaded; no gpu pilot configured.
            target_config={"pilots": {"cpu": {"nodes": 1, "walltime": "5m"}}},
        )
        with pytest.raises(PilotRoutingError, match="gpu"):
            runner.execute(
                command="echo unreachable",
                output_id="gpu_recipe",
                universe_id="baseline",
                resources={"gpus": 1},
            )
