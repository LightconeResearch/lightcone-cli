"""Tests for ``lightcone.engine.clusters`` — config CRUD, sbatch rendering, QoS preflight."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from lightcone.engine.clusters import (
    ClusterRecord,
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
from lightcone.engine.clusters._common import (
    read_record,
    state_path,
    write_record,
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


# ---------------------------------------------------------------------------
# Attach (in-allocation) — `lc cluster attach`
# ---------------------------------------------------------------------------


def _record(**overrides) -> ClusterRecord:
    base = dict(
        name="x",
        type="slurm",
        job_id="42",
        site="perlmutter",
        submitted_at=datetime.now(UTC).isoformat(),
        walltime_seconds=3600,
        scheduler_file="/tmp/sched.json",
    )
    base.update(overrides)
    return ClusterRecord(**base)


class TestAttachToAllocation:
    def test_errors_outside_allocation(self, monkeypatch):
        from lightcone.engine.clusters._slurm import attach_to_allocation

        monkeypatch.delenv("SLURM_JOB_ID", raising=False)
        with pytest.raises(RuntimeError, match="Not inside a SLURM allocation"):
            attach_to_allocation()

    def test_writes_attached_state_and_does_not_sbatch(
        self, monkeypatch, tmp_path,
    ):
        from lightcone.engine.clusters._slurm import attach_to_allocation

        monkeypatch.setenv("SLURM_JOB_ID", "12345")
        monkeypatch.setenv("SLURM_JOB_NUM_NODES", "2")
        monkeypatch.setenv("SLURM_CPUS_ON_NODE", "64")

        # Avoid real subprocess + filesystem expansion.
        sched_proc = MagicMock(pid=111)
        worker_proc = MagicMock(pid=222)
        # First Popen = scheduler, second = worker srun.
        with patch(
            "lightcone.engine.clusters._slurm.subprocess.Popen",
            side_effect=[sched_proc, worker_proc],
        ) as popen, patch(
            "lightcone.engine.clusters._slurm.subprocess.run",
        ) as run, patch(
            "lightcone.engine.clusters._slurm.expand_path",
            side_effect=lambda v: str(tmp_path / "scratch"),
        ):
            info = attach_to_allocation(project_root=tmp_path)

        # No sbatch / scancel should have been called.
        for call in run.call_args_list:
            assert call.args[0][0] != "sbatch"

        # Two Popen calls — scheduler + worker srun.
        assert popen.call_count == 2
        sched_argv, worker_argv = popen.call_args_list[0].args[0], popen.call_args_list[1].args[0]
        assert sched_argv[:2] == ["dask", "scheduler"]
        assert worker_argv[0] == "srun"
        assert "--nodes=2" in worker_argv

        # Record persisted with mode=attached and PIDs.
        assert info.record is not None
        assert info.record.mode == "attached"
        assert info.record.process_pids == [111, 222]
        assert info.record.job_id == "12345"

        # State file matches the synthesized name.
        record = read_record("_attached_12345")
        assert record is not None
        assert record.mode == "attached"

    def test_idempotent_when_already_attached(self, monkeypatch):
        from lightcone.engine.clusters._slurm import attach_to_allocation

        monkeypatch.setenv("SLURM_JOB_ID", "9999")
        monkeypatch.setenv("SLURM_JOB_NUM_NODES", "1")
        monkeypatch.setenv("SLURM_CPUS_ON_NODE", "8")

        # Pre-seed a live attached record.
        write_record(_record(
            name="_attached_9999", job_id="9999", mode="attached",
            process_pids=[111, 222],
        ))
        # Make squeue / sacct say "RUNNING".
        with patch(
            "lightcone.engine.clusters._slurm.query_slurm_state",
            return_value="RUNNING",
        ), patch(
            "lightcone.engine.clusters._slurm.read_scheduler_address",
            return_value="tcp://nid000123:8786",
        ), patch(
            "lightcone.engine.clusters._slurm.subprocess.Popen",
        ) as popen:
            info = attach_to_allocation()
        # No new processes should be spawned.
        popen.assert_not_called()
        # Returned info reflects the existing record.
        assert info.record is not None
        assert info.record.process_pids == [111, 222]

    def test_sweeps_stale_attached_record(self, monkeypatch, tmp_path):
        from lightcone.engine.clusters._slurm import attach_to_allocation

        monkeypatch.setenv("SLURM_JOB_ID", "111")
        monkeypatch.setenv("SLURM_JOB_NUM_NODES", "1")
        monkeypatch.setenv("SLURM_CPUS_ON_NODE", "4")

        # Pre-existing attached record from a dead job — should be cleaned up
        # before the new one is created.
        write_record(_record(
            name="_attached_111", job_id="111", mode="attached",
            scheduler_file=str(tmp_path / "old.json"),
        ))
        with patch(
            "lightcone.engine.clusters._slurm.query_slurm_state",
            return_value="DEAD",
        ), patch(
            "lightcone.engine.clusters._slurm.subprocess.Popen",
            side_effect=[MagicMock(pid=1), MagicMock(pid=2)],
        ), patch(
            "lightcone.engine.clusters._slurm.expand_path",
            side_effect=lambda v: str(tmp_path / "scratch"),
        ):
            info = attach_to_allocation(project_root=tmp_path)

        assert info.record is not None
        assert info.record.process_pids == [1, 2]


# ---------------------------------------------------------------------------
# Idempotent start_slurm_cluster
# ---------------------------------------------------------------------------


class TestStartIdempotency:
    def test_returns_existing_when_running(self, perlmutter_yaml):
        from lightcone.engine.clusters._slurm import start_slurm_cluster

        write_record(_record(name="perlmutter", job_id="987"))
        with patch(
            "lightcone.engine.clusters._slurm.query_slurm_state",
            return_value="RUNNING",
        ), patch(
            "lightcone.engine.clusters._slurm.read_scheduler_address",
            return_value="tcp://nid000:8786",
        ), patch(
            "lightcone.engine.clusters._slurm.subprocess.run",
        ) as run:
            info = start_slurm_cluster("perlmutter", perlmutter_yaml, {})
        # Should NOT call sbatch.
        for call in run.call_args_list:
            assert call.args[0][0] != "sbatch"
        assert info.record is not None
        assert info.record.job_id == "987"
        assert info.state == "RUNNING"

    def test_sweeps_stale_state_and_resubmits(self, perlmutter_yaml, tmp_path):
        from lightcone.engine.clusters._slurm import start_slurm_cluster

        # Stale record (dead job).
        write_record(_record(name="perlmutter", job_id="111"))
        sbatch_result = MagicMock(returncode=0, stdout="Submitted batch job 22222\n", stderr="")
        with patch(
            "lightcone.engine.clusters._slurm.query_slurm_state",
            return_value="DEAD",
        ), patch(
            "lightcone.engine.clusters._slurm.subprocess.run",
            return_value=sbatch_result,
        ), patch(
            "lightcone.engine.clusters._slurm.ensure_worker_env",
        ):
            info = start_slurm_cluster("perlmutter", perlmutter_yaml, {}, project_root=tmp_path)
        assert info.record is not None
        assert info.record.job_id == "22222"


# ---------------------------------------------------------------------------
# Mode-aware stop_slurm_cluster
# ---------------------------------------------------------------------------


class TestStopModeAware:
    def test_attached_kills_pids_and_does_not_scancel(self, tmp_path):
        from lightcone.engine.clusters._slurm import stop_slurm_cluster

        write_record(_record(
            name="_attached_55", job_id="55", mode="attached",
            scheduler_file=str(tmp_path / "sched.json"),
            process_pids=[101, 102],
        ))
        with patch(
            "lightcone.engine.clusters._slurm.os.kill",
        ) as kill, patch(
            "lightcone.engine.clusters._slurm.subprocess.run",
        ) as run:
            stop_slurm_cluster("_attached_55")

        # Two SIGTERMs — one per pid; no scancel.
        assert kill.call_count == 2
        for call in run.call_args_list:
            assert call.args[0][0] != "scancel"
        assert not state_path("_attached_55").exists()

    def test_sbatch_calls_scancel(self, tmp_path):
        from lightcone.engine.clusters._slurm import stop_slurm_cluster

        write_record(_record(
            name="perlmutter", job_id="777", mode="sbatch",
            scheduler_file=str(tmp_path / "sched.json"),
        ))
        with patch(
            "lightcone.engine.clusters._slurm.subprocess.run",
        ) as run:
            stop_slurm_cluster("perlmutter")
        # scancel should have been called.
        cancel_calls = [c for c in run.call_args_list if c.args[0][0] == "scancel"]
        assert len(cancel_calls) == 1
        assert cancel_calls[0].args[0] == ["scancel", "777"]


# ---------------------------------------------------------------------------
# State file forward/backward-compat for the new `mode`/`process_pids` fields
# ---------------------------------------------------------------------------


class TestStateRecordCompat:
    def test_old_record_without_mode_defaults_to_sbatch(self):
        # Simulate a state file written before the `mode` field existed.
        path = state_path("legacy")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "name": "legacy",
            "type": "slurm",
            "job_id": "1",
            "site": "perlmutter",
            "submitted_at": "2025-01-01T00:00:00+00:00",
            "walltime_seconds": 3600,
            "scheduler_file": "/tmp/sched.json",
        }))
        record = read_record("legacy")
        assert record is not None
        assert record.mode == "sbatch"
        assert record.process_pids == []

    def test_extra_fields_are_ignored(self):
        path = state_path("future")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "name": "future",
            "type": "slurm",
            "job_id": "2",
            "site": "perlmutter",
            "submitted_at": "2025-01-01T00:00:00+00:00",
            "walltime_seconds": 3600,
            "scheduler_file": "/tmp/sched.json",
            "mode": "attached",
            "process_pids": [1, 2],
            "future_field_we_dont_know_about": True,
        }))
        record = read_record("future")
        assert record is not None
        assert record.mode == "attached"
        assert record.process_pids == [1, 2]


# ---------------------------------------------------------------------------
# `find_attached_cluster_for_job` — used by `lc run` resolution
# ---------------------------------------------------------------------------


class TestFindAttachedClusterForJob:
    def test_returns_match(self):
        from lightcone.engine.clusters import find_attached_cluster_for_job

        write_record(_record(
            name="_attached_42", job_id="42", mode="attached",
        ))
        with patch(
            "lightcone.engine.clusters._slurm.query_slurm_state",
            return_value="RUNNING",
        ), patch(
            "lightcone.engine.clusters._slurm.read_scheduler_address",
            return_value="tcp://nid:8786",
        ):
            info = find_attached_cluster_for_job("42")
        assert info is not None
        assert info.record is not None
        assert info.record.name == "_attached_42"

    def test_returns_none_when_no_match(self):
        from lightcone.engine.clusters import find_attached_cluster_for_job

        assert find_attached_cluster_for_job("999") is None


# ---------------------------------------------------------------------------
# Bundled Postgres (cluster.postgres_url)
# ---------------------------------------------------------------------------


class TestPostgresUrlPersistence:
    def test_attach_records_postgres_url(self, monkeypatch, tmp_path):
        """attach_to_allocation calls start_pg and stores the URI in the record."""
        from lightcone.engine.clusters import _slurm

        monkeypatch.setenv("SLURM_JOB_ID", "5050")
        monkeypatch.setenv("SLURM_JOB_NUM_NODES", "1")
        monkeypatch.setenv("SLURM_CPUS_ON_NODE", "8")

        # Override the conftest stub with a sentinel we can match against.
        sentinel_url = "postgresql://lc@nid001:5432/lc"
        monkeypatch.setattr(_slurm, "start_pg", lambda project_root: sentinel_url)

        with patch(
            "lightcone.engine.clusters._slurm.subprocess.Popen",
            side_effect=[MagicMock(pid=10), MagicMock(pid=11)],
        ):
            info = _slurm.attach_to_allocation(project_root=tmp_path)

        assert info.record is not None
        assert info.record.postgres_url == sentinel_url

    def test_start_records_postgres_url(self, perlmutter_yaml, monkeypatch, tmp_path):
        """start_slurm_cluster passes start_pg's URI through to the record."""
        from lightcone.engine.clusters import _slurm

        sentinel_url = "postgresql://lc@login01:5432/lc"
        monkeypatch.setattr(_slurm, "start_pg", lambda project_root: sentinel_url)

        sbatch = MagicMock(returncode=0, stdout="Submitted batch job 7777\n", stderr="")
        with patch(
            "lightcone.engine.clusters._slurm.subprocess.run",
            return_value=sbatch,
        ), patch(
            "lightcone.engine.clusters._slurm.ensure_worker_env",
        ):
            info = _slurm.start_slurm_cluster(
                "perlmutter", perlmutter_yaml, {}, project_root=tmp_path,
            )
        assert info.record is not None
        assert info.record.postgres_url == sentinel_url

    def test_stop_calls_stop_pg_when_url_recorded(self, monkeypatch, tmp_path):
        """stop_slurm_cluster invokes stop_pg only when postgres_url is set."""
        from lightcone.engine.clusters import _slurm

        write_record(_record(
            name="perlmutter", job_id="123", mode="sbatch",
            scheduler_file=str(tmp_path / "sched.json"),
            postgres_url="postgresql://lc@x:5432/lc",
        ))
        called: dict = {}
        monkeypatch.setattr(
            _slurm, "stop_pg", lambda project_root: called.setdefault("root", project_root),
        )
        with patch("lightcone.engine.clusters._slurm.subprocess.run"):
            _slurm.stop_slurm_cluster("perlmutter", project_root=tmp_path)
        assert called.get("root") == tmp_path

    def test_stop_skips_stop_pg_when_no_url(self, monkeypatch, tmp_path):
        """stop_slurm_cluster doesn't call stop_pg for legacy records."""
        from lightcone.engine.clusters import _slurm

        write_record(_record(
            name="perlmutter", job_id="123", mode="sbatch",
            scheduler_file=str(tmp_path / "sched.json"),
            # postgres_url defaults to None
        ))
        called: dict = {}
        monkeypatch.setattr(
            _slurm, "stop_pg", lambda project_root: called.setdefault("root", project_root),
        )
        with patch("lightcone.engine.clusters._slurm.subprocess.run"):
            _slurm.stop_slurm_cluster("perlmutter", project_root=tmp_path)
        assert "root" not in called  # stop_pg never invoked

    def test_legacy_record_loads_with_postgres_url_none(self):
        """Old state files (pre-PG) round-trip with postgres_url=None."""
        path = state_path("legacy")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "name": "legacy",
            "type": "slurm",
            "job_id": "1",
            "site": "perlmutter",
            "submitted_at": "2025-01-01T00:00:00+00:00",
            "walltime_seconds": 3600,
            "scheduler_file": "/tmp/sched.json",
            "mode": "sbatch",
        }))
        record = read_record("legacy")
        assert record is not None
        assert record.postgres_url is None
