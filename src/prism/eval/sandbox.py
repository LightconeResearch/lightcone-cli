"""Daytona sandbox lifecycle management for eval trials."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from prism.eval.models import Variant

logger = logging.getLogger(__name__)

BUILD_COMPLETE_MARKER = "BUILD_COMPLETE"


@dataclass
class ExecuteResult:
    """Result from running a command in the sandbox."""

    exit_code: int
    output: str


@dataclass
class ClaudeResult:
    """Parsed result from a claude -p invocation."""

    cost_usd: float = 0.0
    num_turns: int = 0
    duration_ms: int = 0
    result_text: str = ""
    is_error: bool = False


@dataclass
class EvalSandbox:
    """Manages an ephemeral Daytona sandbox for one eval trial.

    Wraps daytona_sdk.Daytona to provide create/setup/exec/teardown lifecycle.
    """

    WORK_DIR = "/home/user/project"

    task_id: str = ""
    variant_id: str = ""
    trial_id: str = ""
    sandbox_image: str = "ghcr.io/lightconeresearch/prism-eval:latest"
    env_vars: dict[str, str] = field(default_factory=dict)

    _daytona: Any = field(default=None, repr=False)
    _sandbox: Any = field(default=None, repr=False)

    def create(self) -> None:
        """Create an ephemeral Daytona sandbox."""
        from daytona_sdk import CreateSandboxParams, Daytona

        self._daytona = Daytona()

        labels = {
            "prism-eval": "true",
            "task": self.task_id,
            "variant": self.variant_id,
            "trial": self.trial_id,
        }

        # Merge env vars: host ANTHROPIC_API_KEY + Langfuse creds + eval metadata
        sandbox_env = {
            "PRISM_EVAL": "true",
            "PRISM_EVAL_TRIAL_ID": self.trial_id,
            "PRISM_EVAL_TASK_ID": self.task_id,
            "PRISM_EVAL_VARIANT_ID": self.variant_id,
        }
        # Pass through host API keys and Langfuse config
        for key in (
            "ANTHROPIC_API_KEY",
            "LANGFUSE_PUBLIC_KEY",
            "LANGFUSE_SECRET_KEY",
            "LANGFUSE_HOST",
        ):
            val = os.environ.get(key)
            if val:
                sandbox_env[key] = val
        sandbox_env.update(self.env_vars)

        params = CreateSandboxParams(
            image=self.sandbox_image,
            labels=labels,
            env_vars=sandbox_env,
            auto_stop_interval=30,
        )
        self._sandbox = self._daytona.create(params)
        logger.info("Created sandbox %s for trial %s", self._sandbox.id, self.trial_id)

    def setup(
        self,
        seed_dir: Path,
        variant: Variant,
        evals_dir: Path,
        universe: str,
        loop_prompt_template: str,
    ) -> None:
        """Upload seed project, apply variant overrides, and template the loop prompt."""
        assert self._sandbox is not None, "Call create() first"

        # Upload seed project files
        self._upload_directory(seed_dir, self.WORK_DIR)

        # Git init so prism init works
        self.exec(f"cd {self.WORK_DIR} && git init && git add -A && git commit -m 'seed'")

        # Run prism init
        self.exec(f"cd {self.WORK_DIR} && prism init . --no-git --no-venv")

        # Apply variant file overrides
        for dest_rel, source_rel in variant.file_overrides.items():
            source_path = evals_dir / "variants" / source_rel
            if source_path.exists():
                dest_path = f"{self.WORK_DIR}/{dest_rel}"
                self.upload_file(dest_path, source_path.read_bytes())

        # Apply inline overrides
        for dest_rel, content in variant.inline_overrides.items():
            dest_path = f"{self.WORK_DIR}/{dest_rel}"
            self.upload_file(dest_path, content.encode())

        # Template the loop prompt
        prompt = loop_prompt_template.replace("{{UNIVERSE}}", universe)
        self.upload_file("/tmp/loop-prompt.md", prompt.encode())

        # Set CLAUDE_CODE_SESSION_ID for Langfuse trace lookup
        self.exec(
            f"echo 'export CLAUDE_CODE_SESSION_ID=eval-{self.trial_id}' >> ~/.bashrc"
        )

    def exec(self, cmd: str, timeout: int = 300) -> ExecuteResult:
        """Execute a command in the sandbox."""
        assert self._sandbox is not None, "Call create() first"

        result = self._sandbox.process.exec(cmd, cwd=self.WORK_DIR, timeout=timeout)
        return ExecuteResult(
            exit_code=result.exit_code,
            output=result.output or "",
        )

    def exec_claude(
        self,
        max_turns: int = 25,
        timeout: int = 600,
        model: str | None = None,
    ) -> ClaudeResult:
        """Run claude -p with the loop prompt and parse JSON output."""
        assert self._sandbox is not None, "Call create() first"

        model_flag = f"--model {model}" if model else ""
        cmd = (
            f"cd {self.WORK_DIR} && "
            f"claude -p \"$(cat /tmp/loop-prompt.md)\" "
            f"--output-format json "
            f"--permission-mode bypassPermissions "
            f"--max-turns {max_turns} "
            f"{model_flag}"
        ).strip()

        start = time.monotonic()
        result = self.exec(cmd, timeout=timeout)
        duration_ms = int((time.monotonic() - start) * 1000)

        return _parse_claude_output(result.output, result.exit_code, duration_ms)

    def upload_file(self, remote_path: str, content: bytes) -> None:
        """Upload a file to the sandbox."""
        assert self._sandbox is not None, "Call create() first"
        self._sandbox.fs.upload_file(remote_path, content)

    def teardown(self) -> None:
        """Delete the sandbox."""
        if self._sandbox is not None and self._daytona is not None:
            try:
                self._daytona.delete(self._sandbox)
                logger.info("Deleted sandbox for trial %s", self.trial_id)
            except Exception:
                logger.warning(
                    "Failed to delete sandbox for trial %s", self.trial_id, exc_info=True
                )
            self._sandbox = None

    def _upload_directory(self, local_dir: Path, remote_dir: str) -> None:
        """Upload a local directory tree to the sandbox."""
        for local_path in local_dir.rglob("*"):
            if local_path.is_file():
                rel = local_path.relative_to(local_dir)
                remote_path = f"{remote_dir}/{rel}"
                self.upload_file(remote_path, local_path.read_bytes())


def _parse_claude_output(
    raw_output: str, exit_code: int, duration_ms: int
) -> ClaudeResult:
    """Parse the JSON output from claude -p --output-format json."""
    result = ClaudeResult(duration_ms=duration_ms)

    if exit_code != 0:
        result.is_error = True
        result.result_text = raw_output
        return result

    try:
        data = json.loads(raw_output)
    except (json.JSONDecodeError, ValueError):
        # Try to find JSON in the output (claude may print non-JSON before it)
        for line in raw_output.strip().splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    data = json.loads(line)
                    break
                except (json.JSONDecodeError, ValueError):
                    continue
        else:
            result.result_text = raw_output
            result.is_error = True
            return result

    result.cost_usd = float(data.get("cost_usd", 0.0))
    result.num_turns = int(data.get("num_turns", 0))
    result.duration_ms = int(data.get("duration_ms", duration_ms))
    result.result_text = str(data.get("result", ""))
    result.is_error = bool(data.get("is_error", False))

    return result
