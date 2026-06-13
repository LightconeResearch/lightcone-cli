"""Claude Code harness — ``claude -p`` headless (stream-json)."""

from __future__ import annotations

import json
import shlex
import time

from lightcone.eval.harnesses.base import AgentResult, Harness, SandboxLike


class ClaudeHarness(Harness):
    """Drives Claude Code. This is the original (and reference) seam: the trial
    loop used to call this logic directly via ``EvalSandbox.exec_claude``."""

    name = "claude"

    def install_commands(self) -> list[str]:
        return [
            "curl -fsSL https://claude.ai/install.sh | bash"
            " && cp /root/.local/bin/claude /usr/local/bin/claude",
        ]

    def credential_env_keys(self) -> list[str]:
        return ["CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"]

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
        # Claude Code consumes skills from the scaffolded `.claude/` dir, so the
        # with/without-skills split is handled by `prepare` (file strip), not a
        # flag here. `with_skills` is accepted for interface uniformity.
        model_flag = f"--model {shlex.quote(model)}" if model else ""
        cmd = (
            f"cd {work_dir} && "
            f'claude -p "$(cat {shlex.quote(prompt_path)})" '
            f"--output-format stream-json --verbose "
            f"--dangerously-skip-permissions "
            f"--max-turns {max_turns} "
            f"{model_flag}"
        ).strip()

        start = time.monotonic()
        result = sandbox.exec_async_poll(cmd, timeout=timeout)
        duration_ms = int((time.monotonic() - start) * 1000)
        return parse_claude_output(result.output, result.exit_code, duration_ms)


def parse_claude_output(
    raw_output: str, exit_code: int, duration_ms: int
) -> AgentResult:
    """Parse JSONL from ``claude -p --output-format stream-json``.

    One JSON object per line; the final ``{"type": "result", ...}`` line carries
    the aggregate metrics.
    """
    result = AgentResult(duration_ms=duration_ms, raw_jsonl=raw_output)

    if exit_code != 0:
        result.is_error = True
        result.result_text = raw_output
        return result

    for raw_line in reversed(raw_output.strip().splitlines()):
        stripped = raw_line.strip()
        if not stripped or not stripped.startswith("{"):
            continue
        try:
            data = json.loads(stripped)
            if data.get("type") == "result":
                result.cost_usd = float(
                    data.get("cost_usd", data.get("total_cost_usd", 0.0))
                )
                result.num_turns = int(data.get("num_turns", 0))
                result.duration_ms = int(data.get("duration_ms", duration_ms))
                result.result_text = str(data.get("result", ""))
                result.is_error = bool(data.get("is_error", False))
                return result
        except (json.JSONDecodeError, ValueError):
            continue

    # No result line found
    result.result_text = raw_output
    result.is_error = True
    return result
