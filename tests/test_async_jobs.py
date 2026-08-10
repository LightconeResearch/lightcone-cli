"""Tests for coarse-grained asynchronous SLURM jobs."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

import lightcone.engine.async_jobs as async_jobs
from lightcone.engine.async_jobs import (
    AsyncJobError,
    JobRecord,
    JobResources,
    SlurmSelection,
    aggregate_job_resources,
    cancel_job,
    job_display_state,
    pending_subdag_outputs,
    query_job_states,
    render_sbatch_script,
    resolve_subdag_outputs,
    select_slurm_policy,
    submit_job,
)
from lightcone.engine.manifest import code_version, write_manifest
from lightcone.engine.site_registry import SITE_DEFAULTS, HostSite


def _write_project(path: Path, outputs: list[dict[str, Any]]) -> None:
    path.mkdir(parents=True)
    (path / "astra.yaml").write_text(yaml.safe_dump({"outputs": outputs}))
    universes = path / "universes"
    universes.mkdir()
    (universes / "baseline.yaml").write_text("decisions: {}\n")


def _materialize(
    project: Path,
    output_id: str,
    *,
    recipe: str,
    inputs: dict[str, Path] | None = None,
) -> Path:
    output_dir = project / "results" / "baseline" / output_id
    output_dir.mkdir(parents=True)
    (output_dir / "data.txt").write_text(f"materialized {output_id}\n")
    write_manifest(
        output_dir=output_dir,
        inputs=inputs or {},
        cfg={
            "output_id": output_id,
            "universe_id": "baseline",
            "recipe": recipe,
            "container_image": None,
            "decisions": {},
            "code_version": code_version(
                recipe=recipe,
                container_image=None,
                decisions={},
            ),
            "git_sha": None,
            "lc_version": "test",
        },
    )
    return output_dir


def _perlmutter() -> HostSite:
    return HostSite(key="perlmutter", defaults=SITE_DEFAULTS["perlmutter"])


def test_resolve_subdag_and_aggregate_resources(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _write_project(
        project,
        [
            {
                "id": "prepare",
                "recipe": {
                    "command": "python prepare.py",
                    "resources": {"cpus": 2, "memory": "2GB", "time_limit": "10m"},
                },
            },
            {
                "id": "fit",
                "inputs": ["prepare"],
                "recipe": {
                    "command": "python fit.py",
                    "resources": {
                        "cpus": 16,
                        "memory": "64GB",
                        "gpus": 1,
                        "time_limit": "2h",
                    },
                },
            },
            {
                "id": "unrelated",
                "recipe": {
                    "command": "echo no",
                    "resources": {"time_limit": "5m"},
                },
            },
        ],
    )
    requested, subdag = resolve_subdag_outputs(project, ("fit",))
    assert requested == ["fit"]
    assert [output.output_id for output in subdag] == ["prepare", "fit"]

    resources = aggregate_job_resources(subdag, time_padding=1.5)
    assert resources == JobResources(
        cpus=16,
        memory_mb=64000,
        gpus=1,
        time_limit_seconds=11700,
        rule_count=2,
    )


def test_async_requires_time_limit_on_every_dependency(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _write_project(
        project,
        [
            {"id": "prepare", "recipe": {"command": "echo prepare"}},
            {
                "id": "fit",
                "inputs": ["prepare"],
                "recipe": {
                    "command": "echo fit",
                    "resources": {"time_limit": "1h"},
                },
            },
        ],
    )
    _, subdag = resolve_subdag_outputs(project, ("fit",))
    with pytest.raises(AsyncJobError, match="prepare"):
        aggregate_job_resources(subdag, time_padding=1.5)


def test_pending_subdag_excludes_current_upstream(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _write_project(
        project,
        [
            {"id": "prepare", "recipe": {"command": "echo prepare"}},
            {
                "id": "fit",
                "inputs": ["prepare"],
                "recipe": {
                    "command": "echo fit",
                    "resources": {"cpus": 8, "time_limit": "1h"},
                },
            },
        ],
    )
    _materialize(project, "prepare", recipe="echo prepare")
    _, subdag = resolve_subdag_outputs(project, ("fit",))

    pending = pending_subdag_outputs(project, subdag, universe="baseline")

    assert [output.output_id for output in pending] == ["fit"]
    assert aggregate_job_resources(pending, time_padding=1.5).rule_count == 1


def test_pending_subdag_propagates_stale_upstream(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _write_project(
        project,
        [
            {
                "id": "prepare",
                "recipe": {
                    "command": "echo new",
                    "resources": {"time_limit": "10m"},
                },
            },
            {
                "id": "fit",
                "inputs": ["prepare"],
                "recipe": {
                    "command": "echo fit",
                    "resources": {"time_limit": "1h"},
                },
            },
        ],
    )
    prepare_dir = _materialize(project, "prepare", recipe="echo old")
    _materialize(
        project,
        "fit",
        recipe="echo fit",
        inputs={"prepare": prepare_dir},
    )
    _, subdag = resolve_subdag_outputs(project, ("fit",))

    pending = pending_subdag_outputs(project, subdag, universe="baseline")

    assert [output.output_id for output in pending] == ["prepare", "fit"]


def test_pending_subdag_detects_changed_external_input(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _write_project(
        project,
        [
            {
                "id": "fit",
                "inputs": ["catalog"],
                "recipe": {
                    "command": "echo fit",
                    "resources": {"time_limit": "1h"},
                },
            }
        ],
    )
    spec = yaml.safe_load((project / "astra.yaml").read_text())
    spec["inputs"] = [{"id": "catalog", "source": "data/catalog.txt"}]
    (project / "astra.yaml").write_text(yaml.safe_dump(spec))
    source = project / "data" / "catalog.txt"
    source.parent.mkdir()
    source.write_text("old\n")
    _materialize(
        project,
        "fit",
        recipe="echo fit",
        inputs={"catalog": source},
    )
    source.write_text("new and different\n")
    _, subdag = resolve_subdag_outputs(project, ("fit",))

    pending = pending_subdag_outputs(project, subdag, universe="baseline")

    assert [output.output_id for output in pending] == ["fit"]


def test_submit_job_requires_explicit_output(tmp_path: Path) -> None:
    with pytest.raises(AsyncJobError, match="explicit output"):
        submit_job(tmp_path, output_ids=(), universe="baseline")


def test_policy_prefers_cpu_shared() -> None:
    selection = select_slurm_policy(
        JobResources(32, 128000, 0, 3600, 1), site=_perlmutter()
    )
    assert selection.qos == "shared"
    assert selection.profile == "cpu"
    assert selection.allocation_cpus == 32
    assert selection.allocation_memory_mb == 128000


def test_policy_uses_gpu_shared_allocation_ratio() -> None:
    shared = select_slurm_policy(
        JobResources(16, 64000, 1, 3600, 1), site=_perlmutter()
    )
    regular = select_slurm_policy(
        JobResources(32, 64000, 1, 3600, 1), site=_perlmutter()
    )
    assert shared.qos == "shared"
    assert shared.profile == "gpu"
    assert regular.qos == "regular"


def test_policy_rejects_walltime_beyond_regular_cap() -> None:
    with pytest.raises(AsyncJobError, match="exceeds the regular QoS cap"):
        select_slurm_policy(
            JobResources(1, 0, 0, 48 * 3600 + 1, 1), site=_perlmutter()
        )


def test_render_script_reenters_plain_lc_run(tmp_path: Path) -> None:
    script = render_sbatch_script(
        project_path=tmp_path,
        account="m1234",
        resources=JobResources(16, 64000, 1, 7200, 1),
        selection=SlurmSelection(
            "perlmutter",
            "gpu",
            "gpu",
            "shared",
            allocation_cpus=16,
            allocation_memory_mb=64000,
            allocation_gpus=1,
        ),
        log_template=tmp_path / "job-%j.log",
        job_name="lc-fit-baseline",
        lc_args=["run", "fit", "--universe", "baseline"],
    )
    assert "#SBATCH --account=m1234" in script
    assert "#SBATCH --qos=shared" in script
    assert "#SBATCH --gpus=1" in script
    assert "#SBATCH --cpus-per-task=16" in script
    assert "#SBATCH --mem=64000M" in script
    assert "unset DASK_SCHEDULER_ADDRESS" in script
    assert " run fit --universe baseline" in script
    assert "--async" not in script
    assert "pip install" not in script


def test_submit_job_writes_script_and_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    _write_project(
        project,
        [
            {
                "id": "fit",
                "recipe": {
                    "command": "python fit.py",
                    "resources": {
                        "cpus": 16,
                        "memory": "64GB",
                        "gpus": 1,
                        "time_limit": "2h",
                    },
                },
            }
        ],
    )
    home = tmp_path / "home"
    (home / ".lightcone").mkdir(parents=True)
    (home / ".lightcone" / "config.yaml").write_text(
        "container:\n  runtime: auto\nslurm:\n  account: m1234\n  time_padding: 1.5\n"
    )
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(async_jobs, "detect_current_site", _perlmutter)
    monkeypatch.setattr(async_jobs, "resolve_scratch_root", lambda _: tmp_path / "scratch")

    calls: list[list[str]] = []
    submitted_environments: list[dict[str, str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        submitted_environments.append(kwargs["env"])
        return subprocess.CompletedProcess(command, 0, stdout="12345;perlmutter\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("SLURM_JOB_ID", "parent-job")
    monkeypatch.setenv("SLURM_CPUS_PER_TASK", "2")
    monkeypatch.setenv("SLURM_TRES_PER_TASK", "cpu=2")
    monkeypatch.setenv("SLURM_CONF", "/etc/slurm/slurm.conf")
    record = submit_job(project, output_ids=("fit",), universe="baseline")

    assert record.job_id == "12345"
    assert record.qos == "shared"
    assert calls == [["sbatch", "--parsable", record.sbatch_path]]
    submission_env = submitted_environments[0]
    assert "SLURM_JOB_ID" not in submission_env
    assert "SLURM_CPUS_PER_TASK" not in submission_env
    assert "SLURM_TRES_PER_TASK" not in submission_env
    assert submission_env["SLURM_CONF"] == "/etc/slurm/slurm.conf"
    assert submission_env["PATH"] == os.environ["PATH"]
    assert Path(record.sbatch_path).is_file()
    assert Path(record.sbatch_path).read_text().count("lc") > 0
    saved = json.loads((project / ".lightcone" / "jobs" / "12345.json").read_text())
    assert saved["resolved_targets"] == ["fit"]
    assert saved["resources"]["time_limit_seconds"] == 10800


def test_query_job_states_uses_squeue_then_sacct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_query(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[0] == "squeue":
            return subprocess.CompletedProcess(command, 0, stdout="11|RUNNING\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="22|TIMEOUT\n", stderr="")

    monkeypatch.setattr(async_jobs, "_scheduler_query", fake_query)
    states, available = query_job_states(["11", "22"])
    assert available is True
    assert states == {"11": "RUNNING", "22": "TIMEOUT"}


@pytest.mark.parametrize(
    ("raw", "display"),
    [
        ("PENDING", "queued"),
        ("RUNNING", "running"),
        ("COMPLETED", "completed"),
        ("TIMEOUT", "failed"),
        ("CANCELLED by 1", "cancelled"),
        ("mystery", "unknown"),
    ],
)
def test_job_display_state(raw: str, display: str) -> None:
    assert job_display_state(raw) == display


def test_cancel_job_updates_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    directory = project / ".lightcone" / "jobs"
    directory.mkdir(parents=True)
    record = JobRecord(
        job_id="123",
        targets=["fit"],
        resolved_targets=["prepare", "fit"],
        universe="baseline",
        qos="regular",
        resources={},
        sbatch_path="job.sbatch",
        log_path="job.log",
        submitted_at="2026-07-16T00:00:00+00:00",
        last_state="PENDING",
    )
    (directory / "123.json").write_text(json.dumps(record.__dict__))
    monkeypatch.setattr(async_jobs, "query_job_states", lambda _: ({"123": "PENDING"}, True))

    calls: list[list[str]] = []

    def fake_run(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    cancelled = cancel_job(project, "prepare")
    assert cancelled.last_state == "CANCELLED"
    assert calls == [["scancel", "123"]]
    assert json.loads((directory / "123.json").read_text())["last_state"] == "CANCELLED"
