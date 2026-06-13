"""Sandbox backend abstraction for eval trials.

A ``Sandbox`` is the execution substrate one eval trial runs inside. It mirrors
the public surface of the original :class:`lightcone.eval.sandbox.EvalSandbox`
(the Daytona backend) so harnesses — which depend only on the
``SandboxLike`` protocol (``WORK_DIR``, ``exec``, ``exec_async_poll``,
``upload_file``) — drive any backend unchanged.

Backends:
  - ``EvalSandbox`` (sandbox.py)        — ephemeral Daytona cloud sandbox
  - ``LocalDockerSandbox`` (this pkg)   — a local Docker container per trial
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ExecuteResult:
    """Result from running a command in a sandbox."""

    exit_code: int
    output: str


class Sandbox(ABC):
    """One ephemeral execution substrate for a single eval trial.

    The lifecycle is ``create() → setup() → (exec/exec_async_poll/upload_file)* →
    teardown()``. Subclasses provide the concrete substrate (Daytona cloud
    sandbox, local Docker container, …); the abstract surface here is exactly
    what the trial loop and the harness layer consume.
    """

    #: Project root inside the sandbox — where ``lc init`` scaffolds and the
    #: agent runs. Mirrors ``EvalSandbox.WORK_DIR``.
    WORK_DIR = "/home/evaluser/project"

    @abstractmethod
    def create(self) -> None:
        """Provision the substrate (build image if needed, start the sandbox)."""

    @abstractmethod
    def setup(
        self,
        seed_dir: Path,
        universe: str,
        loop_prompt_template: str,
        wheels: list[Path] | None = None,
    ) -> None:
        """Scaffold the project via ``lc init`` and overlay task seed files."""

    @abstractmethod
    def exec(self, cmd: str, timeout: int = 300, cwd: str | None = None) -> ExecuteResult:
        """Run a command in the sandbox, returning its exit code and output."""

    @abstractmethod
    def exec_async_poll(
        self, cmd: str, timeout: int = 600, poll_interval: int = 10
    ) -> ExecuteResult:
        """Run a long-running command, tolerant of gateway timeouts."""

    @abstractmethod
    def upload_file(self, remote_path: str, content: bytes) -> None:
        """Upload a file into the sandbox at ``remote_path``."""

    @abstractmethod
    def teardown(self) -> None:
        """Destroy the substrate. Idempotent."""
