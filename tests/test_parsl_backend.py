"""Tests for the Parsl backend (pure functions, no Parsl runtime)."""
from __future__ import annotations

import pytest

from lightcone.engine.parsl_backend import (
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
