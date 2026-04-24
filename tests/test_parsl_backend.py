"""Tests for the Parsl backend (pure functions, no Parsl runtime)."""
from __future__ import annotations

import pytest

from lightcone.engine.parsl_backend import (
    MissingWorkQueueError,
    PilotRoutingError,
    build_parsl_config,
    pick_executor,
    recipe_resources_to_parsl,
)
from lightcone.engine.slurm_info import ClusterInfo, QoSInfo


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


def _have_workqueue() -> bool:
    try:
        import work_queue  # noqa: F401
        from parsl.executors import WorkQueueExecutor  # noqa: F401

        return True
    except ImportError:
        return False


class TestBuildParslConfig:
    """build_parsl_config returns a parsl.Config with one executor per pilot.

    These tests inspect the returned object's attributes rather than
    actually loading the DFK — that's covered by the integration tests.
    """

    def test_missing_pilots_key_raises(self):
        with pytest.raises(ValueError, match="pilots"):
            build_parsl_config({"backend": "slurm"})

    def test_empty_pilots_raises(self):
        with pytest.raises(ValueError, match="pilots"):
            build_parsl_config({"backend": "slurm", "pilots": {}})

    @pytest.mark.skipif(
        not _have_workqueue(),
        reason="ndcctools (WorkQueue) not installed — required for SLURM backend",
    )
    def test_single_cpu_pilot(self):
        target = {
            "backend": "slurm",
            "pilots": {
                "cpu": {
                    "nodes": 4,
                    "walltime": "2h",
                    "qos": "debug",
                    "account": "m1234",
                },
            },
        }
        config = build_parsl_config(target)
        assert len(config.executors) == 1
        ex = config.executors[0]
        assert ex.label == "cpu"
        # Provider sanity
        provider = ex.provider
        assert provider.nodes_per_block == 4
        assert provider.walltime == "02:00:00"
        assert provider.qos == "debug"
        assert provider.account == "m1234"
        assert provider.init_blocks == 1
        assert provider.min_blocks == 1
        assert provider.max_blocks == 1

    @pytest.mark.skipif(
        not _have_workqueue(),
        reason="ndcctools (WorkQueue) not installed",
    )
    def test_cpu_and_gpu_pilots(self):
        target = {
            "backend": "slurm",
            "pilots": {
                "cpu": {"nodes": 4, "walltime": "2h", "account": "m1234"},
                "gpu": {
                    "nodes": 2,
                    "walltime": "1h",
                    "account": "m1234_g",
                    "constraint": "gpu",
                },
            },
        }
        config = build_parsl_config(target)
        labels = {ex.label for ex in config.executors}
        assert labels == {"cpu", "gpu"}
        gpu_ex = next(ex for ex in config.executors if ex.label == "gpu")
        assert gpu_ex.provider.constraint == "gpu"
        assert gpu_ex.provider.account == "m1234_g"

    @pytest.mark.skipif(
        not _have_workqueue(),
        reason="ndcctools (WorkQueue) not installed",
    )
    def test_worker_init_passed_through(self):
        target = {
            "backend": "slurm",
            "pilots": {
                "cpu": {
                    "nodes": 1,
                    "walltime": "30m",
                    "account": "m1234",
                    "worker_init": "module load python\nsource /env/bin/activate",
                },
            },
        }
        config = build_parsl_config(target)
        assert "module load python" in config.executors[0].provider.worker_init

    @pytest.mark.skipif(
        not _have_workqueue(),
        reason="ndcctools (WorkQueue) not installed",
    )
    def test_run_dir_under_results(self, tmp_path):
        target = {
            "backend": "slurm",
            "pilots": {"cpu": {"nodes": 1, "walltime": "30m", "account": "m1"}},
        }
        config = build_parsl_config(target, project_root=tmp_path)
        assert str(tmp_path / "results" / ".parsl") in config.run_dir

    def test_workqueue_missing_raises_clear_error(self, monkeypatch):
        """When ndcctools isn't installed, raise a clear actionable error."""
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name in ("parsl.config", "parsl.executors.workqueue.executor"):
                raise ImportError("No module named 'work_queue'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        target = {
            "backend": "slurm",
            "pilots": {"cpu": {"nodes": 1, "walltime": "30m", "account": "m1"}},
        }
        with pytest.raises(MissingWorkQueueError, match="ndcctools"):
            build_parsl_config(target)


class TestValidatePilotsAgainstQos:
    def _cluster_with_debug_max_8_nodes_30min(self):
        return ClusterInfo(
            qos={
                "gpu_debug": QoSInfo(
                    "gpu_debug", max_wall_minutes=30, max_nodes=8, priority=1
                ),
                "gpu_regular": QoSInfo(
                    "gpu_regular", max_wall_minutes=2880, priority=1
                ),
            },
            user_qos=["gpu_debug", "gpu_regular"],
            user_accounts=["m4031"],
            partitions={},
            timestamp="2026-04-24T00:00:00",
        )

    def test_pilot_within_limits_passes(self, monkeypatch):
        from lightcone.engine.parsl_backend import validate_pilots_against_qos

        cluster = self._cluster_with_debug_max_8_nodes_30min()
        monkeypatch.setattr(
            "lightcone.engine.targets.load_cluster_cache", lambda n: cluster,
        )
        monkeypatch.setattr(
            "lightcone.engine.targets.is_cache_stale", lambda n: False,
        )

        validate_pilots_against_qos(
            pilots={"gpu": {"nodes": 4, "walltime": "20m", "qos": "gpu_debug"}},
            target_name="perlmutter",
        )

    def test_pilot_exceeds_max_nodes_raises(self, monkeypatch):
        from lightcone.engine.parsl_backend import (
            PilotConfigError,
            validate_pilots_against_qos,
        )
        cluster = self._cluster_with_debug_max_8_nodes_30min()
        monkeypatch.setattr(
            "lightcone.engine.targets.load_cluster_cache", lambda n: cluster,
        )
        monkeypatch.setattr(
            "lightcone.engine.targets.is_cache_stale", lambda n: False,
        )

        with pytest.raises(PilotConfigError, match="nodes"):
            validate_pilots_against_qos(
                pilots={
                    "gpu": {"nodes": 16, "walltime": "20m", "qos": "gpu_debug"},
                },
                target_name="perlmutter",
            )

    def test_pilot_exceeds_walltime_raises(self, monkeypatch):
        from lightcone.engine.parsl_backend import (
            PilotConfigError,
            validate_pilots_against_qos,
        )
        cluster = self._cluster_with_debug_max_8_nodes_30min()
        monkeypatch.setattr(
            "lightcone.engine.targets.load_cluster_cache", lambda n: cluster,
        )
        monkeypatch.setattr(
            "lightcone.engine.targets.is_cache_stale", lambda n: False,
        )

        with pytest.raises(PilotConfigError, match="wall"):
            validate_pilots_against_qos(
                pilots={
                    "gpu": {"nodes": 4, "walltime": "2h", "qos": "gpu_debug"},
                },
                target_name="perlmutter",
            )

    def test_no_cluster_cache_warns_but_passes(self, monkeypatch, caplog):
        from lightcone.engine.parsl_backend import validate_pilots_against_qos

        monkeypatch.setattr(
            "lightcone.engine.targets.load_cluster_cache", lambda n: None,
        )
        monkeypatch.setattr(
            "lightcone.engine.targets.is_cache_stale", lambda n: False,
        )
        # No cache — best-effort, don't block lc run; log a warning.
        validate_pilots_against_qos(
            pilots={"cpu": {"nodes": 4, "walltime": "2h", "qos": "debug"}},
            target_name="perlmutter",
        )
        assert any("cluster cache" in r.message.lower() for r in caplog.records)

    def test_pilot_without_qos_skipped(self, monkeypatch):
        # No QoS declared in pilot → nothing to check
        from lightcone.engine.parsl_backend import validate_pilots_against_qos

        cluster = self._cluster_with_debug_max_8_nodes_30min()
        monkeypatch.setattr(
            "lightcone.engine.targets.load_cluster_cache", lambda n: cluster,
        )
        monkeypatch.setattr(
            "lightcone.engine.targets.is_cache_stale", lambda n: False,
        )
        validate_pilots_against_qos(
            pilots={"cpu": {"nodes": 100, "walltime": "100h"}},
            target_name="perlmutter",
        )
