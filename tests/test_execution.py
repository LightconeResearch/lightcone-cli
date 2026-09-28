"""Ordinary Dask tasks must not repeat effects or outlive their invocation silently."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from distributed import Client, LocalCluster, get_worker

from lightcone.engine import execution
from lightcone.engine.worker import TaskResult

_RESOURCES = {"CPU": 1.0, "MEMORY": 1.0}


@pytest.fixture
def execution_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[Client]:
    monkeypatch.setattr(execution, "_HEARTBEAT", 0.05)
    monkeypatch.setattr(execution, "_RPC_TIMEOUT", 2.0)
    monkeypatch.setattr(execution, "_STOP_TIMEOUT", 0.75)
    with LocalCluster(
        n_workers=2, threads_per_worker=1, processes=False,
        dashboard_address=None, memory_limit=0, resources=_RESOURCES,
    ) as cluster, Client(cluster) as client:
        yield client


def _wait_for(path: Path) -> None:
    deadline = time.monotonic() + 5
    while not path.exists():
        if time.monotonic() > deadline:
            pytest.fail(f"worker did not create {path.name}")
        time.sleep(0.01)


def _effect(path: Path) -> TaskResult:
    with path.open("a") as stream:
        stream.write("executed\n")
    return TaskResult(("universe", "output"), "ok", notes=(get_worker().address,))


def _wait_for_release(started: Path, release: Path) -> str:
    started.touch()
    deadline = time.monotonic() + 5
    while not release.exists():
        if time.monotonic() > deadline:
            raise AssertionError("test did not release its running task")
        time.sleep(0.01)
    return "original"


def _cooperate(started: Path, stopped: Path) -> None:
    started.touch()
    deadline = time.monotonic() + 5
    while not execution.cancelled():
        if time.monotonic() > deadline:
            raise AssertionError("task did not observe its invocation's cancellation")
        time.sleep(0.01)
    stopped.touch()
    execution.check_cancelled()


def _cooperating_effect(started: Path, stopped: Path) -> None:
    with started.open("a") as stream:
        stream.write(get_worker().address + "\n")
    _cooperate(started, stopped)


def _remove_worker(client: Client, address: str) -> None:
    worker = next(worker for worker in client.cluster.workers.values()
                  if worker.address == address)
    # close(), unlike close_gracefully(), discards this worker's task data.
    # Keeping its executor alive also models a partitioned task that can still
    # write even though the scheduler has reassigned its Dask key elsewhere.
    client.cluster.sync(worker.close, executor_wait=False, timeout=0.5)


def _raise(error: Exception) -> None:
    raise error


def _records(*, dask_scheduler: Any) -> dict[str, Any]:
    return dask_scheduler.extensions.get("lightcone-executions", {})


def _lose_state(*, dask_scheduler: Any) -> None:
    dask_scheduler.extensions.pop("lightcone-executions", None)


def test_completed_task_replay_on_another_worker_returns_its_original_receipt(
    execution_client: Client, tmp_path: Path,
) -> None:
    effects = tmp_path / "effects"
    with execution.invocation(execution_client) as run:
        assert not run.stopped
        original = run.submit(_effect, effects, key="recipe", resources=_RESOURCES).result()
        other = next(address for address in execution_client.scheduler_info()["workers"]
                     if address != original.notes[0])
        replay = execution_client.submit(
            execution._call, run.id, "recipe", _effect, effects,
            key=f"replay-{uuid4().hex}", workers=[other], allow_other_workers=False,
            pure=False, resources=_RESOURCES,
        ).result()
        assert replay == original
        assert effects.read_text() == "executed\n"
    assert run.stopped
    assert run.id not in execution_client.run_on_scheduler(_records)


def test_worker_loss_recomputes_the_dask_future_without_repeating_completed_effects(
    execution_client: Client, tmp_path: Path,
) -> None:
    effects = tmp_path / "effects"
    with execution.invocation(execution_client) as run:
        future = run.submit(_effect, effects, key="recipe", resources=_RESOURCES)
        original = future.result(timeout=3)
        lost_worker = original.notes[0]
        _remove_worker(execution_client, lost_worker)
        deadline = time.monotonic() + 3
        while True:
            holders = execution_client.who_has([future])[future.key]
            if holders and lost_worker not in holders:
                break
            assert time.monotonic() < deadline, "Dask did not recompute the lost result"
            time.sleep(0.01)
        assert future.result(timeout=3) == original
        assert effects.read_text() == "executed\n"


def test_worker_loss_cannot_replay_effects_while_the_original_execution_still_runs(
    execution_client: Client, tmp_path: Path,
) -> None:
    started, stopped = tmp_path / "started", tmp_path / "stopped"
    with execution.invocation(execution_client) as run:
        future = run.submit(
            _cooperating_effect, started, stopped, key="recipe", resources=_RESOURCES,
        )
        _wait_for(started)
        lost_worker = started.read_text().strip()
        _remove_worker(execution_client, lost_worker)
        with pytest.raises(execution.ExecutionUncertain, match="previous attempt"):
            future.result(timeout=3)
        _wait_for(stopped)
    assert started.read_text().splitlines() == [lost_worker]
    assert run.id not in execution_client.run_on_scheduler(_records)


def test_duplicate_running_attempt_cannot_execute_or_finish_the_original_claim(
    execution_client: Client, tmp_path: Path,
) -> None:
    started, release, duplicate_effect = (
        tmp_path / "started", tmp_path / "release", tmp_path / "duplicate"
    )
    with execution.invocation(execution_client) as run:
        original = run.submit(
            _wait_for_release, started, release, key="recipe", resources=_RESOURCES,
        )
        try:
            _wait_for(started)
            duplicate = execution_client.submit(
                execution._call, run.id, "recipe", _effect, duplicate_effect,
                key=f"duplicate-{uuid4().hex}", pure=False, resources=_RESOURCES,
            )
            with pytest.raises(execution.ExecutionUncertain, match="previous attempt"):
                duplicate.result(timeout=3)
            assert execution._rpc(execution_client, run.id, "pending") == ["recipe"]
            assert not duplicate_effect.exists()
        finally:
            release.touch()
        try:
            assert original.result(timeout=3) == "original"
        except execution.ExecutionCancelled:
            # Rejecting the ambiguous duplicate may revoke the invocation before
            # the original completes. Only that original may acknowledge its stop.
            pass
        assert execution._rpc(execution_client, run.id, "pending") == []


def test_late_dispatch_after_invocation_exit_cannot_recreate_authorization(
    execution_client: Client, tmp_path: Path,
) -> None:
    effects = tmp_path / "effects"
    with execution.invocation(execution_client) as run:
        pass
    late = execution_client.submit(
        execution._call, run.id, "late", _effect, effects, pure=False,
    )
    with pytest.raises(execution.ExecutionUncertain, match="no longer registered"):
        late.result(timeout=3)
    assert not effects.exists()
    assert run.id not in execution_client.run_on_scheduler(_records)


def test_missing_scheduler_state_refuses_both_replay_and_unstarted_tasks(
    execution_client: Client, tmp_path: Path,
) -> None:
    effects = tmp_path / "effects"
    with pytest.raises(execution.ExecutionUncertain, match="confirm execution stopped"):
        with execution.invocation(execution_client) as run:
            run.submit(_effect, effects, key="recipe", resources=_RESOURCES).result()
            execution_client.run_on_scheduler(_lose_state)
            for key in ("recipe", "new-recipe"):
                future = execution_client.submit(
                    execution._call, run.id, key, _effect, effects,
                    key=f"lost-state-{uuid4().hex}", pure=False,
                )
                with pytest.raises(execution.ExecutionUncertain, match="no longer registered"):
                    future.result(timeout=3)
    assert effects.read_text() == "executed\n"
    assert not run.stopped


def test_missing_scheduler_state_stops_running_work_without_claiming_confirmed_cleanup(
    execution_client: Client, tmp_path: Path,
) -> None:
    started, stopped = tmp_path / "started", tmp_path / "stopped"
    with pytest.raises(execution.ExecutionUncertain, match="confirm execution stopped"):
        with execution.invocation(execution_client) as run:
            future = run.submit(
                _cooperate, started, stopped, key="recipe", resources=_RESOURCES,
            )
            _wait_for(started)
            execution_client.run_on_scheduler(_lose_state)
            with pytest.raises(execution.ExecutionCancelled, match="cancelled"):
                future.result(timeout=3)
    assert stopped.exists()


def test_lost_claim_response_never_starts_the_recipe_and_retains_its_unresolved_claim(
    execution_client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    effects = tmp_path / "effects"
    request = execution._rpc

    def lose_claim_response(*args: Any, **kwargs: Any) -> Any:
        result = request(*args, **kwargs)
        if args[2] == "claim":
            raise TimeoutError("claim accepted but reply lost")
        return result

    monkeypatch.setattr(execution, "_rpc", lose_claim_response)
    monkeypatch.setattr(execution, "_STOP_TIMEOUT", 0.1)
    with pytest.raises(execution.ExecutionUncertain, match="unconfirmed tasks: recipe"):
        with execution.invocation(execution_client) as run:
            future = run.submit(_effect, effects, key="recipe", resources=_RESOURCES)
            with pytest.raises(TimeoutError, match="reply lost"):
                future.result(timeout=3)
            assert execution._rpc(execution_client, run.id, "pending") == ["recipe"]
    assert not effects.exists()


@pytest.mark.parametrize("loss", ["lease", "client"])
def test_expired_or_disconnected_invocations_cannot_be_revived_by_heartbeat(loss: str) -> None:
    scheduler = SimpleNamespace(extensions={}, clients={"driver": object()})
    execution._state("invocation", "register", value="driver", dask_scheduler=scheduler)
    record = scheduler.extensions["lightcone-executions"]["invocation"]
    if loss == "lease":
        record["deadline"] = 0
    else:
        scheduler.clients.clear()
    assert not execution._state("invocation", "heartbeat", dask_scheduler=scheduler)
    # Neither a fresh connection nor a late heartbeat can resurrect permission.
    scheduler.clients["driver"] = object()
    record["deadline"] = time.monotonic() + 60
    assert not execution._state("invocation", "heartbeat", dask_scheduler=scheduler)
    with pytest.raises(execution.ExecutionCancelled, match="no longer active"):
        execution._state("invocation", "claim", "recipe", dask_scheduler=scheduler)


def test_invocation_exit_cancels_the_future_and_waits_for_task_cooperation(
    execution_client: Client, tmp_path: Path,
) -> None:
    started, stopped = tmp_path / "started", tmp_path / "stopped"
    with execution.invocation(execution_client) as run:
        future = run.submit(_cooperate, started, stopped, key="recipe", resources=_RESOURCES)
        _wait_for(started)
    assert future.cancelled()
    assert stopped.exists()
    assert run.stopped
    assert run.id not in execution_client.run_on_scheduler(_records)


def test_confirmed_cleanup_survives_an_error_in_the_invoking_driver(
    execution_client: Client, tmp_path: Path,
) -> None:
    started, stopped = tmp_path / "started", tmp_path / "stopped"
    with pytest.raises(ValueError, match="driver failed to commit"):
        with execution.invocation(execution_client) as run:
            run.submit(_cooperate, started, stopped, key="recipe", resources=_RESOURCES)
            _wait_for(started)
            raise ValueError("driver failed to commit")
    assert stopped.exists()
    assert run.stopped


def test_driver_disconnect_revokes_execution_even_while_an_observer_remains_connected(
    execution_client: Client, tmp_path: Path,
) -> None:
    started, stopped = tmp_path / "started", tmp_path / "stopped"
    with Client(execution_client.scheduler.address, set_as_default=False) as owner:
        run = execution.Invocation(owner)
        execution._rpc(owner, run.id, "register", value=owner.id)
        run.submit(_cooperate, started, stopped, key="recipe", resources=_RESOURCES)
        _wait_for(started)
    _wait_for(stopped)
    assert not execution._rpc(execution_client, run.id, "heartbeat")
    deadline = time.monotonic() + 3
    while execution._rpc(execution_client, run.id, "pending"):
        assert time.monotonic() < deadline, "task never acknowledged that it stopped"
        time.sleep(0.01)
    execution._rpc(execution_client, run.id, "forget")


def test_a_returned_failed_recipe_result_is_cached_without_rerunning(
    execution_client: Client,
) -> None:
    failed = TaskResult(("universe", "output"), "failed", reason="recipe exited 2")
    with execution.invocation(execution_client) as run:
        assert run.submit(lambda: failed, key="recipe", resources=_RESOURCES).result() == failed
        replay = execution_client.submit(
            execution._call, run.id, "recipe", _raise, AssertionError("must not execute"),
            pure=False,
        )
        assert replay.result(timeout=3) == failed


def test_function_exception_confirms_stop_but_does_not_authorize_reexecution(
    execution_client: Client,
) -> None:
    with execution.invocation(execution_client) as run:
        future = run.submit(_raise, ValueError("recipe failed"), key="recipe", resources=_RESOURCES)
        with pytest.raises(ValueError, match="recipe failed"):
            future.result(timeout=3)
        assert execution._rpc(execution_client, run.id, "pending") == []
        replay = execution_client.submit(
            execution._call, run.id, "recipe", _raise, AssertionError("must not execute"),
            pure=False,
        )
        with pytest.raises(execution.ExecutionUncertain, match="previous attempt"):
            replay.result(timeout=3)


def test_uncertain_task_remains_unresolved_after_context_exit(
    execution_client: Client, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(execution, "_STOP_TIMEOUT", 0.1)
    with pytest.raises(execution.ExecutionUncertain, match="unconfirmed tasks: recipe"):
        with execution.invocation(execution_client) as run:
            future = run.submit(
                _raise, execution.ExecutionUncertain("container may still be alive"),
                key="recipe", resources=_RESOURCES,
            )
            with pytest.raises(execution.ExecutionUncertain, match="container may still"):
                future.result(timeout=3)
            assert execution._rpc(execution_client, run.id, "pending") == ["recipe"]
    assert execution._rpc(execution_client, run.id, "pending") == ["recipe"]
    assert not execution._rpc(execution_client, run.id, "heartbeat")
    assert not run.stopped


def test_uncertain_cleanup_prevents_independent_tasks_from_using_released_dask_resources(
    execution_client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(execution, "_STOP_TIMEOUT", 0.1)
    effects = tmp_path / "effects"
    with pytest.raises(execution.ExecutionUncertain, match="unconfirmed tasks: first"):
        with execution.invocation(execution_client) as run:
            first = run.submit(
                _raise, execution.ExecutionUncertain("container may still consume memory"),
                key="first", resources=_RESOURCES,
            )
            with pytest.raises(execution.ExecutionUncertain, match="container may still"):
                first.result(timeout=3)
            # Dask has released the first task's reservations, but its external
            # work may survive. Authorization must close before another task runs.
            later = run.submit(_effect, effects, key="later", resources=_RESOURCES)
            error = later.exception(timeout=3)
    assert isinstance(error, execution.ExecutionCancelled)
    assert not effects.exists()
