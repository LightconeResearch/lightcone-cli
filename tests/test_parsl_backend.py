"""Tests for the Parsl backend (pure functions, no Parsl runtime)."""
from __future__ import annotations

import pytest

from lightcone.engine.parsl_backend import (
    PilotRoutingError,
    pick_executor,
    recipe_resources_to_parsl,
)


class TestRecipeResourcesToParsl:
    def test_empty_resources(self):
        assert recipe_resources_to_parsl({}) == {}

    def test_cpus(self):
        assert recipe_resources_to_parsl({"cpus": 4}) == {"cores": 4}

    def test_memory_gb(self):
        # WorkQueue expects memory in MB
        assert recipe_resources_to_parsl({"memory": "16GB"}) == {"memory": 16384}

    def test_memory_mb(self):
        assert recipe_resources_to_parsl({"memory": "512MB"}) == {"memory": 512}

    def test_memory_lower_case(self):
        assert recipe_resources_to_parsl({"memory": "8gb"}) == {"memory": 8192}

    def test_gpus(self):
        assert recipe_resources_to_parsl({"gpus": 2}) == {"gpus": 2}

    def test_time_limit_hours(self):
        # WorkQueue's wall_time is seconds
        assert recipe_resources_to_parsl({"time_limit": "2h"}) == {
            "wall_time": 7200,
        }

    def test_time_limit_minutes(self):
        assert recipe_resources_to_parsl({"time_limit": "30m"}) == {
            "wall_time": 1800,
        }

    def test_time_limit_int_minutes(self):
        assert recipe_resources_to_parsl({"time_limit": 90}) == {
            "wall_time": 5400,
        }

    def test_time_limit_hhmmss(self):
        assert recipe_resources_to_parsl({"time_limit": "01:30:00"}) == {
            "wall_time": 5400,
        }

    def test_full(self):
        spec = recipe_resources_to_parsl(
            {"cpus": 8, "memory": "32GB", "gpus": 1, "time_limit": "1h"}
        )
        assert spec == {
            "cores": 8,
            "memory": 32768,
            "gpus": 1,
            "wall_time": 3600,
        }

    def test_unknown_keys_ignored(self):
        # nodes is a pilot-level concept, not per-task
        assert recipe_resources_to_parsl({"nodes": 4, "cpus": 2}) == {"cores": 2}


class TestPickExecutor:
    def test_cpu_only_routes_to_cpu(self):
        pilots = {"cpu": {"nodes": 4, "walltime": "2h"}}
        assert pick_executor({"cpus": 4}, pilots) == "cpu"

    def test_gpu_resource_routes_to_gpu_when_available(self):
        pilots = {
            "cpu": {"nodes": 4, "walltime": "2h"},
            "gpu": {"nodes": 1, "walltime": "1h"},
        }
        assert pick_executor({"gpus": 1}, pilots) == "gpu"

    def test_gpu_resource_falls_back_to_cpu_when_no_gpu_pilot(self):
        # Without a GPU pilot, GPU recipes raise — better to fail fast than
        # silently dispatch to a CPU pilot that won't have GPU resources.
        pilots = {"cpu": {"nodes": 4, "walltime": "2h"}}
        with pytest.raises(PilotRoutingError, match="gpu"):
            pick_executor({"gpus": 1}, pilots)

    def test_multi_node_routes_to_mpi_when_available(self):
        pilots = {
            "cpu": {"nodes": 4, "walltime": "2h"},
            "mpi": {"nodes": 8, "walltime": "4h"},
        }
        assert pick_executor({"nodes": 4}, pilots) == "mpi"

    def test_multi_node_falls_back_to_cpu_when_no_mpi_pilot(self):
        pilots = {"cpu": {"nodes": 4, "walltime": "2h"}}
        # No MPI pilot — falls through to cpu; user is responsible for
        # whether that's actually viable.
        assert pick_executor({"nodes": 2}, pilots) == "cpu"

    def test_no_resources_routes_to_cpu(self):
        pilots = {"cpu": {"nodes": 4, "walltime": "2h"}}
        assert pick_executor({}, pilots) == "cpu"

    def test_no_pilots_raises(self):
        with pytest.raises(PilotRoutingError, match="no pilots"):
            pick_executor({"cpus": 4}, {})

    def test_no_cpu_pilot_for_cpu_recipe_raises(self):
        # Edge case: only a gpu pilot exists, recipe asks for nothing
        pilots = {"gpu": {"nodes": 1, "walltime": "1h"}}
        with pytest.raises(PilotRoutingError):
            pick_executor({"cpus": 2}, pilots)
