"""Tests for ``lightcone.engine.clusters`` — config CRUD, sbatch rendering, QoS preflight."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from lightcone.engine.clusters import (
    ClusterSpec,
    WorkerPool,
    list_clusters,
    load_cluster_config,
    parse_walltime_seconds,
    read_scheduler_address,
    resolve_cluster,
    save_cluster_config,
    spec_from_config,
    walltime_to_slurm,
)
from lightcone.engine.clusters._slurm import (
    ensure_worker_env,
    parse_job_id,
    render_sbatch,
)

# ---------------------------------------------------------------------------
# Walltime helpers
# ---------------------------------------------------------------------------


class TestWalltime:
    def test_h_form(self):
        assert parse_walltime_seconds("24h") == 86400

    def test_m_form(self):
        assert parse_walltime_seconds("30m") == 1800

    def test_hms_form(self):
        assert parse_walltime_seconds("01:30:00") == 5400

    def test_int_minutes(self):
        assert parse_walltime_seconds(45) == 2700

    def test_to_slurm(self):
        assert walltime_to_slurm("24h") == "24:00:00"
        assert walltime_to_slurm("30m") == "00:30:00"
        assert walltime_to_slurm("01:30:00") == "01:30:00"


# ---------------------------------------------------------------------------
# Config CRUD
# ---------------------------------------------------------------------------


@pytest.fixture
def perlmutter_yaml() -> dict:
    return {
        "type": "slurm",
        "site": "perlmutter",
        "account": "m1234",
        "qos": "regular",
        "walltime": "24h",
        "workers": [
            {"nodes": 4, "threads_per_node": 64, "memory": "200GB"},
        ],
    }


class TestConfigCRUD:
    def test_save_and_load(self, perlmutter_yaml):
        save_cluster_config("perlmutter", perlmutter_yaml)
        loaded = load_cluster_config("perlmutter")
        assert loaded == perlmutter_yaml

    def test_load_missing_returns_none(self):
        assert load_cluster_config("nonexistent") is None

    def test_list_alphabetical(self, perlmutter_yaml):
        save_cluster_config("perlmutter", perlmutter_yaml)
        save_cluster_config("frontier", {**perlmutter_yaml, "site": "frontier"})
        assert list_clusters() == ["frontier", "perlmutter"]


class TestSpecFromConfig:
    def test_pulls_site_defaults(self, perlmutter_yaml):
        spec = spec_from_config("perlmutter", perlmutter_yaml)
        assert spec.site == "perlmutter"
        assert spec.account == "m1234"
        assert spec.qos == "regular"
        assert spec.scratch_root == "$PSCRATCH"          # from site_registry
        assert spec.container_runtime == "podman-hpc"    # from site_registry
        assert len(spec.workers) == 1
        assert spec.workers[0].nodes == 4

    def test_missing_type_raises(self):
        with pytest.raises(ValueError, match="missing required field 'type'"):
            spec_from_config("x", {"site": "perlmutter", "account": "m1"})

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="unknown type"):
            spec_from_config("x", {"type": "k8s", "site": "perlmutter", "account": "m1"})

    def test_missing_site_raises(self):
        with pytest.raises(ValueError, match="missing required field 'site'"):
            spec_from_config("x", {"type": "slurm", "account": "m1"})

    def test_missing_workers_raises(self):
        with pytest.raises(ValueError, match="at least one worker pool"):
            spec_from_config(
                "x", {"type": "slurm", "site": "perlmutter", "account": "m1"},
            )


# ---------------------------------------------------------------------------
# Cluster resolution
# ---------------------------------------------------------------------------


class TestResolveCluster:
    def test_explicit_cli_flag_wins(self, tmp_path, perlmutter_yaml):
        save_cluster_config("perlmutter", perlmutter_yaml)
        save_cluster_config("debug", {**perlmutter_yaml, "qos": "debug"})
        name, _ = resolve_cluster(tmp_path, cli_cluster="debug")
        assert name == "debug"

    def test_unknown_cli_flag_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            resolve_cluster(tmp_path, cli_cluster="ghost")

    def test_project_config(self, tmp_path, perlmutter_yaml):
        save_cluster_config("perlmutter", perlmutter_yaml)
        cfg_dir = tmp_path / ".lightcone"
        cfg_dir.mkdir()
        (cfg_dir / "lightcone.yaml").write_text("cluster: perlmutter\n")
        name, _ = resolve_cluster(tmp_path, cli_cluster=None)
        assert name == "perlmutter"

    def test_single_cluster_fallback(self, tmp_path, perlmutter_yaml):
        save_cluster_config("perlmutter", perlmutter_yaml)
        name, _ = resolve_cluster(tmp_path, cli_cluster=None)
        assert name == "perlmutter"

    def test_no_resolution_returns_none(self, tmp_path, perlmutter_yaml):
        save_cluster_config("a", perlmutter_yaml)
        save_cluster_config("b", perlmutter_yaml)
        assert resolve_cluster(tmp_path, cli_cluster=None) is None


# ---------------------------------------------------------------------------
# Sbatch rendering
# ---------------------------------------------------------------------------


def _spec(**overrides) -> ClusterSpec:
    base = {
        "name": "perlmutter",
        "type": "slurm",
        "site": "perlmutter",
        "account": "m1234",
        "qos": "regular",
        "walltime": "24h",
        "workers": [WorkerPool(nodes=4, threads_per_node=64, memory="200GB")],
        "container_runtime": "podman-hpc",
        "scratch_root": "$PSCRATCH",
    }
    base.update(overrides)
    return ClusterSpec(**base)


class TestSbatchRender:
    def test_single_pool_basic_directives(self):
        script = render_sbatch(_spec())
        assert "#SBATCH --job-name=lc-cluster-perlmutter" in script
        assert "#SBATCH --nodes=4" in script
        assert "#SBATCH --time=24:00:00" in script
        assert "#SBATCH --qos=regular" in script
        assert "#SBATCH --account=m1234" in script

    def test_runs_dask_scheduler_and_worker(self):
        script = render_sbatch(_spec())
        assert "dask scheduler" in script
        assert "dask worker" in script
        assert "$PSCRATCH/lightcone/clusters/perlmutter.json" in script
        assert 'wait "$SCHED_PID"' in script

    def test_default_worker_init_template(self):
        script = render_sbatch(_spec())
        assert "module load python" in script
        assert "source $HOME/.lightcone/envs/perlmutter/bin/activate" in script

    def test_user_overrides_worker_init(self):
        spec = _spec(worker_init="echo override\n")
        script = render_sbatch(spec)
        assert "echo override" in script
        assert "module load python" not in script

    def test_uniform_constraint_lifts_to_top_level(self):
        spec = _spec(workers=[
            WorkerPool(nodes=2, constraint="cpu"),
            WorkerPool(nodes=2, constraint="cpu"),
        ])
        script = render_sbatch(spec)
        assert "#SBATCH --constraint=cpu" in script
        # No per-srun --constraint when uniform.
        assert script.count("--constraint=") == 1

    def test_multipool_cpu_gpu_emits_two_srun(self):
        spec = _spec(workers=[
            WorkerPool(nodes=3, constraint="cpu", resources={}),
            WorkerPool(nodes=1, constraint="gpu", resources={"GPU": 4}),
        ])
        script = render_sbatch(spec)
        # Total nodes = sum of pools.
        assert "#SBATCH --nodes=4" in script
        # Two srun lines, each with its own constraint.
        assert script.count("srun") >= 2
        assert "--constraint=cpu" in script
        assert "--constraint=gpu" in script
        # GPU pool advertises Dask resources; CPU pool doesn't.
        assert '--resources "GPU=4"' in script

    def test_missing_account_raises(self):
        spec = _spec(account="")
        with pytest.raises(ValueError, match="missing 'account'"):
            render_sbatch(spec)


# ---------------------------------------------------------------------------
# Job-id parsing & scheduler-address parsing
# ---------------------------------------------------------------------------


class TestParsing:
    def testparse_job_id(self):
        assert parse_job_id("Submitted batch job 12345678\n") == "12345678"

    def testparse_job_id_no_match(self):
        assert parse_job_id("error: bogus output") is None

    def testread_scheduler_address(self, tmp_path):
        path = tmp_path / "sched.json"
        path.write_text(json.dumps({"address": "tcp://nid001234:8786"}))
        assert read_scheduler_address(str(path)) == "tcp://nid001234:8786"

    def testread_scheduler_address_missing_file(self, tmp_path):
        assert read_scheduler_address(str(tmp_path / "absent.json")) is None

    def testread_scheduler_address_corrupt(self, tmp_path):
        path = tmp_path / "sched.json"
        path.write_text("{not json")
        assert read_scheduler_address(str(path)) is None


# ---------------------------------------------------------------------------
# Worker env auto-bootstrap (mocks ``uv``)
# ---------------------------------------------------------------------------


class TestEnsureWorkerEnv:
    def test_idempotent_when_python_exists(self):
        spec = _spec()
        from lightcone.engine.clusters import env_path_for_site

        env = env_path_for_site(spec.site)
        (env / "bin").mkdir(parents=True)
        (env / "bin" / "python").write_text("# stub\n")
        with patch("subprocess.run") as run:
            ensure_worker_env(spec)
            run.assert_not_called()

    def test_provisions_when_missing(self):
        spec = _spec()
        with patch("subprocess.run") as run:
            ensure_worker_env(spec)
            assert run.call_count == 2
            assert run.call_args_list[0].args[0][0] == "uv"
            assert run.call_args_list[1].args[0][:3] == ["uv", "pip", "install"]


# ---------------------------------------------------------------------------
# QoS preflight (port of TestQoSEligibility from test_runner)
# ---------------------------------------------------------------------------


class TestQoSPreflight:
    def _populate_cache(self, site: str = "perlmutter") -> None:
        from lightcone.engine.clusters._slurm import save_slurm_cache as save_cluster_cache
        from lightcone.engine.clusters._slurm_info import ClusterInfo, QoSInfo

        info = ClusterInfo(
            qos={
                "debug": QoSInfo(name="debug", max_wall_minutes=30, max_nodes=8),
                "regular": QoSInfo(name="regular", max_wall_minutes=720, max_nodes=4096),
            },
            user_qos=["debug", "regular"],
            user_accounts=["m1234"],
            timestamp=datetime.now(UTC).isoformat(),
        )
        save_cluster_cache(site, info)

    def test_no_cache_skips_validation(self):
        from lightcone.engine.clusters._slurm import validate_against_qos

        spec = _spec(qos="debug", walltime="2h")
        # Should not raise; logs a warning.
        result = validate_against_qos(spec)
        assert result.qos == "debug"

    def test_fit_clamps_walltime(self):
        from lightcone.engine.clusters._slurm import validate_against_qos

        self._populate_cache()
        spec = _spec(qos="debug", walltime="2h")        # debug caps at 30 min
        result = validate_against_qos(spec, strategy="fit")
        # walltime should now fit within debug's 30-min cap.
        assert parse_walltime_seconds(result.walltime) <= 30 * 60

    def test_eligible_passthrough(self):
        from lightcone.engine.clusters._slurm import validate_against_qos

        self._populate_cache()
        spec = _spec(qos="regular", walltime="2h")
        result = validate_against_qos(spec)
        assert result.walltime == "2h"
