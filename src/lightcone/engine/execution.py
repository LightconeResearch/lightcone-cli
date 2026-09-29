"""Refuse repeated execution of side-effecting tasks in the existing Dask scheduler."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from lightcone.engine.project import ProjectError


def _register(invocation: str, owner: str, *, dask_scheduler: Any) -> None:
    records = dask_scheduler.extensions.setdefault("lightcone-executions", {})
    if invocation in records:
        raise ProjectError("execution is already registered")
    # Collect abandoned invocations using Dask's own client membership, without
    # a background service or a second liveness protocol.
    for key, record in list(records.items()):
        if record["client"] not in dask_scheduler.clients:
            del records[key]
    records[invocation] = {"client": owner, "tasks": set()}


def _claim(invocation: str, task: str, *, dask_scheduler: Any) -> None:
    records = dask_scheduler.extensions.get("lightcone-executions", {})
    record = records.get(invocation)
    if record is None or record["client"] not in dask_scheduler.clients:
        raise ProjectError("execution is no longer registered or its client disconnected")
    # This synchronous callback runs atomically on the scheduler's event loop.
    # Claims survive task failure, worker loss, and Dask forgetting task results.
    if task in record["tasks"]:
        raise ProjectError(f"{task}: already claimed; refusing duplicate execution")
    record["tasks"].add(task)


def _forget(invocation: str, *, dask_scheduler: Any) -> None:
    dask_scheduler.extensions.get("lightcone-executions", {}).pop(invocation, None)


def _rpc(client: Any, function: Callable[..., None], *args: Any) -> None:
    try:
        client.sync(client.run_on_scheduler, function, *args, callback_timeout=5)
    except ProjectError:
        raise
    except Exception as exc:
        raise ProjectError(f"cannot contact the Dask execution guard: {exc}") from exc


def _call(invocation: str, task: str, function: Callable[..., Any], *args: Any) -> Any:
    from distributed import get_client

    # A lost claim reply is ambiguous. Execute only after acknowledgment, and
    # never recreate missing state or retry the claim on a worker's behalf.
    _rpc(get_client(), _claim, invocation, task)
    return function(*args)


@dataclass(frozen=True)
class Invocation:
    """Submit tasks that may begin at most once within this invocation."""

    client: Any
    id: str

    def submit(self, function: Callable[..., Any], *args: Any, key: str) -> Any:
        """Guard each task before its first side effect, including Dask recomputation."""
        return self.client.submit(
            _call, self.id, key, function, *args,
            key=f"lc-{self.id}-{key}", pure=False, retries=0,
        )


@contextmanager
def invocation(client: Any) -> Iterator[Invocation]:
    """Register claims for one borrowed client; never stop its running commands.

    Missing records refuse admission, so forgetting an invocation also prevents
    late tasks from starting. Cleanup failures cannot discard received results;
    abandoned records are collected when another invocation registers.
    """
    run = Invocation(client, uuid4().hex)
    _rpc(client, _register, run.id, client.id)
    try:
        yield run
    finally:
        try:
            _rpc(client, _forget, run.id)
        except ProjectError:
            pass
