"""pi harness — ``pi -p`` headless (``@earendil-works/pi-coding-agent``).

Unlike Claude Code, pi gates skill loading via *invoke flags* (``--skill`` /
``--no-skills``), not via the on-disk scaffold. So this harness keeps the
Lightcone scaffold in place (no-op ``prepare``) and lets the with/without-skills
flags decide what the agent sees.
"""

from __future__ import annotations

import json
import shlex
import time

from lightcone.eval.harnesses.base import AgentResult, Harness, SandboxLike


class PiHarness(Harness):
    """Drives the pi coding agent (``@earendil-works/pi-coding-agent``).

    pi uses the process CWD as the project dir (no ``--cd`` flag), so every
    command is prefixed with ``cd <work_dir> &&``. Skill loading is toggled at
    invoke time, not by file-stripping.
    """

    name = "pi"

    def install_commands(self) -> list[str]:
        return ["npm install -g @earendil-works/pi-coding-agent"]

    def credential_env_keys(self) -> list[str]:
        return ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"]

    def prepare(
        self, sandbox: SandboxLike, *, work_dir: str, with_skills: bool
    ) -> None:
        """No-op: pi gates skills via invoke flags (``--skill`` / ``--no-skills``),
        not by stripping the on-disk scaffold. We keep the scaffold in place and
        let the flags in ``invoke`` decide what the agent loads."""

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
        # pi uses the process CWD as the project dir (no `--cd`), so prefix the
        # command with `cd <work_dir>`. `--no-session` keeps the run ephemeral.
        if with_skills:
            # Load the lc-init scaffold's skills dir (relative to work_dir).
            skill_flags = "--skill .claude/skills"
        else:
            # Bare run: disable skill discovery and context files (CLAUDE.md etc).
            skill_flags = "--no-skills --no-context-files"

        model_flag = ""
        if model:
            model_flag = f"--model {shlex.quote(model)}"
            # Heuristic: Claude-family model ids need the anthropic provider, since
            # pi's default provider is google. Other ids fall through to whatever
            # provider pi is configured with.
            if any(tag in model.lower() for tag in ("claude", "sonnet", "opus", "haiku")):
                model_flag = f"--provider anthropic {model_flag}"

        cmd = (
            f"cd {work_dir} && "
            f'pi -p "$(cat {shlex.quote(prompt_path)})" '
            f"--mode json --no-session "
            f"{skill_flags} "
            f"{model_flag}"
        ).strip()

        start = time.monotonic()
        result = sandbox.exec_async_poll(cmd, timeout=timeout)
        duration_ms = int((time.monotonic() - start) * 1000)
        return parse_pi_output(result.output, result.exit_code, duration_ms)


def parse_pi_output(raw: str, exit_code: int, duration_ms: int) -> AgentResult:
    """Parse output from ``pi -p --mode json``.

    The ``--mode json`` shape is UNCONFIRMED, so this is deliberately tolerant:
    try ``json.loads`` on the whole stdout, then on the last non-empty line; pull
    cost / turn / final-text fields under their plausible aliases. Never raises on
    malformed JSON — falls back to the last non-empty line as ``result_text``.
    """
    result = AgentResult(duration_ms=duration_ms, raw_jsonl=raw, is_error=exit_code != 0)

    lines = [ln.strip() for ln in raw.strip().splitlines() if ln.strip()]
    last_line = lines[-1] if lines else ""

    data: dict | None = None
    for candidate in (raw.strip(), last_line):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            data = parsed
            break

    if data is None:
        # No parseable JSON object — best-effort fall back to the last line.
        result.result_text = last_line
        return result

    cost = data.get("cost", data.get("cost_usd", data.get("total_cost_usd", 0.0)))
    try:
        result.cost_usd = float(cost)
    except (TypeError, ValueError):
        result.cost_usd = 0.0

    turns = data.get("num_turns", data.get("turns", 0))
    try:
        result.num_turns = int(turns)
    except (TypeError, ValueError):
        result.num_turns = 0

    for key in ("result", "text", "content", "message"):
        if key in data and data[key]:
            result.result_text = str(data[key])
            break
    else:
        result.result_text = last_line

    return result
