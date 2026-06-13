"""OpenAI Codex CLI harness — ``codex exec`` headless (JSONL)."""

from __future__ import annotations

import json
import shlex
import time

from lightcone.eval.harnesses.base import AgentResult, Harness, SandboxLike


class CodexHarness(Harness):
    """Drives the OpenAI Codex CLI. Mirrors :class:`ClaudeHarness`: a headless
    ``codex exec`` run inside the sandbox, parsed into an ``AgentResult``."""

    name = "codex"

    def install_commands(self) -> list[str]:
        # The eval base image provides node/npm; the local binary is a brew cask
        # but npm is the container path.
        return ["npm install -g @openai/codex"]

    def credential_env_keys(self) -> list[str]:
        return ["OPENAI_API_KEY", "CODEX_API_KEY"]

    # `prepare` is inherited from the base default (strip the scaffold for the
    # without-skills arm). Note: Codex reads `AGENTS.md`, not `.claude/skills`,
    # so "with-skills" guidance never actually reaches Codex — a known
    # limitation tracked for LCR-85; we do not synthesize AGENTS.md here.

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
        # Codex has no native max-turns; the run is bounded by `timeout` only.
        # `with_skills` is accepted for interface uniformity (see `prepare`).
        model_flag = f"-m {shlex.quote(model)}" if model else ""
        cmd = (
            f"codex exec --cd {shlex.quote(work_dir)} "
            f"--dangerously-bypass-approvals-and-sandbox "
            f"--skip-git-repo-check --json "
            f"{model_flag} "
            f"-o /tmp/codex-last-message.txt "
            f'"$(cat {shlex.quote(prompt_path)})"'
        ).strip()

        start = time.monotonic()
        result = sandbox.exec_async_poll(cmd, timeout=timeout)
        duration_ms = int((time.monotonic() - start) * 1000)

        last = sandbox.exec("cat /tmp/codex-last-message.txt")
        last_message = last.output if last.exit_code == 0 else ""

        return parse_codex_output(
            result.output, result.exit_code, duration_ms, last_message
        )


def parse_codex_output(
    raw_jsonl: str, exit_code: int, duration_ms: int, last_message: str
) -> AgentResult:
    """Parse JSONL from ``codex exec --json``.

    The event shape is unconfirmed, so this stays tolerant: bad lines are
    skipped and never raise. Codex does not price a run, so ``cost_usd`` is
    always ``0.0``. ``result_text`` prefers the ``-o`` last-message file,
    falling back to the last non-empty stdout line. ``num_turns`` is a
    best-effort count of assistant/turn events.
    """
    result = AgentResult(
        cost_usd=0.0,
        duration_ms=duration_ms,
        is_error=exit_code != 0,
        raw_jsonl=raw_jsonl,
    )

    num_turns = 0
    last_nonempty_line = ""
    for raw_line in raw_jsonl.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        last_nonempty_line = stripped
        if not stripped.startswith("{"):
            continue
        try:
            data = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            continue
        # Best-effort turn detection: tolerate several plausible event shapes.
        event_type = str(data.get("type", "") or data.get("event", ""))
        if "turn" in event_type or "assistant" in event_type:
            num_turns += 1

    result.num_turns = num_turns
    result.result_text = last_message or last_nonempty_line
    return result
