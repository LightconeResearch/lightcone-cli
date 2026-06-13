"""Harness abstraction: one agent CLI driven headlessly inside a Sandbox.

A ``Harness`` is the single seam where the eval becomes agent-specific. Before
this layer, ``EvalSandbox.exec_claude`` hardwired Claude Code into the trial
loop. A harness declares four things and nothing more:

1. how to **install** its agent CLI into the eval image,
2. which host **credentials** to forward for auth,
3. optional per-trial project **prepare** (e.g. strip the Lightcone scaffold for
   the without-skills arm of the A/B), and
4. how to **invoke** the agent headlessly, returning a parsed ``AgentResult``.

Everything downstream stays harness-agnostic: graders read the materialized
filesystem (``lc status --json``, ``astra validate``), never the agent. The
concrete per-harness commands live in the ``harness-invocation-matrix`` fiber.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class AgentResult:
    """Parsed result of one headless agent invocation, normalized across
    harnesses.

    ``cost_usd`` is best-effort: harnesses that do not price a run (e.g. codex)
    report ``0.0``. ``raw_jsonl`` keeps the agent's full stdout for the trial
    transcript sidecar.
    """

    cost_usd: float = 0.0
    num_turns: int = 0
    duration_ms: int = 0
    result_text: str = ""
    is_error: bool = False
    raw_jsonl: str = ""


@runtime_checkable
class CommandResult(Protocol):
    """Anything with an exit code and captured output — e.g. the sandbox
    backends' ``ExecuteResult``."""

    exit_code: int
    output: str


@runtime_checkable
class SandboxLike(Protocol):
    """The execution substrate a ``Harness`` drives.

    Both ``DaytonaSandbox`` and ``LocalDockerSandbox`` satisfy this. A harness
    uses only these primitives, so it never depends on a specific backend.
    """

    WORK_DIR: str

    def exec(self, cmd: str, timeout: int = 300, cwd: str | None = None) -> CommandResult: ...

    def exec_async_poll(
        self, cmd: str, timeout: int = 600, poll_interval: int = 10
    ) -> CommandResult: ...

    def upload_file(self, remote_path: str, content: bytes) -> None: ...


class Harness(ABC):
    """One agent CLI, driven headlessly inside a sandbox.

    Subclasses set ``name`` (the registry id) and implement install /
    credentials / invoke. ``prepare`` has a sensible default — strip the
    Lightcone scaffold for the without-skills arm — that file-scaffold harnesses
    (Claude) inherit and flag-based harnesses (pi) may override.
    """

    #: registry id, e.g. "claude" / "codex" / "pi"
    name: str = ""

    @abstractmethod
    def install_commands(self) -> list[str]:
        """Shell commands (run as root at image-build time) that install the
        agent CLI onto ``PATH``. Consumed by every sandbox backend's image
        build so the binary is present before a trial starts."""

    @abstractmethod
    def credential_env_keys(self) -> list[str]:
        """Host env var names to forward into the sandbox for auth. Missing keys
        are skipped — a harness with no credentials is reported *skipped*, not
        failed."""

    @abstractmethod
    def invoke(
        self,
        sandbox: SandboxLike,
        *,
        prompt_path: str,
        work_dir: str,
        max_turns: int,
        timeout: int,
        with_skills: bool,
        model: str | None = None,
    ) -> AgentResult:
        """Run the agent headlessly against the project in ``work_dir``, reading
        the loop prompt from ``prompt_path`` inside the sandbox.

        ``with_skills`` lets harnesses with native skill flags (pi's
        ``--skill`` / ``--no-skills``) toggle skill loading at invoke time;
        file-scaffold harnesses rely on ``prepare`` having stripped the scaffold
        instead, and can ignore it.
        """

    def prepare(
        self, sandbox: SandboxLike, *, work_dir: str, with_skills: bool
    ) -> None:
        """Per-trial project prep before invocation.

        Default: for the without-skills arm, strip the Lightcone scaffold so the
        agent runs bare (``.claude/`` skills+hooks, ``CLAUDE.md``, and any
        sibling agent-context files). The ``lc`` engine is intentionally left in
        place — the A/B isolates the *guidance layer*, not the execution
        substrate. Harnesses that gate skills via invoke flags may override.
        """
        if not with_skills:
            sandbox.exec(
                f"cd {work_dir} && rm -rf .claude CLAUDE.md AGENTS.md GEMINI.md",
                timeout=30,
            )
