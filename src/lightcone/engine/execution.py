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
from typing import Any, Literal
from uuid import uuid4

from lightcone.engine.sandbox.model import ExecutionCancelled as ExecutionCancelled
from lightcone.engine.sandbox.model import ExecutionUncertain as ExecutionUncertain

_HEARTBEAT = 2.0
_LEASE = 15.0
_RPC_TIMEOUT = 5.0
_STOP_TIMEOUT = 40.0
_CANCELLED: ContextVar[Callable[[], bool]] = ContextVar(
    "execution_cancelled", default=lambda: False,
)


Operation = Literal[
    "register", "heartbeat", "active", "claim", "finished", "stopped", "uncertain",
    "revoke", "pending", "forget",
]


class ExecutionInterrupted(KeyboardInterrupt):
    """An interrupt whose invocation has positively confirmed command cleanup."""


@dataclass(frozen=True)
class _Claim:
    fresh: bool
    result: Any
    remaining: float


@dataclass(frozen=True)
class _Progress:
    running: tuple[str, ...]
    uncertain: tuple[str, ...]


def cancelled() -> bool:
    """Check the current task's authorization without blocking its subprocess loop."""
    return _CANCELLED.get()()


def check_cancelled() -> None:
    """Refuse further task mutations once cancellation has been observed."""
    if cancelled():
        raise ExecutionCancelled("execution was cancelled")


def _state(
    invocation: str, operation: Operation, task: str = "", value: Any = None,
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
        return max(0.0, record["deadline"] - now) if record["active"] else 0.0
    if operation == "claim":
        if not record["active"]:
            raise ExecutionCancelled("execution is no longer active")
        previous = record["tasks"].get(task)
        if previous is not None:
            if previous["state"] == "finished":
                return _Claim(False, previous["result"], 0.0)
            record["active"] = False
            raise ExecutionUncertain(f"{task}: a previous attempt has no confirmed result")
        record["tasks"][task] = {"state": "running", "attempt": value}
        return _Claim(True, None, record["deadline"] - now)
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
        return _Progress(
            tuple(name for name, item in record["tasks"].items() if item["state"] == "running"),
            tuple(name for name, item in record["tasks"].items() if item["state"] == "uncertain"),
        )
    if operation == "forget":
        del records[invocation]
        return None
    raise ValueError(f"unknown execution operation: {operation}")


def _rpc(
    client: Any, invocation: str, operation: Operation, task: str = "", value: Any = None,
) -> Any:
    return client.sync(
        client.run_on_scheduler, _state, invocation, operation, task, value,
        callback_timeout=_RPC_TIMEOUT,
    )


def _call(invocation: str, task: str, function: Callable[..., Any], *args: Any) -> Any:
    from distributed import get_client

    client = get_client()
    # A lost claim reply is ambiguous. Do not execute unless it was received.
    attempt = uuid4().hex
    requested_at = time.monotonic()
    claim: _Claim = _rpc(client, invocation, "claim", task, attempt)
    if not claim.fresh:
        return claim.result
    deadline = requested_at + claim.remaining
    stopped = threading.Event()
    revoked = threading.Event()

    def monitor() -> None:
        nonlocal deadline
        while not stopped.wait(_HEARTBEAT):
            requested_at = time.monotonic()
            if requested_at >= deadline:
                revoked.set()
                return
            try:
                remaining = _rpc(client, invocation, "active")
            except ExecutionUncertain:
                revoked.set()
                return
            except Exception:
                # An unavailable RPC is not a revocation. Keep the last grant,
                # without extending it, while retrying within its deadline.
                continue
            if not remaining or time.monotonic() >= deadline:
                revoked.set()
                return
            deadline = requested_at + remaining

    def is_cancelled() -> bool:
        if time.monotonic() >= deadline:
            revoked.set()
        return revoked.is_set()

    watcher = threading.Thread(target=monitor, daemon=True)
    watcher.start()
    token = _CANCELLED.set(is_cancelled)
    try:
        check_cancelled()
        result = function(*args)
        check_cancelled()
    except BaseException as exc:
        state: Operation = "uncertain" if isinstance(exc, ExecutionUncertain) else "stopped"
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
                f"{task}: could not record completion; refusing replay"
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
    _submissions: int = 0

    def submit(
        self, function: Callable[..., Any], *args: Any, key: str, resources: dict[str, float],
    ) -> Any:
        """Claim each logical task inside its worker before it can mutate files."""
        # A submit failure may follow native acceptance. Count it before the
        # call so even a caught exception cannot manufacture full completion.
        self._submissions += 1
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
    registered_at = time.monotonic()
    _rpc(client, run.id, "register", value=client.id)
    stopped = threading.Event()

    def heartbeat() -> None:
        deadline = registered_at + _LEASE
        while not stopped.wait(_HEARTBEAT):
            requested_at = time.monotonic()
            if requested_at >= deadline:
                return
            try:
                if not _rpc(client, run.id, "heartbeat"):
                    return
            except ExecutionUncertain:
                return
            except Exception:
                continue
            deadline = requested_at + _LEASE

    threading.Thread(target=heartbeat, daemon=True).start()
    failure: BaseException | None = None
    try:
        yield run
    except BaseException as exc:
        failure = exc
        raise
    finally:
        stopped.set()
        # A finished wrapper has already acknowledged command cleanup and
        # published its result. Losing metadata-cleanup RPCs cannot undo that.
        run.stopped = (
            run._submissions == len(run.futures)
            and all(future.status == "finished" for future in run.futures)
        )
        try:
            pending = _rpc(client, run.id, "revoke")
            unfinished = [future for future in run.futures if not future.done()]
            if unfinished:
                client.sync(client.cancel, unfinished, callback_timeout=_RPC_TIMEOUT)
            deadline = time.monotonic() + _STOP_TIMEOUT
            while pending.running and not pending.uncertain and time.monotonic() < deadline:
                time.sleep(0.1)
                pending = _rpc(client, run.id, "pending")
            if pending.running or pending.uncertain:
                run.stopped = False
                raise ExecutionUncertain(
                    "unconfirmed tasks: " + ", ".join((*pending.running, *pending.uncertain))
                )
            run.stopped = True
        except Exception as exc:
            if not run.stopped:
                raise ExecutionUncertain(
                    f"could not confirm execution stopped: {exc}; partial outputs were retained. "
                    "Stop the allocation and verify its commands/containers have ended "
                    "before retrying"
                ) from exc
        else:
            # Revoked admission plus no unfinished claims already proves stop.
            # Forgetting receipts is metadata cleanup, not another safety gate.
            try:
                _rpc(client, run.id, "forget")
            except Exception:
                pass
        if isinstance(failure, KeyboardInterrupt) and run.stopped:
            raise ExecutionInterrupted() from failure
