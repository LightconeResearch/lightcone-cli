"""One scheduler claim prevents Dask from replaying a side-effecting task."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from distributed import Client, LocalCluster, get_worker

from lightcone.engine import execution
from lightcone.engine.project import ProjectError


@pytest.fixture
def execution_client() -> Iterator[Client]:
    with LocalCluster(
        n_workers=2, threads_per_worker=1, processes=False,
        dashboard_address=None, memory_limit=0,
    ) as cluster, Client(cluster) as client:
        yield client


def _wait_until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 10
    while not condition():
        assert time.monotonic() < deadline, "Dask task did not reach the expected state"
        time.sleep(0.01)


def _effect(path: Path, fail: bool = False) -> str:
    address = get_worker().address
    with path.open("a") as stream:
        stream.write(address + "\n")
    if fail:
        raise ValueError("recipe failed after writing")
    return address


def _running_effect(path: Path, release: Path, finished: Path) -> None:
    _effect(path)
    # Publish readiness after the append is closed, including on slow CI hosts.
    path.with_suffix(".ready").touch()
    try:
        _wait_until(release.exists)
    finally:
        finished.touch()


def _remove_worker(client: Client, address: str) -> None:
    worker = next(worker for worker in client.cluster.workers.values()
                  if worker.address == address)
    # Discard its data while keeping an executing thread alive: scheduler loss
    # does not itself prove that the original command has stopped writing.
    client.cluster.sync(worker.close, executor_wait=False, timeout=1)


def _replay(client: Client, invocation: str, task: str, path: Path) -> Any:
    return client.submit(
        execution._call, invocation, task, _effect, path,
        key=f"replay-{uuid4().hex}", pure=False, retries=0,
    )


def _lose_state(*, dask_scheduler: Any) -> None:
    dask_scheduler.extensions.pop("lightcone-executions", None)


def test_completed_worker_loss_refuses_recomputation(
    execution_client: Client, tmp_path: Path,
) -> None:
    effects = tmp_path / "effects"
    with execution.invocation(execution_client) as run:
        future = run.submit(_effect, effects, key="recipe")
        address = future.result(timeout=10)
        _remove_worker(execution_client, address)
        # A previously finished Future can still contain its old result until
        # the scheduler tells this client that recomputation has failed.
        _wait_until(lambda: future.status == "error")
        with pytest.raises(ProjectError, match="already claimed"):
            future.result(timeout=10)
    assert effects.read_text().splitlines() == [address]


def test_running_worker_loss_refuses_replay_while_original_can_still_write(
    execution_client: Client, tmp_path: Path,
) -> None:
    effects, release, finished = (tmp_path / name for name in ("effects", "release", "finished"))
    with execution.invocation(execution_client) as run:
        future = run.submit(_running_effect, effects, release, finished, key="recipe")
        try:
            _wait_until(effects.with_suffix(".ready").exists)
            address = effects.read_text().strip()
            _remove_worker(execution_client, address)
            with pytest.raises(ProjectError, match="already claimed"):
                future.result(timeout=10)
            assert not finished.exists()
            assert effects.read_text().splitlines() == [address]
        finally:
            release.touch()
            _wait_until(finished.exists)


def test_task_failure_does_not_release_its_claim(
    execution_client: Client, tmp_path: Path,
) -> None:
    effects = tmp_path / "effects"
    with execution.invocation(execution_client) as run:
        with pytest.raises(ValueError, match="recipe failed"):
            run.submit(_effect, effects, True, key="recipe").result(timeout=10)
        with pytest.raises(ProjectError, match="already claimed"):
            _replay(execution_client, run.id, "recipe", effects).result(timeout=10)
    assert len(effects.read_text().splitlines()) == 1


def test_dask_forgetting_and_recreating_a_task_does_not_forget_its_claim(
    execution_client: Client, tmp_path: Path,
) -> None:
    effects = tmp_path / "effects"
    with execution.invocation(execution_client) as run:
        original = run.submit(_effect, effects, key="recipe")
        original.result(timeout=10)
        key = original.key
        original.release()
        _wait_until(lambda: execution_client.run_on_scheduler(
            lambda dask_scheduler: key not in dask_scheduler.tasks,
        ))
        with pytest.raises(ProjectError, match="already claimed"):
            run.submit(_effect, effects, key="recipe").result(timeout=10)
    assert len(effects.read_text().splitlines()) == 1


@pytest.mark.parametrize(
    "state_lost", [False, True], ids=["context-exited", "scheduler-state-lost"],
)
def test_missing_invocation_refuses_replays_and_late_new_tasks(
    execution_client: Client, tmp_path: Path, state_lost: bool,
) -> None:
    effects = tmp_path / "effects"
    with execution.invocation(execution_client) as run:
        run.submit(_effect, effects, key="recipe").result(timeout=10)
        if state_lost:
            execution_client.run_on_scheduler(_lose_state)
            with pytest.raises(ProjectError):
                _replay(execution_client, run.id, "new-recipe", effects).result(timeout=10)
    for key in ("recipe", "new-recipe"):
        with pytest.raises(ProjectError):
            _replay(execution_client, run.id, key, effects).result(timeout=10)
    assert len(effects.read_text().splitlines()) == 1


def test_lost_claim_reply_never_executes_and_cannot_be_retried(
    execution_client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    effects = tmp_path / "effects"
    request = execution._rpc

    def lose_reply(client: Any, function: Any, *args: Any) -> Any:
        result = request(client, function, *args)
        if function is execution._claim:
            raise ProjectError("claim accepted but reply lost")
        return result

    with execution.invocation(execution_client) as run:
        with monkeypatch.context() as patch:
            patch.setattr(execution, "_rpc", lose_reply)
            with pytest.raises(ProjectError, match="reply lost"):
                run.submit(_effect, effects, key="recipe").result(timeout=10)
        with pytest.raises(ProjectError, match="already claimed"):
            _replay(execution_client, run.id, "recipe", effects).result(timeout=10)
    assert not effects.exists()


def test_claims_are_scoped_to_one_invocation(
    execution_client: Client, tmp_path: Path,
) -> None:
    effects = tmp_path / "effects"
    for _ in range(2):
        with execution.invocation(execution_client) as run:
            run.submit(_effect, effects, key="recipe").result(timeout=10)
    assert len(effects.read_text().splitlines()) == 2


def test_disconnected_owner_cannot_admit_tasks_and_registration_prunes_stale_records() -> None:
    scheduler = SimpleNamespace(extensions={}, clients={"owner": object()})
    execution._register("first", "owner", dask_scheduler=scheduler)
    execution._claim("first", "recipe", dask_scheduler=scheduler)
    with pytest.raises(ProjectError):
        execution._register("first", "owner", dask_scheduler=scheduler)
    with pytest.raises(ProjectError, match="already claimed"):
        execution._claim("first", "recipe", dask_scheduler=scheduler)
    scheduler.clients.clear()
    with pytest.raises(ProjectError):
        execution._claim("first", "late-recipe", dask_scheduler=scheduler)
    scheduler.clients["new-owner"] = object()
    execution._register("second", "new-owner", dask_scheduler=scheduler)
    assert "first" not in scheduler.extensions["lightcone-executions"]
    execution._claim("second", "recipe", dask_scheduler=scheduler)


def test_unavailable_scheduler_is_reported_as_a_project_error() -> None:
    def offline(*args: Any, **kwargs: Any) -> None:
        raise OSError("connection lost")

    client = SimpleNamespace(sync=offline, run_on_scheduler=None)
    with pytest.raises(ProjectError, match="Dask execution guard"):
        execution._rpc(client, execution._claim, "invocation", "recipe")


def test_forget_failure_does_not_hide_a_successful_result(
    execution_client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = execution._rpc

    def fail_forget(client: Any, function: Any, *args: Any) -> Any:
        if function is execution._forget:
            raise ProjectError("scheduler disconnected during cleanup")
        return request(client, function, *args)

    monkeypatch.setattr(execution, "_rpc", fail_forget)
    with execution.invocation(execution_client) as run:
        result = run.submit(_effect, tmp_path / "effects", key="recipe").result(timeout=10)
    assert result
