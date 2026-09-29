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


@pytest.fixture
def execution_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[Client]:
    monkeypatch.setattr(execution, "_HEARTBEAT", 0.05)
    monkeypatch.setattr(execution, "_RPC_TIMEOUT", 2.0)
    monkeypatch.setattr(execution, "_STOP_TIMEOUT", 0.75)
    with LocalCluster(
        n_workers=2, threads_per_worker=1, processes=False,
        dashboard_address=None, memory_limit=0,
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
    # Publish readiness after closing the append, never during file creation.
    started.with_suffix(".ready").touch()
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


def _stay_authorized(seconds: float) -> str:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        execution.check_cancelled()
        time.sleep(0.01)
    return "completed"


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
        original = run.submit(_effect, effects, key="recipe").result()
        other = next(address for address in execution_client.scheduler_info()["workers"]
                     if address != original.notes[0])
        replay = execution_client.submit(
            execution._call, run.id, "recipe", _effect, effects,
            key=f"replay-{uuid4().hex}", workers=[other], allow_other_workers=False,
            pure=False,
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
        future = run.submit(_effect, effects, key="recipe")
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
            _cooperating_effect, started, stopped, key="recipe",
        )
        _wait_for(started.with_suffix(".ready"))
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
            _wait_for_release, started, release, key="recipe",
        )
        try:
            _wait_for(started)
            duplicate = execution_client.submit(
                execution._call, run.id, "recipe", _effect, duplicate_effect,
                key=f"duplicate-{uuid4().hex}", pure=False,
            )
            with pytest.raises(execution.ExecutionUncertain, match="previous attempt"):
                duplicate.result(timeout=3)
            assert execution._rpc(execution_client, run.id, "pending").running == ("recipe",)
            assert not duplicate_effect.exists()
        finally:
            release.touch()
        try:
            assert original.result(timeout=3) == "original"
        except execution.ExecutionCancelled:
            # Rejecting the ambiguous duplicate may revoke the invocation before
            # the original completes. Only that original may acknowledge its stop.
            pass
        assert execution._rpc(execution_client, run.id, "pending").running == ()


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
    with execution.invocation(execution_client) as run:
        run.submit(_effect, effects, key="recipe").result()
        execution_client.run_on_scheduler(_lose_state)
        for key in ("recipe", "new-recipe"):
            future = execution_client.submit(
                execution._call, run.id, key, _effect, effects,
                key=f"lost-state-{uuid4().hex}", pure=False,
            )
            with pytest.raises(execution.ExecutionUncertain, match="no longer registered"):
                future.result(timeout=3)
    assert effects.read_text() == "executed\n"
    assert run.stopped  # The only admitted task returned its confirmed completion.


def test_missing_scheduler_state_stops_running_work_without_claiming_confirmed_cleanup(
    execution_client: Client, tmp_path: Path,
) -> None:
    started, stopped = tmp_path / "started", tmp_path / "stopped"
    with pytest.raises(execution.ExecutionUncertain, match="confirm execution stopped"):
        with execution.invocation(execution_client) as run:
            future = run.submit(
                _cooperate, started, stopped, key="recipe",
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
            future = run.submit(_effect, effects, key="recipe")
            with pytest.raises(TimeoutError, match="reply lost"):
                future.result(timeout=3)
            assert execution._rpc(execution_client, run.id, "pending").running == ("recipe",)
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
        future = run.submit(_cooperate, started, stopped, key="recipe")
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
            run.submit(_cooperate, started, stopped, key="recipe")
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
        run.submit(_cooperate, started, stopped, key="recipe")
        _wait_for(started)
    _wait_for(stopped)
    assert not execution._rpc(execution_client, run.id, "heartbeat")
    deadline = time.monotonic() + 3
    while execution._rpc(execution_client, run.id, "pending").running:
        assert time.monotonic() < deadline, "task never acknowledged that it stopped"
        time.sleep(0.01)
    execution._rpc(execution_client, run.id, "forget")


def test_a_returned_failed_recipe_result_is_cached_without_rerunning(
    execution_client: Client,
) -> None:
    failed = TaskResult(("universe", "output"), "failed", reason="recipe exited 2")
    with execution.invocation(execution_client) as run:
        assert run.submit(lambda: failed, key="recipe").result() == failed
        replay = execution_client.submit(
            execution._call, run.id, "recipe", _raise, AssertionError("must not execute"),
            pure=False,
        )
        assert replay.result(timeout=3) == failed


def test_function_exception_confirms_stop_but_does_not_authorize_reexecution(
    execution_client: Client,
) -> None:
    with execution.invocation(execution_client) as run:
        future = run.submit(_raise, ValueError("recipe failed"), key="recipe")
        with pytest.raises(ValueError, match="recipe failed"):
            future.result(timeout=3)
        assert execution._rpc(execution_client, run.id, "pending").running == ()
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
                key="recipe",
            )
            with pytest.raises(execution.ExecutionUncertain, match="container may still"):
                future.result(timeout=3)
            assert execution._rpc(execution_client, run.id, "pending").uncertain == ("recipe",)
    assert execution._rpc(execution_client, run.id, "pending").uncertain == ("recipe",)
    assert not execution._rpc(execution_client, run.id, "heartbeat")
    assert not run.stopped


def test_uncertain_cleanup_prevents_later_tasks_from_starting(
    execution_client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(execution, "_STOP_TIMEOUT", 0.1)
    effects = tmp_path / "effects"
    with pytest.raises(execution.ExecutionUncertain, match="unconfirmed tasks: first"):
        with execution.invocation(execution_client) as run:
            first = run.submit(
                _raise, execution.ExecutionUncertain("container may still consume memory"),
                key="first",
            )
            with pytest.raises(execution.ExecutionUncertain, match="container may still"):
                first.result(timeout=3)
            # Dask reported the first task's error, but its external work may
            # survive. Authorization must close before another task runs.
            later = run.submit(_effect, effects, key="later")
            error = later.exception(timeout=3)
    assert isinstance(error, execution.ExecutionCancelled)
    assert not effects.exists()


def test_transient_heartbeat_and_monitor_failures_preserve_the_confirmed_lease(
    execution_client: Client, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(execution, "_LEASE", 0.4)
    request = execution._rpc
    calls = {"heartbeat": 0, "active": 0}

    def fail_once(*args: Any, **kwargs: Any) -> Any:
        operation = args[2]
        if operation in calls:
            calls[operation] += 1
            if calls[operation] == 1:
                raise TimeoutError("temporary scheduler RPC failure")
        return request(*args, **kwargs)

    monkeypatch.setattr(execution, "_rpc", fail_once)
    with execution.invocation(execution_client) as run:
        future = run.submit(_stay_authorized, 0.8, key="recipe")
        assert future.result(timeout=3) == "completed"
    assert calls["heartbeat"] > 2
    assert calls["active"] > 2
    assert run.stopped


def test_unreachable_monitor_expires_its_last_grant_even_when_driver_heartbeats_continue(
    execution_client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(execution, "_LEASE", 0.3)
    request = execution._rpc
    failed_polls = 0

    def lose_monitor(*args: Any, **kwargs: Any) -> Any:
        nonlocal failed_polls
        if args[2] == "active":
            failed_polls += 1
            raise TimeoutError("worker cannot reach scheduler")
        return request(*args, **kwargs)

    monkeypatch.setattr(execution, "_rpc", lose_monitor)
    started, stopped = tmp_path / "started", tmp_path / "stopped"
    with execution.invocation(execution_client) as run:
        future = run.submit(_cooperate, started, stopped, key="recipe")
        with pytest.raises(execution.ExecutionCancelled, match="cancelled"):
            future.result(timeout=3)
    assert failed_polls > 1  # A single failed RPC did not revoke valid authorization.
    assert stopped.exists()


@pytest.mark.parametrize("operation", ["revoke", "forget"])
def test_completed_results_survive_metadata_cleanup_rpc_failure(
    execution_client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    request = execution._rpc
    effects = tmp_path / "effects"

    def lose_cleanup(*args: Any, **kwargs: Any) -> Any:
        if args[2] == operation:
            raise TimeoutError("scheduler unavailable during metadata cleanup")
        return request(*args, **kwargs)

    with execution.invocation(execution_client) as run:
        result = run.submit(_effect, effects, key="recipe").result(timeout=3)
        monkeypatch.setattr(execution, "_rpc", lose_cleanup)
    assert result.status == "ok"
    assert effects.read_text() == "executed\n"
    assert run.stopped


def test_caught_submit_failure_cannot_manufacture_completion_from_an_empty_future_list(
    execution_client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, submit = execution._rpc, execution_client.submit
    effects = tmp_path / "effects"

    def accept_then_fail(*args: Any, **kwargs: Any) -> Any:
        future = submit(*args, **kwargs)
        future.result(timeout=3)
        raise TimeoutError("submission accepted but its handle was lost")

    def lose_revoke(*args: Any, **kwargs: Any) -> Any:
        if args[2] == "revoke":
            raise TimeoutError("cannot query accepted submissions")
        return request(*args, **kwargs)

    monkeypatch.setattr(execution_client, "submit", accept_then_fail)
    monkeypatch.setattr(execution, "_rpc", lose_revoke)
    with pytest.raises(execution.ExecutionUncertain, match="confirm execution stopped"):
        with execution.invocation(execution_client) as run:
            with pytest.raises(TimeoutError, match="handle was lost"):
                run.submit(_effect, effects, key="recipe")
            assert not run.futures
    assert effects.read_text() == "executed\n"
    assert not run.stopped


@pytest.mark.parametrize("submit", [False, True])
def test_positive_task_completion_preserves_driver_error_during_cleanup_outage(
    execution_client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, submit: bool,
) -> None:
    request = execution._rpc
    failure = ValueError("driver could not commit")

    def lose_revoke(*args: Any, **kwargs: Any) -> Any:
        if args[2] == "revoke":
            raise TimeoutError("scheduler unavailable during cleanup")
        return request(*args, **kwargs)

    with pytest.raises(ValueError, match="could not commit") as caught:
        with execution.invocation(execution_client) as run:
            if submit:
                run.submit(
                    _effect, tmp_path / "effects", key="recipe",
                ).result()
            monkeypatch.setattr(execution, "_rpc", lose_revoke)
            raise failure
    assert caught.value is failure
    assert run.stopped


def test_confirmed_revocation_and_drain_remain_valid_when_forgetting_receipts_fails(
    execution_client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = execution._rpc
    started, stopped = tmp_path / "started", tmp_path / "stopped"
    interruption = KeyboardInterrupt()

    def lose_forget(*args: Any, **kwargs: Any) -> Any:
        if args[2] == "forget":
            raise TimeoutError("receipt cleanup reply lost")
        return request(*args, **kwargs)

    monkeypatch.setattr(execution, "_rpc", lose_forget)
    with pytest.raises(execution.ExecutionInterrupted) as caught:
        with execution.invocation(execution_client) as run:
            run.submit(_cooperate, started, stopped, key="recipe")
            _wait_for(started)
            raise interruption
    assert caught.value.__cause__ is interruption
    assert stopped.exists()
    assert run.stopped


def test_known_terminal_uncertainty_is_reported_without_polling_for_a_different_result(
    execution_client: Client, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = execution._rpc

    def refuse_polling(*args: Any, **kwargs: Any) -> Any:
        if args[2] == "pending":
            pytest.fail("a terminal uncertain receipt cannot become confirmed by waiting")
        return request(*args, **kwargs)

    monkeypatch.setattr(execution, "_rpc", refuse_polling)
    with pytest.raises(execution.ExecutionUncertain, match="unconfirmed tasks: recipe"):
        with execution.invocation(execution_client) as run:
            future = run.submit(
                _raise, execution.ExecutionUncertain("container still unaccounted for"),
                key="recipe",
            )
            with pytest.raises(execution.ExecutionUncertain, match="unaccounted"):
                future.result(timeout=3)
    assert not run.stopped
