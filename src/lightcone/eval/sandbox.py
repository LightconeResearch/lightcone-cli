"""Daytona sandbox lifecycle management for eval trials."""

from __future__ import annotations

import logging
import os
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lightcone.eval.harnesses.base import AgentResult
from lightcone.eval.harnesses.claude import parse_claude_output

logger = logging.getLogger(__name__)

BUILD_COMPLETE_MARKER = "BUILD_COMPLETE"

# Idle-minutes backstop before Daytona auto-stops a sandbox. Teardown is the
# primary cleanup path (``teardown()`` deletes the sandbox), but any path where
# teardown does NOT run — an unhandled exception, the eval process being killed,
# a cancelled CI job — would otherwise leak a sandbox that runs forever and
# burns the compute budget (LCR-131). A non-zero auto-stop is the safety net:
# an active trial polls Daytona every ~10s so it never goes idle, while an
# orphaned sandbox stops after this many idle minutes. NOT zero — zero disables
# the net entirely, which is the bug.
AUTO_STOP_BACKSTOP_MINUTES = 30


@dataclass
class ExecuteResult:
    """Result from running a command in the sandbox."""

    exit_code: int
    output: str


# Back-compat alias: the harness layer now owns the normalized agent-result
# type. `_parse_claude_output` is re-exported from the Claude harness so callers
# (and tests) that imported it from here keep working.
ClaudeResult = AgentResult
_parse_claude_output = parse_claude_output


@dataclass
class EvalSandbox:
    """Manages an ephemeral Daytona sandbox for one eval trial.

    Wraps daytona_sdk.Daytona to provide create/setup/exec/teardown lifecycle.
    """

    WORK_DIR = "/home/evaluser/project"

    task_id: str = ""
    trial_id: str = ""
    sandbox_image: str | Any = None  # str for pre-built, Image for dynamic, None for default
    env_vars: dict[str, str] = field(default_factory=dict)

    _daytona: Any = field(default=None, repr=False)
    _sandbox: Any = field(default=None, repr=False)

    def create(self) -> None:
        """Create an ephemeral Daytona sandbox.

        Uses Daytona's Image API for the sandbox environment:
        - None (default): Image.debian_slim("3.12") with git and Claude Code pre-installed
        - str: Pre-built Docker image reference (e.g. "python:3.12-slim")
        - Image: A daytona_sdk.Image object for custom dynamic builds
        """
        from daytona_sdk import (
            CreateSandboxFromImageParams,
            Daytona,
            Image,
        )

        self._daytona = Daytona()

        # Build the sandbox image
        if self.sandbox_image is None:
            # Pre-install third-party Python deps so the only thing we
            # upload at trial-setup time is the lightcone-cli wheel
            # built from the branch under test. ``astra-tools`` (which
            # ships the ``astra`` module) and ``astra-spec`` come from
            # PyPI here — astra isn't the package under test, just a
            # dependency, so we let pip resolve like a normal user
            # install. The lightcone-cli wheel is installed below with
            # ``--no-deps``, so any runtime dep it relies on must be
            # listed here.
            deps = (
                "astra-tools astra-spec"
                " jinja2 jsonschema"
                " snakemake snakemake-interface-executor-plugins"
                " snakemake-interface-common dask distributed"
            )
            image = (
                Image.debian_slim("3.12")
                .run_commands(
                    # System deps + non-root user (as root)
                    "apt-get update && apt-get install -y git curl bash"
                    " && rm -rf /var/lib/apt/lists/*",
                    "useradd -m -s /bin/bash evaluser",
                    # Install Claude Code globally (as root)
                    "curl -fsSL https://claude.ai/install.sh | bash"
                    " && cp /root/.local/bin/claude /usr/local/bin/claude",
                    # Pre-install all third-party Python deps (as root → system-wide)
                    f"pip install --no-cache-dir {deps}",
                )
                # Switch to non-root user for runtime
                # (Claude Code refuses bypassPermissions as root)
                .dockerfile_commands(["USER evaluser", "WORKDIR /home/evaluser"])
                .env({"PATH": "/home/evaluser/.local/bin:/usr/local/bin:/usr/bin:/bin"})
                .run_commands(
                    # Skip Claude Code onboarding
                    "mkdir -p ~/.claude"
                    " && echo '{\"hasCompletedOnboarding\": true}' > ~/.claude.json",
                )
            )
        elif isinstance(self.sandbox_image, str):
            image = Image.base(self.sandbox_image)
        else:
            image = self.sandbox_image

        labels = {
            "lightcone-eval": "true",
            "task": self.task_id,
            "trial": self.trial_id,
        }

        # Merge env vars: host ANTHROPIC_API_KEY + eval metadata
        sandbox_env = {
            "LIGHTCONE_EVAL": "true",
            "LIGHTCONE_EVAL_TRIAL_ID": self.trial_id,
            "LIGHTCONE_EVAL_TASK_ID": self.task_id,
        }
        # Pass through host API keys and OAuth token.
        for key in (
            "ANTHROPIC_API_KEY",
            "CLAUDE_CODE_OAUTH_TOKEN",
        ):
            val = os.environ.get(key)
            if val:
                sandbox_env[key] = val
        sandbox_env.update(self.env_vars)

        params = CreateSandboxFromImageParams(
            image=image,
            labels=labels,
            env_vars=sandbox_env,
            # Backstop against leaked sandboxes (see AUTO_STOP_BACKSTOP_MINUTES);
            # teardown() still deletes the sandbox on the normal path.
            auto_stop_interval=AUTO_STOP_BACKSTOP_MINUTES,
        )

        def _on_build_log(line: str) -> None:
            logger.info("[sandbox build] %s", line.rstrip())

        self._sandbox = self._daytona.create(
            params,
            on_snapshot_create_logs=_on_build_log,
        )
        logger.info("Created sandbox %s for trial %s", self._sandbox.id, self.trial_id)

    def setup(
        self,
        seed_dir: Path,
        universe: str,
        loop_prompt_template: str,
        wheels: list[Path] | None = None,
    ) -> None:
        """Scaffold the project via ``lc init`` and overlay task seed files.

        The scaffold (``.claude/``, ``CLAUDE.md``, ``Containerfile``,
        ``requirements.txt``, ``.lightcone/``, ``.gitignore``, default
        ``universes/baseline.yaml``) is produced by the same ``lc init``
        command users run — no separate uploaded copies of skills/hooks
        to drift out of sync. The task seed dir contributes only the
        bits that ``lc init`` cannot generate: ``astra.yaml``, ``data/``,
        and ``task.yaml``.
        """
        assert self._sandbox is not None, "Call create() first"

        # Install dependency wheels system-wide so `lc` and `astra` are
        # importable before we run them (lc init below, astra universe
        # generate after the overlay).
        if wheels:
            self._install_wheels(wheels)

        # Seed a minimal global config before invoking `lc`. `lc`'s main
        # group would auto-create this, but writing it explicitly pins
        # the runtime for reproducibility across eval runs.
        self.exec(
            "mkdir -p ~/.lightcone"
            " && printf 'container:\\n  runtime: auto\\n' > ~/.lightcone/config.yaml"
        )

        # Scaffold the project using the lc init from the wheel under
        # test. --no-venv: deps are pre-installed system-wide on the
        # sandbox image. --no-git: we run git init below, after the
        # overlay, so the seed commit captures the task files too.
        result = self.exec(
            f"mkdir -p {self.WORK_DIR}"
            f" && lc init {self.WORK_DIR} --no-git --no-venv",
            timeout=120,
        )
        if result.exit_code != 0:
            raise RuntimeError(
                f"`lc init` failed (exit {result.exit_code}):\n"
                f"{result.output[-2000:]}"
            )

        # Overlay task seed files (astra.yaml, data/, task.yaml). The
        # task's astra.yaml replaces the one `astra init` boilerplated.
        self._upload_directory(seed_dir, self.WORK_DIR)

        # Regenerate baseline.yaml from the task astra.yaml's defaults.
        # The one lc init produced matches the boilerplate astra.yaml
        # (different decisions), so it would no longer be coherent.
        self.exec(
            f"cd {self.WORK_DIR}"
            f" && rm -f universes/baseline.yaml"
            f" && astra universe generate -n baseline"
            f" -d 'Default configuration using standard practices'",
            timeout=60,
        )

        # Template and stage the loop prompt for the agent.
        prompt = loop_prompt_template.replace("{{UNIVERSE}}", universe)
        self.upload_file("/tmp/loop-prompt.md", prompt.encode())

        # Initial commit captures the full project state (scaffold +
        # task overlay + regenerated universe).
        self.exec(
            f"cd {self.WORK_DIR}"
            " && git config --global user.name Eval"
            " && git config --global user.email eval@lightcone"
            " && git init -q && git add -A && git commit -q -m 'seed'"
        )

    def exec(self, cmd: str, timeout: int = 300, cwd: str | None = None) -> ExecuteResult:
        """Execute a command in the sandbox."""
        assert self._sandbox is not None, "Call create() first"

        result = self._sandbox.process.exec(cmd, cwd=cwd, timeout=timeout)
        return ExecuteResult(
            exit_code=result.exit_code,
            output=result.result or "",
        )

    def exec_claude(
        self,
        max_turns: int = 25,
        timeout: int = 600,
        model: str | None = None,
    ) -> AgentResult:
        """Run Claude Code headless against the staged loop prompt.

        Thin back-compat wrapper over :class:`ClaudeHarness`. The multi-harness
        trial loop calls ``harness.invoke(sandbox, ...)`` directly; this stays so
        existing callers and tests keep working.
        """
        from lightcone.eval.harnesses.claude import ClaudeHarness

        return ClaudeHarness().invoke(
            self,
            prompt_path="/tmp/loop-prompt.md",
            work_dir=self.WORK_DIR,
            max_turns=max_turns,
            timeout=timeout,
            with_skills=True,
            model=model,
        )

    def exec_async_poll(
        self, cmd: str, timeout: int = 600, poll_interval: int = 10
    ) -> ExecuteResult:
        """Execute a command asynchronously via Daytona sessions and poll for completion.

        Unlike process.exec() which makes a single blocking HTTP call (and drops
        on long-running commands due to gateway timeouts), this uses:
          1. create_session — establish a persistent session
          2. execute_session_command(run_async=True) — fire-and-forget
          3. get_session_command — poll until exit_code is set
          4. get_session_command_logs — retrieve output
        """
        from daytona_sdk._sync.process import SessionExecuteRequest

        session_id = f"claude-{self.trial_id}"
        proc = self._sandbox.process

        proc.create_session(session_id)
        try:
            req = SessionExecuteRequest(command=cmd, run_async=True)
            resp = proc.execute_session_command(session_id, req, timeout=60)
            cmd_id = resp.cmd_id

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                time.sleep(poll_interval)
                cmd_info = proc.get_session_command(session_id, cmd_id)
                if cmd_info.exit_code is not None:
                    logs = proc.get_session_command_logs(session_id, cmd_id)
                    return ExecuteResult(
                        exit_code=cmd_info.exit_code,
                        output=logs.stdout or logs.output or "",
                    )

            # Timeout reached — kill and return error
            logger.warning("Command timed out after %ds in trial %s", timeout, self.trial_id)
            return ExecuteResult(exit_code=1, output=f"Timed out after {timeout}s")
        finally:
            try:
                proc.delete_session(session_id)
            except Exception:
                logger.debug("Failed to delete session %s", session_id, exc_info=True)

    def upload_file(self, remote_path: str, content: bytes) -> None:
        """Upload a file to the sandbox."""
        assert self._sandbox is not None, "Call create() first"
        self._sandbox.fs.upload_file(content, remote_path)

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

    def _install_wheels(self, wheels: list[Path]) -> None:
        """Upload and install the lightcone-cli wheel into the sandbox.

        The wheel is built from the branch under test in
        :func:`lightcone.eval.build.build_eval_wheels`. ``--no-deps``
        because every runtime dep is already in the sandbox image;
        ``--force-reinstall`` so the local wheel always overrides any
        pre-existing install and we never silently run a PyPI version.
        """
        self.exec("mkdir -p /tmp/deps")

        remote_paths: list[str] = []
        for whl in wheels:
            remote_path = f"/tmp/deps/{whl.name}"
            self.upload_file(remote_path, whl.read_bytes())
            remote_paths.append(remote_path)

        whl_cmd = (
            "pip install --no-deps --force-reinstall "
            + " ".join(shlex.quote(p) for p in remote_paths)
        )
        result = self.exec(whl_cmd, timeout=120)
        if result.exit_code != 0:
            logger.warning(
                "Failed to install wheels (exit %d):\n...%s",
                result.exit_code,
                result.output[-2000:],
            )
        else:
            logger.info("Installed wheels: %s", [w.name for w in wheels])

    def _upload_directory(self, local_dir: Path, remote_dir: str) -> None:
        """Upload a local directory tree to the sandbox."""
        for local_path in local_dir.rglob("*"):
            if local_path.is_file():
                rel = local_path.relative_to(local_dir)
                remote_path = f"{remote_dir}/{rel}"
                self.upload_file(remote_path, local_path.read_bytes())
