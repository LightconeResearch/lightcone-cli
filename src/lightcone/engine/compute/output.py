"""Forward command bytes through ordinary Dask events to the invoking process."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Literal

from lightcone.engine.sandbox.boundary import write_output


@dataclass
class Forwarder:
    """Receive one invocation's output and acknowledge each task's final bytes."""

    topic: str
    stdout: Literal["stdout", "stderr"] = "stdout"
    finished: set[str] = field(default_factory=set)
    condition: threading.Condition = field(default_factory=threading.Condition)

    def receive(self, event: tuple[float, dict[str, Any]]) -> None:
        """Write stream bytes or acknowledge a task's completion marker."""
        message = event[1]
        if "done" in message:
            with self.condition:
                self.finished.add(message["done"])
                self.condition.notify_all()
        elif message.get("stream") in ("stdout", "stderr"):
            stream = self.stdout if message["stream"] == "stdout" else "stderr"
            write_output(stream, message["data"])

    def wait(self, task: str) -> bool:
        """Wait briefly for output already queued when a task returned its result."""
        with self.condition:
            return self.condition.wait_for(lambda: task in self.finished, timeout=10)


@contextmanager
def forwarding(
    client: Any, invocation: str, *, stdout: Literal["stdout", "stderr"] = "stdout"
) -> Iterator[Forwarder]:
    """Subscribe before any task starts and unsubscribe when the invocation ends."""
    forwarder = Forwarder(f"lc-output-{invocation}", stdout)
    client.subscribe_topic(forwarder.topic, forwarder.receive)
    try:
        yield forwarder
    finally:
        client.unsubscribe_topic(forwarder.topic)


def call(function: Callable[..., Any], topic: str, task: str, *args: Any) -> Any:
    """Run a task with a byte receiver; mark completion even when the task fails."""
    from distributed import get_worker

    worker = get_worker()

    def output(stream: str, data: bytes) -> None:
        worker.log_event(topic, {"stream": stream, "data": data})

    try:
        return function(*args, output=output)
    finally:
        worker.log_event(topic, {"done": task})
