"""Tests for ``lightcone.engine.clusters`` — config CRUD, sbatch rendering,
QoS preflight, per-project state file, dispatcher, lifecycle (sbatch /
attached / local), bundled Postgres."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from lightcone.engine.clusters import (
    ClusterRecord,
    ClusterSpec,
    WorkerPool,
    is_login_node,
    list_clusters,
    load_cluster_config,
    parse_walltime_seconds,
    project_state_path,
    read_project_state,
    read_scheduler_address,
    save_cluster_config,
    spec_from_config,
    start_cluster,
    stop_cluster,
    walltime_to_slurm,
)
from lightcone.engine.clusters._common import (
    clear_project_state,
    write_project_state,
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
# Config CRUD (cluster yaml templates in ~/.lightcone/clusters/)
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
        assert spec.scratch_root == "$PSCRATCH"
        assert spec.container_runtime == "podman-hpc"
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
        assert script.count("--constraint=") == 1

    def test_multipool_cpu_gpu_emits_two_srun(self):
        spec = _spec(workers=[
            WorkerPool(nodes=3, constraint="cpu", resources={}),
            WorkerPool(nodes=1, constraint="gpu", resources={"GPU": 4}),
        ])
        script = render_sbatch(spec)
        assert "#SBATCH --nodes=4" in script
        assert script.count("srun") >= 2
        assert "--constraint=cpu" in script
        assert "--constraint=gpu" in script
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
# QoS preflight
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
        result = validate_against_qos(spec)
        assert result.qos == "debug"

    def test_fit_clamps_walltime(self):
        from lightcone.engine.clusters._slurm import validate_against_qos

        self._populate_cache()
        spec = _spec(qos="debug", walltime="2h")
        result = validate_against_qos(spec, strategy="fit")
        assert parse_walltime_seconds(result.walltime) <= 30 * 60

    def test_eligible_passthrough(self):
        from lightcone.engine.clusters._slurm import validate_against_qos

        self._populate_cache()
        spec = _spec(qos="regular", walltime="2h")
        result = validate_against_qos(spec)
        assert result.walltime == "2h"


# ---------------------------------------------------------------------------
# Per-project state file CRUD
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


class TestProjectStateFile:
    def test_round_trip(self, tmp_path):
        rec = _record()
        write_project_state(tmp_path, rec)
        loaded = read_project_state(tmp_path)
        assert loaded == rec

    def test_state_path_in_project_lightcone_dir(self, tmp_path):
        assert project_state_path(tmp_path) == tmp_path / ".lightcone" / "cluster.state.json"

    def test_read_missing_returns_none(self, tmp_path):
        assert read_project_state(tmp_path) is None

    def test_clear_is_idempotent(self, tmp_path):
        clear_project_state(tmp_path)  # no file
        write_project_state(tmp_path, _record())
        clear_project_state(tmp_path)
        assert read_project_state(tmp_path) is None

    def test_extra_fields_in_state_file_are_ignored(self, tmp_path):
        path = project_state_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "name": "fwd",
            "type": "slurm",
            "job_id": "1",
            "site": "perlmutter",
            "submitted_at": "2025-01-01T00:00:00+00:00",
            "walltime_seconds": 0,
            "scheduler_file": "/tmp/sched.json",
            "future_field_we_dont_know": True,
        }))
        rec = read_project_state(tmp_path)
        assert rec is not None
        assert rec.name == "fwd"


# ---------------------------------------------------------------------------
# Login-node detection
# ---------------------------------------------------------------------------


class TestLoginNodeDetection:
    def test_no_slurm_no_nersc(self, monkeypatch):
        monkeypatch.delenv("SLURM_JOB_ID", raising=False)
        monkeypatch.delenv("NERSC_HOST", raising=False)
        monkeypatch.delenv("LMOD_SYSTEM_NAME", raising=False)
        assert is_login_node() is False

    def test_nersc_set_no_slurm(self, monkeypatch):
        monkeypatch.delenv("SLURM_JOB_ID", raising=False)
        monkeypatch.setenv("NERSC_HOST", "perlmutter")
        assert is_login_node() is True

    def test_inside_salloc_is_not_login(self, monkeypatch):
        monkeypatch.setenv("SLURM_JOB_ID", "123")
        monkeypatch.setenv("NERSC_HOST", "perlmutter")
        assert is_login_node() is False


# ---------------------------------------------------------------------------
# Unified `start_cluster` dispatcher
# ---------------------------------------------------------------------------


class TestStartClusterDispatch:
    def test_target_dispatches_to_sbatch(self, perlmutter_yaml, monkeypatch, tmp_path):
        save_cluster_config("perlmutter", perlmutter_yaml)
        monkeypatch.delenv("SLURM_JOB_ID", raising=False)
        sbatch_calls: list = []

        def fake_start_slurm(target, config, overrides, *, project_root, strategy):
            sbatch_calls.append((target, project_root))
            from lightcone.engine.clusters._common import ClusterInfo
            return ClusterInfo(
                spec=spec_from_config(target, config),
                record=_record(name=target, mode="sbatch"),
                state="PENDING", scheduler_address=None,
            )

        monkeypatch.setattr(
            "lightcone.engine.clusters._slurm.start_slurm_cluster", fake_start_slurm,
        )
        info = start_cluster(target="perlmutter", project_root=tmp_path)
        assert sbatch_calls == [("perlmutter", tmp_path.resolve())]
        assert info.record.mode == "sbatch"

    def test_inside_salloc_dispatches_to_attach(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SLURM_JOB_ID", "9999")
        called: list = []

        def fake_attach(*, project_root):
            called.append(project_root)
            from lightcone.engine.clusters._common import ClusterInfo
            return ClusterInfo(
                spec=_spec(),
                record=_record(name="_attached_9999", mode="attached", job_id="9999"),
                state="PENDING", scheduler_address=None,
            )

        monkeypatch.setattr(
            "lightcone.engine.clusters._slurm.attach_to_allocation", fake_attach,
        )
        info = start_cluster(project_root=tmp_path)
        assert called == [tmp_path.resolve()]
        assert info.record.mode == "attached"

    def test_login_node_refuses_local(self, monkeypatch, tmp_path):
        monkeypatch.delenv("SLURM_JOB_ID", raising=False)
        monkeypatch.setenv("NERSC_HOST", "perlmutter")
        with pytest.raises(RuntimeError, match="Refusing to start a local cluster"):
            start_cluster(project_root=tmp_path)

    def test_laptop_dispatches_to_local(self, monkeypatch, tmp_path):
        monkeypatch.delenv("SLURM_JOB_ID", raising=False)
        monkeypatch.delenv("NERSC_HOST", raising=False)
        monkeypatch.delenv("LMOD_SYSTEM_NAME", raising=False)
        called: list = []

        def fake_local(project_root):
            called.append(project_root)
            from lightcone.engine.clusters._common import ClusterInfo
            return ClusterInfo(
                spec=_spec(), record=_record(name="_local", mode="local"),
                state="RUNNING", scheduler_address="tcp://localhost:8786",
            )

        monkeypatch.setattr(
            "lightcone.engine.clusters._local.start_local_cluster", fake_local,
        )
        info = start_cluster(project_root=tmp_path)
        assert called == [tmp_path.resolve()]
        assert info.record.mode == "local"

    def test_unknown_target_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="No cluster target"):
            start_cluster(target="ghost", project_root=tmp_path)


# ---------------------------------------------------------------------------
# Unified `stop_cluster` dispatcher
# ---------------------------------------------------------------------------


class TestStopClusterDispatch:
    def test_no_active_cluster_is_noop(self, tmp_path):
        stop_cluster(project_root=tmp_path)  # no error

    def test_local_dispatches_to_local_stop(self, monkeypatch, tmp_path):
        write_project_state(tmp_path, _record(name="_local", mode="local"))
        called: list = []
        monkeypatch.setattr(
            "lightcone.engine.clusters._local.stop_local_cluster",
            lambda *, project_root: called.append(project_root),
        )
        stop_cluster(project_root=tmp_path)
        assert called == [tmp_path.resolve()]

    def test_slurm_dispatches_to_slurm_stop(self, monkeypatch, tmp_path):
        write_project_state(tmp_path, _record(name="perl", mode="sbatch"))
        called: list = []
        monkeypatch.setattr(
            "lightcone.engine.clusters._slurm.stop_slurm_cluster",
            lambda *, project_root: called.append(project_root),
        )
        stop_cluster(project_root=tmp_path)
        assert called == [tmp_path.resolve()]


# ---------------------------------------------------------------------------
# Attach lifecycle (in-allocation)
# ---------------------------------------------------------------------------


class TestAttachToAllocation:
    def test_errors_outside_allocation(self, monkeypatch):
        from lightcone.engine.clusters._slurm import attach_to_allocation

        monkeypatch.delenv("SLURM_JOB_ID", raising=False)
        with pytest.raises(RuntimeError, match="Not inside a SLURM allocation"):
            attach_to_allocation()

    def test_writes_attached_state_with_pg_url(self, monkeypatch, tmp_path):
        from lightcone.engine.clusters._slurm import attach_to_allocation

        monkeypatch.setenv("SLURM_JOB_ID", "12345")
        monkeypatch.setenv("SLURM_JOB_NUM_NODES", "2")
        monkeypatch.setenv("SLURM_CPUS_ON_NODE", "64")

        sched_proc = MagicMock(pid=111)
        worker_proc = MagicMock(pid=222)
        with patch(
            "lightcone.engine.clusters._slurm.subprocess.Popen",
            side_effect=[sched_proc, worker_proc],
        ) as popen, patch(
            "lightcone.engine.clusters._slurm.subprocess.run",
        ) as run:
            attach_to_allocation(project_root=tmp_path)

        for call in run.call_args_list:
            assert call.args[0][0] != "sbatch"

        assert popen.call_count == 2
        sched_argv = popen.call_args_list[0].args[0]
        worker_argv = popen.call_args_list[1].args[0]
        assert sched_argv[:2] == ["dask", "scheduler"]
        assert worker_argv[0] == "srun"
        assert "--nodes=2" in worker_argv

        record = read_project_state(tmp_path)
        assert record is not None
        assert record.mode == "attached"
        assert record.job_id == "12345"
        assert record.process_pids == [111, 222]
        # The conftest pg stub populates a non-None URL.
        assert record.postgres_url == "postgresql://test/db"


# ---------------------------------------------------------------------------
# Sbatch start lifecycle (--target)
# ---------------------------------------------------------------------------


class TestStartSlurmCluster:
    def test_records_state_with_pg_url(self, perlmutter_yaml, tmp_path):
        from lightcone.engine.clusters._slurm import start_slurm_cluster

        sbatch_result = MagicMock(returncode=0, stdout="Submitted batch job 22222\n", stderr="")
        with patch(
            "lightcone.engine.clusters._slurm.subprocess.run",
            return_value=sbatch_result,
        ), patch(
            "lightcone.engine.clusters._slurm.ensure_worker_env",
        ):
            info = start_slurm_cluster(
                "perlmutter", perlmutter_yaml, {}, project_root=tmp_path,
            )

        assert info.record is not None
        assert info.record.job_id == "22222"
        assert info.record.postgres_url == "postgresql://test/db"
        # State persisted under per-project location.
        record = read_project_state(tmp_path)
        assert record is not None and record.job_id == "22222"

    def test_idempotent_when_already_running(self, perlmutter_yaml, tmp_path):
        from lightcone.engine.clusters._slurm import start_slurm_cluster

        write_project_state(
            tmp_path,
            _record(name="perlmutter", job_id="987", postgres_url="postgresql://x/y"),
        )
        with patch(
            "lightcone.engine.clusters._slurm.query_slurm_state",
            return_value="RUNNING",
        ), patch(
            "lightcone.engine.clusters._slurm.read_scheduler_address",
            return_value="tcp://nid:8786",
        ), patch(
            "lightcone.engine.clusters._slurm.subprocess.run",
        ) as run:
            info = start_slurm_cluster(
                "perlmutter", perlmutter_yaml, {}, project_root=tmp_path,
            )
        for call in run.call_args_list:
            assert call.args[0][0] != "sbatch"
        assert info.record is not None
        assert info.record.job_id == "987"


# ---------------------------------------------------------------------------
# Mode-aware stop
# ---------------------------------------------------------------------------


class TestStopSlurmCluster:
    def test_attached_kills_pids_and_does_not_scancel(self, tmp_path):
        from lightcone.engine.clusters._slurm import stop_slurm_cluster

        write_project_state(tmp_path, _record(
            name="_attached_55", job_id="55", mode="attached",
            scheduler_file=str(tmp_path / "sched.json"),
            process_pids=[101, 102],
            postgres_url="postgresql://x/y",
        ))
        with patch(
            "lightcone.engine.clusters._slurm.os.kill",
        ) as kill, patch(
            "lightcone.engine.clusters._slurm.subprocess.run",
        ) as run:
            stop_slurm_cluster(project_root=tmp_path)
        assert kill.call_count == 2
        for call in run.call_args_list:
            assert call.args[0][0] != "scancel"
        assert read_project_state(tmp_path) is None

    def test_sbatch_calls_scancel(self, tmp_path):
        from lightcone.engine.clusters._slurm import stop_slurm_cluster

        write_project_state(tmp_path, _record(
            name="perlmutter", job_id="777", mode="sbatch",
            scheduler_file=str(tmp_path / "sched.json"),
        ))
        with patch(
            "lightcone.engine.clusters._slurm.subprocess.run",
        ) as run:
            stop_slurm_cluster(project_root=tmp_path)
        cancel_calls = [c for c in run.call_args_list if c.args[0][0] == "scancel"]
        assert len(cancel_calls) == 1
        assert cancel_calls[0].args[0] == ["scancel", "777"]
