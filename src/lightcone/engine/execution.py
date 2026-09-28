"""Bound one invocation's ordinary Dask tasks to their command lifetimes.

The existing scheduler keeps small claims and completion receipts. Tasks never
create missing invocations: losing the scheduler therefore fails closed, rather
than starting a recipe again. This is not a lock on a project checkout.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from lightcone.engine.project import ProjectError

_HEARTBEAT = 2.0
_LEASE = 15.0
_RPC_TIMEOUT = 5.0
_STOP_TIMEOUT = 40.0
_CANCELLED: ContextVar[Callable[[], bool]] = ContextVar(
    "execution_cancelled", default=lambda: False,
)


class ExecutionUncertain(ProjectError):  # noqa: N818
    """Execution may still own writers; its partial outputs must be retained."""


class ExecutionCancelled(ProjectError):  # noqa: N818
    """Execution was revoked and its command has stopped."""


def cancelled() -> bool:
    """Check the current task's authorization without blocking its subprocess loop."""
    return _CANCELLED.get()()


def check_cancelled() -> None:
    """Refuse further task mutations once cancellation has been observed."""
    if cancelled():
        raise ExecutionCancelled("execution was cancelled")


def _state(
    invocation: str, operation: str, task: str = "", value: Any = None,
    *, dask_scheduler: Any,
) -> Any:
    # Scheduler callbacks run serially on its event loop. Registration is a
    # driver-only operation, never retried implicitly by a task or heartbeat.
    records = dask_scheduler.extensions.setdefault("lightcone-executions", {})
    now = time.monotonic()
    if operation == "register":
        if invocation in records:
            raise ExecutionUncertain("execution is already registered")
        records[invocation] = {"client": value, "deadline": now + _LEASE,
                               "active": True, "tasks": {}}
        return None
    record = records.get(invocation)
    if record is None:
        raise ExecutionUncertain("execution is no longer registered; refusing task replay")
    if now > record["deadline"] or record["client"] not in dask_scheduler.clients:
        record["active"] = False
    if operation == "heartbeat":
        if record["active"]:
            record["deadline"] = now + _LEASE
        return record["active"]
    if operation == "active":
        return record["active"]
    if operation == "claim":
        if not record["active"]:
            raise ExecutionCancelled("execution is no longer active")
        previous = record["tasks"].get(task)
        if previous is not None:
            if previous["state"] == "finished":
                return False, previous["result"]
            record["active"] = False
            raise ExecutionUncertain(f"{task}: a previous attempt has no confirmed result")
        record["tasks"][task] = {"state": "running", "attempt": value}
        return True, None
    if operation in {"finished", "stopped", "uncertain"}:
        attempt, result = value
        if record["tasks"].get(task, {}).get("attempt") != attempt:
            raise ExecutionUncertain(f"{task}: completion belongs to another attempt")
        record["tasks"][task] = {"state": operation, "attempt": attempt, "result": result}
        if operation == "uncertain":
            record["active"] = False
        return None
    if operation == "revoke":
        record["active"] = False
    if operation in {"revoke", "pending"}:
        return [name for name, item in record["tasks"].items()
                if item["state"] in {"running", "uncertain"}]
    if operation == "forget":
        del records[invocation]
        return None
    raise ValueError(f"unknown execution operation: {operation}")


async def _request(client: Any, invocation: str, operation: str, task: str, value: Any) -> Any:
    return await client.run_on_scheduler(_state, invocation, operation, task, value)


def _rpc(client: Any, invocation: str, operation: str, task: str = "", value: Any = None) -> Any:
    return client.sync(
        _request, client, invocation, operation, task, value, callback_timeout=_RPC_TIMEOUT,
    )


def _call(invocation: str, task: str, function: Callable[..., Any], *args: Any) -> Any:
    from distributed import get_client

    client = get_client()
    # A lost claim reply is ambiguous. Do not execute unless it was received.
    attempt = uuid4().hex
    claimed, result = _rpc(client, invocation, "claim", task, attempt)
    if not claimed:
        return result
    stopped = threading.Event()
    revoked = threading.Event()

    def monitor() -> None:
        while not stopped.wait(_HEARTBEAT):
            try:
                active = _rpc(client, invocation, "active")
            except Exception:
                active = False
            if not active:
                revoked.set()
                return

    watcher = threading.Thread(target=monitor, daemon=True)
    watcher.start()
    token = _CANCELLED.set(revoked.is_set)
    try:
        result = function(*args)
        check_cancelled()
    except BaseException as exc:
        state = "uncertain" if isinstance(exc, ExecutionUncertain) else "stopped"
        try:
            _rpc(client, invocation, state, task, (attempt, None))
        except Exception:
            pass
        raise
    else:
        try:
            _rpc(client, invocation, "finished", task, (attempt, result))
        except Exception as exc:
            raise ExecutionUncertain(
                f"{task}: could not record completion; outputs retained, refusing replay"
            ) from exc
        return result
    finally:
        _CANCELLED.reset(token)
        stopped.set()


@dataclass
class Invocation:
    """Submit tasks whose side effects must not be replayed automatically."""

    client: Any
    id: str = field(default_factory=lambda: uuid4().hex)
    futures: list[Any] = field(default_factory=list)
    stopped: bool = False

    def submit(
        self, function: Callable[..., Any], *args: Any, key: str, resources: dict[str, float],
    ) -> Any:
        """Claim each logical task inside its worker before it can mutate files."""
        future = self.client.submit(
            _call, self.id, key, function, *args,
            key=f"lc-{self.id}-{key}", pure=False, retries=0, resources=resources,
        )
        self.futures.append(future)
        return future


@contextmanager
def invocation(client: Any) -> Iterator[Invocation]:
    """Own authorization and wait for running commands to stop before detaching."""
    run = Invocation(client)
    _rpc(client, run.id, "register", value=client.id)
    stopped = threading.Event()

    def heartbeat() -> None:
        while not stopped.wait(_HEARTBEAT):
            try:
                if not _rpc(client, run.id, "heartbeat"):
                    return
            except Exception:
                return

    threading.Thread(target=heartbeat, daemon=True).start()
    interruption: KeyboardInterrupt | None = None
    try:
        yield run
    except KeyboardInterrupt as exc:
        interruption = exc
        raise
    finally:
        stopped.set()
        try:
            pending = _rpc(client, run.id, "revoke")
            unfinished = [future for future in run.futures if not future.done()]
            if unfinished:
                client.sync(client.cancel, unfinished, callback_timeout=_RPC_TIMEOUT)
            deadline = time.monotonic() + _STOP_TIMEOUT
            while pending and time.monotonic() < deadline:
                time.sleep(0.1)
                pending = _rpc(client, run.id, "pending")
            if pending:
                raise ExecutionUncertain("unconfirmed tasks: " + ", ".join(pending))
            # A late dispatch cannot recreate this record: missing is a refusal.
            _rpc(client, run.id, "forget")
            run.stopped = True
            if interruption is not None:
                interruption.execution_stopped = True  # type: ignore[attr-defined]
        except Exception as exc:
            raise ExecutionUncertain(
                f"could not confirm execution stopped: {exc}; partial outputs were retained. "
                "Stop the allocation and verify its commands/containers have ended before retrying"
            ) from exc
