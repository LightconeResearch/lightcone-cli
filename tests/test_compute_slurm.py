"""Native Slurm lifecycle contracts without submitting a real allocation."""

from __future__ import annotations

import argparse
import asyncio
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import psutil
import pytest

from lightcone.engine.compute import Compute, slurm, slurm_bootstrap
from lightcone.engine.compute.catalog import Catalog
from lightcone.engine.compute.model import (
    ComputeError,
    Connection,
    Identity,
    Offer,
    Request,
    Resources,
    TimeLimits,
)
from lightcone.engine.compute.runtime import (
    open_client,
    private_directory,
    read_private_json,
    write_private_json,
)

NAMESPACE = "9d0c0fc5-9be8-407a-a3ec-f17c4110b162"
TOKEN = "c82a7b8d0ccf40a4be0e57e784edb989"
IDENTITY = Identity(namespace=NAMESPACE, native_id="123", token=TOKEN)
NAME = f"lc-v1-{IDENTITY.name}"
COMMENT = f"lightcone:v1:kind=dask:token={TOKEN}"


@pytest.fixture
def provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> slurm.SlurmProvider:
    _native(monkeypatch, {})
    return slurm.SlurmProvider(
        Connection(
            namespace=NAMESPACE,
            provider="slurm",
            context="perlmutter",
            launch={
                "python": sys.executable,
                "connection_root": str(tmp_path / "private"),
                "scratch_root": str(tmp_path / "scratch"),
                "cwd": str(tmp_path),
                "task_slots_per_node": 126,
            },
        )
    )


@pytest.fixture
def offer() -> Offer:
    return Offer(
        name="batch",
        connection="nersc",
        resources=Resources(cpus=256, memory_gib=480),
        max_nodes=4,
        time=TimeLimits(default="1h", max="4h"),
        config={
            "submit": "sbatch",
            "account": "myproject",
            "qos": "regular",
            "constraint": "cpu",
        },
    )


def _live(
    *, job_id: str = "123", token: str = TOKEN, uid: int | None = None,
    state: str = "RUNNING", name: str = IDENTITY.name, comment: str | None = None,
) -> str:
    comment = comment if comment is not None else f"lightcone:v1:kind=dask:token={token}"
    return f"{job_id}|lc-v1-{name}|{os.getuid() if uid is None else uid}|{state}|{comment}\n"


def _control(
    *, token: str = TOKEN, uid: int | None = None, state: str = "RUNNING", restarts: int = 0,
    name: str = IDENTITY.name, comment: str | None = None,
) -> str:
    owner = os.getuid() if uid is None else uid
    comment = comment if comment is not None else f"lightcone:v1:kind=dask:token={token}"
    return (
        f"JobId=123 JobName=lc-v1-{name} UserId=alice({owner}) JobState={state}\n"
        f"   Comment={comment} \n"
        f"   NumNodes=2 NumCPUs=512 CPUs/Task=256 MinMemoryNode=480G Restarts={restarts} "
        "SubmitTime=2026-09-27T10:00:00 StartTime=2026-09-27T10:00:05 Reason=None\n"
    )


def _history(
    *, token: str = TOKEN, state: str = "COMPLETED", name: str = IDENTITY.name,
    job_id: str = "123", submitted: str = "2026-09-27T10:00:00", comment: str | None = None,
    uid: int | None = None,
) -> str:
    comment = comment if comment is not None else f"lightcone:v1:kind=dask:token={token}"
    owner = os.getuid() if uid is None else uid
    return f"{job_id}|lc-v1-{name}|{owner}|{state}|{submitted}|{comment}\n"


def _native(
    monkeypatch: pytest.MonkeyPatch, answers: dict[str, Any]
) -> list[tuple[list[str], dict[str, Any]]]:
    calls: list[tuple[list[str], dict[str, Any]]] = []
    answers = {"id": f"{os.getuid()}\n", **answers}

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        answer = answers[argv[0]]
        if isinstance(answer, list):
            answer = answer.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, tuple):
            code, stdout, stderr = answer
            return subprocess.CompletedProcess(argv, code, stdout, stderr)
        return subprocess.CompletedProcess(argv, 0, answer, "")

    monkeypatch.setattr(slurm.subprocess, "run", run)
    return calls


def _metadata(provider: slurm.SlurmProvider, *, restarts: int = 0, **changes: Any) -> Path:
    directory = private_directory(
        slurm.attempt_directory(provider.connection, IDENTITY, restarts), create=True
    )
    write_private_json(
        directory / "identity.json",
        {
            "namespace": NAMESPACE,
            "native_id": "123",
            "token": TOKEN,
            "uid": os.getuid(),
            "restarts": restarts,
            "num_nodes": 2,
            "scheduler_id": "Scheduler-test",
            **changes,
        },
    )
    return directory


def test_plan_preserves_native_envelope_without_native_queries(
    provider: slurm.SlurmProvider, offer: Offer, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _native(monkeypatch, {})
    plan = provider.plan(offer, Request.parse("32+", "120+", num_nodes=2))
    assert plan.resources == Resources.from_bytes(cpus=256, memory_bytes=515396075520)
    assert plan.details["task_slots_per_node"] == 126
    assert "--mem=491520M" in plan.details["native_args"]
    assert "--cpus-per-task=256" in plan.details["native_args"]
    assert "--nodes=2" in plan.details["native_args"]
    assert "--time=01:00:00" in plan.details["native_args"]
    assert "--clusters=perlmutter" in plan.details["native_args"]
    assert not any(arg.startswith("--partition=") for arg in plan.details["native_args"])
    assert "time_policy" not in plan.details
    assert calls == []


def test_plan_resolves_configured_roots_but_preserves_virtualenv_python(
    provider: slurm.SlurmProvider, offer: Offer, tmp_path: Path,
) -> None:
    actual = tmp_path / "actual-home"
    actual.mkdir()
    alias = tmp_path / "home"
    alias.symlink_to(actual, target_is_directory=True)
    python = alias / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    connection = provider.connection.replace(
        launch={
            **provider.connection.launch,
            "python": str(python),
            "connection_root": str(alias / "private"),
            "scratch_root": str(alias / "scratch"),
        },
    )
    plan = slurm.SlurmProvider(connection).plan(offer, Request.parse("256", "480"))
    assert plan.details["connection_root"] == str(actual / "private")
    assert plan.details["scratch_root"] == str(actual / "scratch")
    assert plan.details["python"] == str(python)


@pytest.mark.parametrize(
    "config",
    [
        {"wrap": "anything"},
        {"array": "0-3"},
        {"qos": "regular\n--nodes=10"},
        {"submit": "bash"},
        {"requeue": True},
    ],
)
def test_plan_rejects_unowned_native_options(
    provider: slurm.SlurmProvider, offer: Offer, config: dict[str, Any]
) -> None:
    with pytest.raises(ComputeError):
        provider.plan(offer.replace(config=config), Request.parse("256", "480"))


def test_plan_refuses_hidden_memory_rounding(provider: slurm.SlurmProvider, offer: Offer) -> None:
    with pytest.raises(ComputeError, match="whole number of MiB"):
        provider.plan(
            offer.replace(resources=Resources.from_bytes(cpus=256, memory_bytes=100001)),
            Request(cpus=256, memory_bytes=100001),
        )


@pytest.mark.parametrize("submit", ["sbatch", "salloc"])
def test_plan_includes_only_an_explicit_partition_and_requested_walltime(
    provider: slurm.SlurmProvider,
    offer: Offer,
    monkeypatch: pytest.MonkeyPatch,
    submit: str,
) -> None:
    calls = _native(monkeypatch, {})
    plan = provider.plan(
        offer.replace(config={**offer.config, "partition": "short", "submit": submit}),
        Request.parse("256", "480", time="30m"),
    )
    assert "--partition=short" in plan.details["native_args"]
    assert "--time=00:30:00" in plan.details["native_args"]
    assert plan.seconds == 1800
    assert calls == []


def test_plan_refuses_multiple_partitions(provider: slurm.SlurmProvider, offer: Offer) -> None:
    with pytest.raises(ComputeError, match="one native partition"):
        provider.plan(
            offer.replace(config={**offer.config, "partition": "short,long"}),
            Request.parse("256", "480"),
        )


def test_sbatch_launch_owns_payload_and_scrubs_ambient_overrides(
    provider: slurm.SlurmProvider,
    offer: Offer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slurm.uuid, "uuid4", lambda: SimpleNamespace(hex=TOKEN))
    for name in (
        "SBATCH_WRAP",
        "SBATCH_ARRAY_INX",
        "SALLOC_NODES",
        "SRUN_CPUS_PER_TASK",
        "SLURM_JOB_ID",
        "SLURM_CLUSTERS",
    ):
        monkeypatch.setenv(name, "bad-override")
    monkeypatch.setenv("SLURM_CONF", "/etc/slurm/site.conf")
    monkeypatch.setenv("SLURM_JWT", "private-auth")
    calls = _native(monkeypatch, {"sbatch": "123;perlmutter\n"})
    plan = provider.plan(offer, Request.parse("256", "480", num_nodes=2))
    assert provider.launch(plan) == IDENTITY
    argv, kwargs = next(call for call in calls if call[0][0] == "sbatch")
    assert "--parsable" in argv and "--no-requeue" in argv
    assert f"--job-name={NAME}" in argv
    assert f"--comment={COMMENT}" in argv
    assert kwargs["timeout"] > 0
    assert kwargs["env"]["SLURM_CONF"] == "/etc/slurm/site.conf"
    assert kwargs["env"]["SLURM_JWT"] == "private-auth"
    assert "SLURM_JOB_ID" not in kwargs["env"]
    assert "SBATCH_WRAP" not in kwargs["env"]
    assert "SRUN_CPUS_PER_TASK" not in kwargs["env"]
    script = kwargs["input"]
    assert script.startswith("#!/bin/bash\nset -euo pipefail\numask 077\n")
    payload = shlex.split(script.splitlines()[-1])
    assert payload[:2] == ["exec", "srun"]
    assert "--kill-on-bad-exit=1" in payload and "--overlap" not in payload
    assert "--ntasks=2" in payload
    python = payload.index(sys.executable)
    assert payload[python + 1 : python + 4] == [
        "-P",
        "-m",
        "lightcone.engine.compute.slurm_bootstrap",
    ]
    assert payload[payload.index("--memory-bytes") + 1] == "515396075520"


def test_sbatch_argv_does_not_interpret_shell_metacharacters(
    provider: slurm.SlurmProvider,
    offer: Offer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _native(monkeypatch, {"sbatch": "123\n"})
    config = {**offer.config, "account": "project;$(touch /tmp/not-executed)"}
    provider.launch(provider.plan(offer.replace(config=config), Request.parse("256", "480")))
    argv, kwargs = next(call for call in calls if call[0][0] == "sbatch")
    assert "--account=project;$(touch /tmp/not-executed)" in argv
    assert "shell" not in kwargs


def test_uncertain_submission_recovers_known_id_without_accounting(
    provider: slurm.SlurmProvider,
    offer: Offer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slurm.uuid, "uuid4", lambda: SimpleNamespace(hex=TOKEN))
    target_uid = os.getuid() + 10000
    calls = _native(
        monkeypatch,
        {
            "id": f"{target_uid}\n",
            "sbatch": subprocess.TimeoutExpired("sbatch", 60),
            "squeue": _live(uid=target_uid),
        },
    )
    assert provider.launch(provider.plan(offer, Request.parse("256", "480"))) == IDENTITY
    slurm_calls = [(argv, kwargs) for argv, kwargs in calls if argv[0] != "id"]
    assert [argv[0] for argv, _ in slurm_calls] == ["sbatch", "squeue"]
    assert slurm_calls[0][1]["timeout"] == 60
    assert slurm_calls[1][1]["timeout"] == 10


def test_unknown_submission_returns_reconciliation_token_and_never_resubmits(
    provider: slurm.SlurmProvider,
    offer: Offer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slurm.uuid, "uuid4", lambda: SimpleNamespace(hex=TOKEN))
    calls = _native(monkeypatch, {"sbatch": "garbled", "squeue": "", "sacct": ""})
    with pytest.raises(ComputeError, match="Do not resubmit") as raised:
        provider.launch(provider.plan(offer, Request.parse("256", "480")))
    assert raised.value.submission_token == TOKEN
    assert raised.value.cluster_id is None
    assert sum(argv[0] == "sbatch" for argv, _ in calls) == 1


def test_ambiguous_recovery_refuses_to_pick_a_job(
    provider: slurm.SlurmProvider,
    offer: Offer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slurm.uuid, "uuid4", lambda: SimpleNamespace(hex=TOKEN))
    _native(monkeypatch, {"sbatch": "garbled", "squeue": _live() + _live(job_id="124")})
    with pytest.raises(ComputeError) as raised:
        provider.launch(provider.plan(offer, Request.parse("256", "480")))
    assert raised.value.submission_token == TOKEN


def test_salloc_runs_the_native_driver_detached_until_authoritative_acceptance(
    provider: slurm.SlurmProvider,
    offer: Offer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slurm.uuid, "uuid4", lambda: SimpleNamespace(hex=TOKEN))
    popen = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(slurm.subprocess, "Popen", popen)
    _native(monkeypatch, {"squeue": _live(state="PENDING")})
    interactive = offer.replace(config={**offer.config, "submit": "salloc", "qos": "interactive"})
    assert provider.launch(provider.plan(interactive, Request.parse("256", "480"))) == IDENTITY
    argv = popen.call_args.args[0]
    assert argv[0] == "salloc" and "srun" in argv
    assert "--kill-command=TERM" in argv
    python = argv.index(sys.executable)
    assert argv[python + 1 : python + 4] == [
        "-P",
        "-m",
        "lightcone.engine.compute.slurm_bootstrap",
    ]
    assert not {"--no-shell", "--no-requeue", "--parsable"}.intersection(argv)
    assert popen.call_args.kwargs["start_new_session"] is True
    assert popen.call_args.kwargs["stdin"] == subprocess.DEVNULL
    assert popen.call_args.kwargs["stdout"] is not subprocess.PIPE


@pytest.mark.parametrize("submit", ["sbatch", "salloc"])
@pytest.mark.parametrize("name", ["a", "cosmology-fast", "a" * 63])
def test_named_launch_keeps_the_full_submission_token_in_native_metadata(
    provider: slurm.SlurmProvider, offer: Offer, monkeypatch: pytest.MonkeyPatch,
    submit: str, name: str,
) -> None:
    monkeypatch.setattr(slurm.uuid, "uuid4", lambda: SimpleNamespace(hex=TOKEN))
    popen = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(slurm.subprocess, "Popen", popen)
    calls = _native(monkeypatch, {"sbatch": "123\n", "squeue": _live(name=name)})
    selected = offer.replace(config={**offer.config, "submit": submit})
    plan = provider.plan(selected, Request.parse("256", "480")).replace(name=name)

    launched = provider.launch(plan)

    assert launched == IDENTITY.replace(name=name)
    argv = (next(argv for argv, _ in calls if argv[0] == "sbatch")
            if submit == "sbatch" else popen.call_args.args[0])
    assert f"--job-name=lc-v1-{name}" in argv
    assert f"--comment={COMMENT}" in argv
    assert len(f"lc-v1-{name}") <= 69


@pytest.mark.parametrize("name", ["", "Upper", "two words", "-leading", "trailing-", "a" * 64])
def test_invalid_name_is_rejected_before_native_submission(
    provider: slurm.SlurmProvider, offer: Offer, monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    calls = _native(monkeypatch, {})
    plan = provider.plan(offer, Request.parse("256", "480")).replace(name=name)
    with pytest.raises(ComputeError, match="cluster names"):
        provider.launch(plan)
    assert calls == []


def test_named_submission_recovers_from_history_when_sbatch_output_is_malformed(
    provider: slurm.SlurmProvider, offer: Offer, monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = "analysis-2026"
    target_uid = os.getuid() + 10000
    monkeypatch.setattr(slurm.uuid, "uuid4", lambda: SimpleNamespace(hex=TOKEN))
    calls = _native(monkeypatch, {
        "id": f"{target_uid}\n",
        "sbatch": "garbled", "squeue": "", "sacct": _history(name=name, uid=target_uid),
    })
    plan = provider.plan(offer, Request.parse("256", "480")).replace(name=name)

    identity = provider.launch(plan)
    assert identity == IDENTITY.replace(name=name)
    assert provider.inspect(identity).phase == "ended"

    accounting = next(argv for argv, _ in calls if argv[0] == "sacct")
    assert f"--name=lc-v1-{name}" in accounting
    assert f"--uid={target_uid}" in accounting
    assert "--format=JobIDRaw,JobName%128,UID,State,Submit,Comment%128" in accounting
    assert sum(argv[0] == "sbatch" for argv, _ in calls) == 1


def test_recovery_does_not_accept_a_different_name_with_the_same_nonce(
    provider: slurm.SlurmProvider, offer: Offer, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slurm.uuid, "uuid4", lambda: SimpleNamespace(hex=TOKEN))
    _native(monkeypatch, {
        "sbatch": "garbled", "squeue": _live(name="other"), "sacct": _history(name="other"),
    })
    plan = provider.plan(offer, Request.parse("256", "480")).replace(name="requested")
    with pytest.raises(ComputeError, match=f"lc-v1-requested and comment token {TOKEN}") as raised:
        provider.launch(plan)
    assert raised.value.submission_token == TOKEN


def test_named_jobs_are_discovered_and_cancelled_from_native_names(
    provider: slurm.SlurmProvider, monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = "a" * 63
    calls = _native(monkeypatch, {
        "squeue": _live(name=name), "scontrol": _control(name=name), "scancel": "",
    })
    snapshot, = provider.discover()
    assert snapshot.identity == IDENTITY.replace(name=name)
    assert snapshot.identity.name == name
    provider.terminate(snapshot.identity)
    assert f"--name=lc-v1-{name}" in calls[-1][0]
    assert "--format=%i|%128j|%U|%T|%128k" in next(
        argv for argv, _ in calls if argv[0] == "squeue"
    )


def test_discovery_and_cancellation_resolve_the_execution_user_once_per_provider(
    provider: slurm.SlurmProvider, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_uid = os.getuid() + 10000
    calls = _native(monkeypatch, {
        "id": f"{target_uid}\n",
        "squeue": _live(uid=target_uid),
        "scontrol": _control(uid=target_uid),
        "scancel": "",
    })
    snapshot, = provider.discover()
    assert snapshot.identity == IDENTITY
    provider.terminate(snapshot.identity)
    assert all(
        f"--user={target_uid}" in argv
        for argv, _ in calls if argv[0] in {"squeue", "scancel"}
    )
    identity_calls = [(argv, kwargs) for argv, kwargs in calls if argv[0] == "id"]
    assert len(identity_calls) == 1
    assert identity_calls[0][0] == ["id", "-u"]
    assert not identity_calls[0][1].get("shell", False)

    slurm.SlurmProvider(provider.connection).discover()
    assert sum(argv[0] == "id" for argv, _ in calls) == 2


@pytest.mark.parametrize("answer", ["", "1000\n1001\n", "١٠٠٠\n", (1, "", "no identity")])
def test_unresolved_execution_user_refuses_cancellation(
    provider: slurm.SlurmProvider, monkeypatch: pytest.MonkeyPatch, answer: Any,
) -> None:
    calls = _native(monkeypatch, {"id": answer})
    with pytest.raises(ComputeError):
        provider.terminate(IDENTITY)
    assert [argv for argv, _ in calls] == [["id", "-u"]]


@pytest.mark.parametrize("comment", [None, "", "(null)", "unrelated|comment", COMMENT + "extra"])
def test_existing_native_name_cannot_be_hidden_from_duplicate_name_checks(
    provider: slurm.SlurmProvider, offer: Offer, monkeypatch: pytest.MonkeyPatch,
    comment: str | None,
) -> None:
    calls = _native(monkeypatch, {
        "squeue": _live(name="analysis", comment=comment),
        "scontrol": _control(name="analysis", comment=comment),
    })
    compute = Compute.__new__(Compute)
    compute.catalog = Catalog(version=1, connections={"nersc": provider.connection}, offers=[offer])
    monkeypatch.setattr(compute, "provider", lambda connection: provider)
    plan = provider.plan(offer, Request.parse("256", "480")).replace(name="analysis")

    expected = "already in use" if comment is None else "discovery is incomplete"
    with pytest.raises(ComputeError, match=expected):
        compute.launch(plan)

    assert all(argv[0] != "sbatch" for argv, _ in calls)


@pytest.mark.parametrize("comment", ["", "(null)", "wrong-kind:token=" + TOKEN, COMMENT + "|extra"])
def test_discovery_refuses_missing_or_malformed_native_submission_identity(
    provider: slurm.SlurmProvider, monkeypatch: pytest.MonkeyPatch, comment: str,
) -> None:
    _native(monkeypatch, {"squeue": _live(comment=comment)})
    with pytest.raises(ComputeError, match="missing or malformed.*Comment"):
        provider.discover()


def test_named_job_identity_survives_terminal_accounting(
    provider: slurm.SlurmProvider, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _native(monkeypatch, {"squeue": "", "sacct": _history(name="finished-analysis")})
    identity = IDENTITY.replace(name="finished-analysis")
    snapshot = provider.inspect(identity)
    assert snapshot.identity == identity
    assert snapshot.phase == "ended"
    assert provider.inspect(identity.replace(name="other")).phase == "unknown"


@pytest.mark.parametrize("comment", ["", "(null)", "missing-token", COMMENT + "|extra"])
def test_accounting_without_verified_comment_cannot_prove_allocation_ended(
    provider: slurm.SlurmProvider, monkeypatch: pytest.MonkeyPatch, comment: str,
) -> None:
    calls = _native(monkeypatch, {"squeue": "", "sacct": _history(comment=comment)})
    observed = provider.inspect(IDENTITY)
    assert observed.phase == "unknown"
    assert "submission token cannot be verified" in observed.reason
    with pytest.raises(ComputeError, match="unknown"):
        provider.terminate(IDENTITY)
    assert all(argv[0] != "scancel" for argv, _ in calls)


def test_accounting_keeps_equal_job_ids_and_names_separate_by_submission_token(
    provider: slurm.SlurmProvider, monkeypatch: pytest.MonkeyPatch,
) -> None:
    older = _history(state="COMPLETED")
    newer = _history(token="a" * 32, state="RUNNING", submitted="2026-09-28T10:00:00")
    _native(monkeypatch, {"squeue": "", "sacct": older + newer})
    assert provider.inspect(IDENTITY).phase == "ended"
    assert provider.inspect(IDENTITY.replace(token="a" * 32)).phase == "unknown"


def test_submission_recovery_uses_nonce_among_historical_name_and_id_duplicates(
    provider: slurm.SlurmProvider, offer: Offer, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slurm.uuid, "uuid4", lambda: SimpleNamespace(hex=TOKEN))
    calls = _native(monkeypatch, {
        "sbatch": "garbled",
        "squeue": _live(token="a" * 32),
        "sacct": _history() + _history(token="a" * 32, submitted="2026-09-28T10:00:00"),
    })
    assert provider.launch(provider.plan(offer, Request.parse("256", "480"))) == IDENTITY
    assert sum(argv[0] == "sbatch" for argv, _ in calls) == 1


def test_historical_recovery_never_infers_a_missing_submission_token(
    provider: slurm.SlurmProvider, offer: Offer, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slurm.uuid, "uuid4", lambda: SimpleNamespace(hex=TOKEN))
    _native(monkeypatch, {"sbatch": "garbled", "squeue": "", "sacct": _history(comment="")})
    with pytest.raises(ComputeError, match="submission outcome is uncertain") as raised:
        provider.launch(provider.plan(offer, Request.parse("256", "480")))
    assert raised.value.submission_token == TOKEN


@pytest.mark.parametrize("name", ["", "Upper", "two words", "-leading", "trailing-", "a" * 64])
def test_discovery_ignores_invalid_native_names(
    provider: slurm.SlurmProvider, monkeypatch: pytest.MonkeyPatch, name: str,
) -> None:
    _native(monkeypatch, {"squeue": _live(name=name)})
    assert provider.discover() == []


def test_live_discovery_uses_native_marker_and_keeps_grant_evidence_honest(
    provider: slurm.SlurmProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unrelated = f"999|another|job|{os.getuid()}|RUNNING\n"
    calls = _native(monkeypatch, {"squeue": _live() + unrelated, "scontrol": _control()})
    snapshots = provider.discover()
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.identity == IDENTITY
    assert snapshot.phase == "active"
    assert snapshot.resources == Resources.from_bytes(cpus=256, memory_bytes=480 * 1024**3)
    assert snapshot.evidence == "requested"
    assert snapshot.ready is None
    assert all("--clusters=perlmutter" in argv for argv, _ in calls if argv[0] != "id")


@pytest.mark.parametrize("cpus,memory", [
    ("256", "0"), ("256", "0G"), ("256", "unknown"), ("0", "480G"),
])
def test_discovery_preserves_allocations_with_unknown_native_resource_evidence(
    provider: slurm.SlurmProvider, monkeypatch: pytest.MonkeyPatch,
    cpus: str, memory: str,
) -> None:
    control = _control().replace("CPUs/Task=256", f"CPUs/Task={cpus}")
    control = control.replace("MinMemoryNode=480G", f"MinMemoryNode={memory}")
    _native(monkeypatch, {"squeue": _live(), "scontrol": control})

    snapshot, = provider.discover()

    assert snapshot.identity == IDENTITY
    assert snapshot.phase == "active"
    assert snapshot.resources is None
    assert snapshot.evidence == "unknown"


def test_native_query_failure_is_not_an_empty_list(
    provider: slurm.SlurmProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    _native(monkeypatch, {"squeue": (1, "", "controller unavailable")})
    with pytest.raises(ComputeError, match="controller unavailable"):
        provider.discover()


@pytest.mark.parametrize("row", [_live(token="a" * 32), _live(uid=999999), _live(name="other")])
def test_reused_id_or_wrong_owner_cannot_be_cancelled(
    provider: slurm.SlurmProvider,
    monkeypatch: pytest.MonkeyPatch,
    row: str,
) -> None:
    calls = _native(monkeypatch, {"squeue": row})
    with pytest.raises(ComputeError, match="ownership, submission token, or name"):
        provider.terminate(IDENTITY)
    assert all(argv[0] != "scancel" for argv, _ in calls)


def test_control_record_revalidated_before_cancellation(
    provider: slurm.SlurmProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _native(monkeypatch, {"squeue": _live(), "scontrol": _control(token="a" * 32)})
    with pytest.raises(ComputeError, match="ownership, submission token, or name"):
        provider.terminate(IDENTITY)
    assert all(argv[0] != "scancel" for argv, _ in calls)


@pytest.mark.parametrize("comment", ["", "changed-token", COMMENT + " extra",
                                     COMMENT + " extra=value"])
def test_changed_or_missing_control_comment_cannot_be_cancelled(
    provider: slurm.SlurmProvider, monkeypatch: pytest.MonkeyPatch, comment: str,
) -> None:
    calls = _native(monkeypatch, {"squeue": _live(), "scontrol": _control(comment=comment)})
    with pytest.raises(ComputeError, match="ownership, submission token, or name"):
        provider.terminate(IDENTITY)
    assert all(argv[0] != "scancel" for argv, _ in calls)


@pytest.mark.parametrize("context", ["perlmutter", ""])
def test_cancel_targets_allocation_with_native_name_and_owner_filters(
    provider: slurm.SlurmProvider, monkeypatch: pytest.MonkeyPatch, context: str,
) -> None:
    provider = slurm.SlurmProvider(provider.connection.replace(context=context))
    calls = _native(monkeypatch, {"squeue": _live(), "scontrol": _control(), "scancel": ""})
    provider.terminate(IDENTITY)
    argv = calls[-1][0]
    assert argv == [
        "scancel",
        "--ctld",
        *(["--clusters=perlmutter"] if context else []),
        f"--user={os.getuid()}",
        f"--name={NAME}",
        "123",
    ]


def test_accounted_terminal_job_down_is_noop(
    provider: slurm.SlurmProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _native(monkeypatch, {"squeue": "", "sacct": _history()})
    assert provider.inspect(IDENTITY).phase == "ended"
    provider.terminate(IDENTITY)
    assert all(argv[0] != "scancel" for argv, _ in calls)
    assert all("--duplicates" in argv for argv, _ in calls if argv[0] == "sacct")


@pytest.mark.parametrize("history", ["", _history(state="RUNNING"), _history(token="a" * 32)])
def test_absence_or_lagging_accounting_is_unknown_not_ended(
    provider: slurm.SlurmProvider,
    monkeypatch: pytest.MonkeyPatch,
    history: str,
) -> None:
    _native(monkeypatch, {"squeue": "", "sacct": history})
    assert provider.inspect(IDENTITY).phase == "unknown"
    with pytest.raises(ComputeError, match="unknown"):
        provider.terminate(IDENTITY)


def test_requeue_never_reads_previous_attempt_credentials(
    provider: slurm.SlurmProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    _metadata(provider)
    _native(monkeypatch, {"scontrol": _control(restarts=1)})
    opened = MagicMock()
    monkeypatch.setattr(slurm, "open_client", opened)
    with pytest.raises(ComputeError):
        with provider.connect(IDENTITY):
            pytest.fail("old credentials must not connect")
    opened.assert_not_called()


@pytest.mark.parametrize("name", [IDENTITY.name, "analysis"])
def test_requeue_keeps_native_identity_but_uses_fresh_attempt(
    provider: slurm.SlurmProvider, monkeypatch: pytest.MonkeyPatch, name: str,
) -> None:
    target_uid = os.getuid() + 10000
    directory = _metadata(provider, restarts=1, uid=target_uid)
    _native(monkeypatch, {
        "id": f"{target_uid}\n",
        "scontrol": _control(restarts=1, name=name, uid=target_uid),
    })
    client = MagicMock()
    client.scheduler_info.return_value = {"workers": {"one": {}, "two": {}}}
    opened = MagicMock(return_value=client)
    monkeypatch.setattr(slurm, "open_client", opened)
    with provider.connect(IDENTITY.replace(name=name)) as connected:
        assert connected is client
    opened.assert_called_once_with(directory, "Scheduler-test", timeout=10)
    client.close.assert_called_once()


@pytest.mark.parametrize("managed_symlink", [False, True])
def test_connect_resolves_only_the_configured_root(
    provider: slurm.SlurmProvider,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    managed_symlink: bool,
) -> None:
    actual = tmp_path / "actual-home"
    actual.mkdir()
    alias = tmp_path / "home"
    alias.symlink_to(actual, target_is_directory=True)
    connection = provider.connection.replace(
        launch={**provider.connection.launch, "connection_root": str(alias / "private")},
    )
    provider = slurm.SlurmProvider(connection)
    directory = _metadata(provider)
    assert directory == actual / "private" / NAMESPACE / f"123-{TOKEN}" / "attempt-0"
    if managed_symlink:
        moved = directory.with_name("moved")
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
    _native(monkeypatch, {"scontrol": _control()})
    client = MagicMock()
    opened = MagicMock(return_value=client)
    monkeypatch.setattr(slurm, "open_client", opened)
    if managed_symlink:
        with pytest.raises(ComputeError, match="not a plain directory"):
            with provider.connect(IDENTITY):
                pytest.fail("managed symlink must not connect")
        opened.assert_not_called()
    else:
        with provider.connect(IDENTITY) as connected:
            assert connected is client
        opened.assert_called_once_with(directory, "Scheduler-test", timeout=10)
        client.close.assert_called_once()


@pytest.mark.parametrize("changes", [{"token": "a" * 32}, {"uid": 999999}, {"restarts": True}])
def test_connect_refuses_wrong_or_untyped_identity_metadata(
    provider: slurm.SlurmProvider,
    monkeypatch: pytest.MonkeyPatch,
    changes: dict[str, Any],
) -> None:
    _metadata(provider, **changes)
    _native(monkeypatch, {"scontrol": _control()})
    with pytest.raises(ComputeError):
        with provider.connect(IDENTITY):
            pytest.fail("invalid identity must not connect")


def test_status_can_observe_a_reachable_but_degraded_scheduler(
    provider: slurm.SlurmProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    _metadata(provider)
    _native(monkeypatch, {"squeue": _live(), "scontrol": _control()})
    client = MagicMock()
    client.scheduler_info.return_value = {"workers": {"one": {}}}
    monkeypatch.setattr(slurm, "open_client", lambda *args, **kwargs: client)
    compute = Compute.__new__(Compute)
    monkeypatch.setattr(compute, "resolve", lambda cluster_id: (provider, IDENTITY))
    observed = compute.status(IDENTITY.encode())
    assert observed.phase == "active"
    assert observed.observation == "reachable"
    assert observed.ready is False
    assert observed.workers == 1
    client.close.assert_called_once()


def test_connect_rechecks_native_attempt_after_tls(
    provider: slurm.SlurmProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    _metadata(provider)
    _native(monkeypatch, {"scontrol": [_control(), _control(restarts=1)]})
    client = MagicMock()
    client.scheduler_info.return_value = {"workers": {"one": {}, "two": {}}}
    monkeypatch.setattr(slurm, "open_client", lambda *args, **kwargs: client)
    with pytest.raises(ComputeError, match="changed during connection"):
        with provider.connect(IDENTITY):
            pytest.fail("changed native attempt must not run tasks")
    client.close.assert_called_once()


def _bootstrap_args(tmp_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        submission=TOKEN,
        namespace=NAMESPACE,
        connection_root=str(tmp_path / "private"),
        scratch_root=str(tmp_path / "scratch"),
        num_nodes=2,
        cpus=1,
        memory_bytes=64 * 1024**2,
        task_slots=1,
        interface=None,
    )


def _bootstrap_env(rank: int) -> dict[str, str]:
    return {
        "SLURM_JOB_ID": "123",
        "SLURM_PROCID": str(rank),
        "SLURM_NTASKS": "2",
        "SLURM_JOB_NUM_NODES": "2",
        "SLURM_CPUS_PER_TASK": "1",
        "SLURM_MEM_PER_NODE": "64",
        "SLURM_RESTART_COUNT": "0",
    }


def test_bootstrap_refuses_mismatched_native_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key, value in _bootstrap_env(0).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("SLURM_NTASKS", "1")
    with pytest.raises(ComputeError, match="frozen allocation envelope"):
        slurm_bootstrap._allocation(_bootstrap_args(tmp_path))


def test_worker_rendezvous_has_a_finite_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key, value in _bootstrap_env(1).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(slurm_bootstrap, "_STARTUP_TIMEOUT", 0)
    with pytest.raises(ComputeError, match="timed out"):
        asyncio.run(slurm_bootstrap.run(_bootstrap_args(tmp_path)))


def test_bootstrap_refuses_a_managed_scratch_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in _bootstrap_env(0).items():
        monkeypatch.setenv(key, value)
    args = _bootstrap_args(tmp_path)
    root = private_directory(Path(args.scratch_root), create=True)
    target = private_directory(tmp_path / "other", create=True)
    (root / TOKEN).symlink_to(target, target_is_directory=True)
    with pytest.raises(ComputeError, match="not a plain directory"):
        asyncio.run(slurm_bootstrap.run(args))


@pytest.mark.parametrize("symlink_roots", [False, True])
def test_standard_bootstrap_starts_scheduler_and_worker_on_rank_zero_and_worker_on_rank_one(
    tmp_path: Path, symlink_roots: bool,
) -> None:
    """Exercise real Dask/TLS subprocesses locally; no native Slurm command is run."""
    args = _bootstrap_args(tmp_path)
    if symlink_roots:
        alias = tmp_path / "home-alias"
        alias.symlink_to(tmp_path, target_is_directory=True)
        args.connection_root = str(alias / "private")
        args.scratch_root = str(alias / "scratch")
    # Both ranks run on this host; CI must not depend on hostname/mDNS resolution.
    loopback = [
        name
        for name, addresses in psutil.net_if_addrs().items()
        if any(address.address == "127.0.0.1" for address in addresses)
    ]
    assert loopback, "the local bootstrap smoke test requires an IPv4 loopback interface"
    args.interface = loopback[0]
    for module in ("signal", "platform"):
        (tmp_path / f"{module}.py").write_text(
            "raise RuntimeError('working directory shadowed Python')\n"
        )
    argv = [sys.executable, "-P", "-m", "lightcone.engine.compute.slurm_bootstrap"]
    for key, value in vars(args).items():
        if value is not None:
            argv += ["--" + key.replace("_", "-"), str(value)]
    connection = Connection(
        namespace=NAMESPACE, provider="slurm", launch={"connection_root": args.connection_root}
    )
    directory = slurm.attempt_directory(connection, IDENTITY, 0)
    processes = []
    client = None
    try:
        for rank in (1, 0):
            with (tmp_path / f"rank-{rank}.log").open("wb") as log:
                processes.append(
                    subprocess.Popen(
                        argv,
                        cwd=tmp_path,
                        env={**os.environ, **_bootstrap_env(rank)},
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                    )
                )
        deadline = time.monotonic() + 20
        while not (directory / "identity.json").exists():
            if time.monotonic() >= deadline or any(
                process.poll() is not None for process in processes
            ):
                logs = "\n".join((tmp_path / f"rank-{rank}.log").read_text() for rank in (0, 1))
                pytest.fail(f"bootstrap failed to publish scheduler: {logs}")
            time.sleep(0.1)
        metadata = read_private_json(directory / "identity.json")
        client = open_client(directory, metadata["scheduler_id"], timeout=5)
        client.wait_for_workers(2, timeout=10)
        workers = client.scheduler_info()["workers"]
        assert {worker["name"] for worker in workers.values()} == {"lightcone-0", "lightcone-1"}
        assert all(worker["nthreads"] == 1 for worker in workers.values())
        assert client.submit(sum, [2, 3]).result(timeout=5) == 5
        assert client.scheduler_info()["address"].startswith("tls://127.0.0.1:")
        assert all(address.startswith("tls://127.0.0.1:") for address in workers)
        assert (
            client.run_on_scheduler(lambda dask_scheduler: dask_scheduler.http_server.address)
            == "127.0.0.1"
        )
        assert set(client.run(lambda dask_worker: dask_worker.http_server.address).values()) == {
            "127.0.0.1"
        }
        client.shutdown()
        client = None
        for process in processes:
            assert process.wait(timeout=10) == 0
    finally:
        if client is not None:
            client.close(timeout=3)
        for process in processes:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
