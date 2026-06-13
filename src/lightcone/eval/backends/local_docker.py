"""Local-Docker sandbox backend for the eval harness.

Runs each trial in a local Docker container — the on-host/CI counterpart of the
Daytona :class:`lightcone.eval.sandbox.EvalSandbox`. Drives the ``docker`` CLI
via ``subprocess`` (no docker SDK), and mirrors ``EvalSandbox``'s public surface
and ``setup()`` body so harnesses run unchanged.

The image (``lc-eval-local:latest``) bakes in every registered harness's agent
CLI plus the third-party Python deps the Daytona image pre-installs; it is built
once and cached by tag. Auth credentials are copied in per-container (``docker
cp`` + ``chown``) rather than bind-mounted, because the host credential files are
``0600`` and host-uid-owned and would not be readable by the container's
``evaluser``.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path

from lightcone.eval.backends.base import ExecuteResult, Sandbox
from lightcone.eval.harnesses import available_harnesses, get_harness
from lightcone.eval.harnesses.base import Harness

logger = logging.getLogger(__name__)

#: Shared image tag — built once, reused across trials.
IMAGE_TAG = "lc-eval-local:latest"

#: Third-party Python deps the Daytona image pre-installs system-wide. Kept
#: verbatim in sync with ``EvalSandbox.create``'s ``deps`` so the local image
#: resolves ``lc`` / ``astra`` identically (the lightcone-cli wheel is installed
#: ``--no-deps`` at setup time, so every runtime dep it needs is listed here).
DEPS = (
    "astra-tools astra-spec"
    " jinja2 jsonschema"
    " snakemake snakemake-interface-executor-plugins"
    " snakemake-interface-common dask distributed"
)

#: Per-harness host auth files to copy into the container, keyed by harness name.
#: Each entry is ``(host_path, container_path)``; host paths may use ``~``.
#: Missing host files are skipped (a harness without credentials is *skipped*,
#: not failed — mirrors ``credential_env_keys`` semantics).
CREDENTIAL_FILES: dict[str, list[tuple[str, str]]] = {
    "claude": [("~/.claude.json", "/home/evaluser/.claude.json")],
    "codex": [("~/.codex/auth.json", "/home/evaluser/.codex/auth.json")],
    "pi": [
        ("~/.pi/agent/auth.json", "/home/evaluser/.pi/agent/auth.json"),
        ("~/.pi/agent/models.json", "/home/evaluser/.pi/agent/models.json"),
        ("~/.pi/agent/settings.json", "/home/evaluser/.pi/agent/settings.json"),
    ],
}


def _sanitize(name: str) -> str:
    """Coerce ``name`` to the docker container-name charset ``[a-zA-Z0-9_.-]``."""
    return re.sub(r"[^a-zA-Z0-9_.-]", "-", name)


def _install_lines() -> list[str]:
    """One ``RUN`` line per registered harness's install commands, adapted so the
    agent CLI lands on ``PATH`` for ``evaluser``.

    Two install shapes exist among the harnesses:

    - **npm globals** (codex, pi): run as root (the default ``RUN`` user) so the
      binary lands in ``/usr/local/bin`` — on ``PATH`` for every user.
    - **Claude's curl installer**, whose command ends ``&& cp /root/.local/bin/
      claude /usr/local/bin/claude`` (assuming a root install): the ``cp`` source
      doesn't exist when run as ``evaluser``. We rewrite it to install as
      ``evaluser`` (so its config lands under ``/home/evaluser``) and symlink from
      ``/home/evaluser/.local/bin`` into ``/usr/local/bin`` as root.
    """
    lines: list[str] = []
    for hname in available_harnesses():
        for cmd in get_harness(hname).install_commands():
            # Claude idiom: `<installer> && cp /root/.local/bin/<x> /usr/local/bin/<x>`.
            m = re.search(r"cp\s+/root/\.local/bin/(\S+)\s+/usr/local/bin/\S+", cmd)
            if m:
                binary = m.group(1)
                installer = cmd[: m.start()].rstrip(" &")
                lines.append(f"RUN su - evaluser -c {shlex.quote(installer)}")
                lines.append(
                    f"RUN ln -sf /home/evaluser/.local/bin/{binary} /usr/local/bin/{binary}"
                )
            else:
                # npm -g and friends: root install → /usr/local/bin.
                lines.append(f"RUN {cmd}")
    return lines


def _build_dockerfile() -> str:
    """Generate the Dockerfile for the shared eval image (all harnesses baked in)."""
    install = "\n".join(_install_lines())
    # Node 22 (NodeSource) — pi (`@earendil-works/pi-coding-agent`) needs
    # node >=22.19.0; Debian slim's `nodejs` apt package is v20 and crashes pi at
    # runtime (`webidl.util.markAsUncloneable is not a function`). codex and
    # claude are unaffected, but we install one modern Node for all npm globals.
    return f"""FROM python:3.12-slim
RUN apt-get update && apt-get install -y git curl bash sudo \\
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \\
    && apt-get install -y nodejs \\
    && rm -rf /var/lib/apt/lists/*
RUN useradd -m -s /bin/bash evaluser \\
    && echo 'evaluser ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers
RUN pip install --no-cache-dir {DEPS}
{install}
RUN mkdir -p /home/evaluser/.claude \\
    && echo '{{"hasCompletedOnboarding": true}}' > /home/evaluser/.claude.json \\
    && chown -R evaluser:evaluser /home/evaluser/.claude /home/evaluser/.claude.json
"""


class LocalDockerSandbox(Sandbox):
    """Runs one eval trial inside a local Docker container.

    The container is started detached (``sleep infinity``) and each ``exec`` is a
    fresh ``docker exec``; ``teardown`` removes it. The image is shared across
    trials and built once (cached by tag).
    """

    def __init__(
        self,
        task_id: str,
        trial_id: str,
        harness: Harness,
        env_vars: dict[str, str],
    ) -> None:
        self.task_id = task_id
        self.trial_id = trial_id
        self.harness = harness
        self.env_vars = env_vars
        self.cname = _sanitize(f"lc-eval-{trial_id}")

    # ------------------------------------------------------------------ lifecycle

    def create(self) -> None:
        """Build the shared image (if not cached) and start the trial container."""
        self._ensure_image()

        env_args: list[str] = []
        for key, val in self.env_vars.items():
            env_args += ["-e", f"{key}={val}"]
        # Forward host credentials this harness declares, when set in the env.
        for key in self.harness.credential_env_keys():
            val = os.environ.get(key)
            if val:
                env_args += ["-e", f"{key}={val}"]

        self._run(
            [
                "docker", "run", "-d",
                "--name", self.cname,
                "-u", "evaluser",
                *env_args,
                "-w", "/home/evaluser",
                IMAGE_TAG,
                "sleep", "infinity",
            ]
        )
        logger.info("Started container %s for trial %s", self.cname, self.trial_id)

        self._copy_credentials()

    def _ensure_image(self) -> None:
        """Build ``lc-eval-local:latest`` unless already present (tag cache)."""
        present = subprocess.run(
            ["docker", "image", "inspect", IMAGE_TAG],
            capture_output=True,
        )
        if present.returncode == 0:
            logger.info("Reusing cached eval image %s", IMAGE_TAG)
            return

        dockerfile = _build_dockerfile()
        logger.info("Building eval image %s …", IMAGE_TAG)
        proc = subprocess.run(
            ["docker", "build", "-t", IMAGE_TAG, "-f", "-", "."],
            input=dockerfile,
            text=True,
            capture_output=True,
        )
        for line in (proc.stdout + proc.stderr).splitlines():
            logger.info("[image build] %s", line.rstrip())
        if proc.returncode != 0:
            raise RuntimeError(f"Failed to build {IMAGE_TAG} (exit {proc.returncode})")
        logger.info("Built eval image %s", IMAGE_TAG)

    def _copy_credentials(self) -> None:
        """Copy host auth files into the container (``docker cp`` + ``chown``).

        Bind mounts won't work: host credential files are ``0600`` and
        host-uid-owned, unreadable by the container's ``evaluser``. So we copy
        them in and re-own to ``evaluser``. Missing host files are skipped.
        """
        for host_raw, container_path in CREDENTIAL_FILES.get(self.harness.name, []):
            host_path = Path(host_raw).expanduser()
            if not host_path.is_file():
                continue
            parent = os.path.dirname(container_path)
            self._run(["docker", "exec", "-u", "evaluser", self.cname, "mkdir", "-p", parent])
            self._run(["docker", "cp", str(host_path), f"{self.cname}:{container_path}"])
            self._run(
                ["docker", "exec", "-u", "root", self.cname,
                 "chown", "-R", "evaluser:evaluser", container_path]
            )

    def teardown(self) -> None:
        """Force-remove the container. Idempotent (errors ignored)."""
        subprocess.run(
            ["docker", "rm", "-f", self.cname],
            capture_output=True,
        )

    # ------------------------------------------------------------------ exec / io

    def exec(self, cmd: str, timeout: int = 300, cwd: str | None = None) -> ExecuteResult:
        """Run ``cmd`` as ``evaluser`` via ``docker exec ... bash -lc``."""
        full = f"cd {shlex.quote(cwd)} && {cmd}" if cwd else cmd
        try:
            proc = subprocess.run(
                ["docker", "exec", "-u", "evaluser", self.cname, "bash", "-lc", full],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return ExecuteResult(124, f"Timed out after {timeout}s")
        return ExecuteResult(proc.returncode, proc.stdout + proc.stderr)

    def exec_async_poll(
        self, cmd: str, timeout: int = 600, poll_interval: int = 10
    ) -> ExecuteResult:
        """Run a long command. Locally a blocking ``docker exec`` is fine — there
        is no gateway timeout to drop the connection — so delegate to ``exec``."""
        return self.exec(cmd, timeout=timeout)

    def upload_file(self, remote_path: str, content: bytes) -> None:
        """Write ``content`` to ``remote_path`` inside the container."""
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            parent = os.path.dirname(remote_path)
            if parent:
                self._run(
                    ["docker", "exec", "-u", "evaluser", self.cname, "mkdir", "-p", parent]
                )
            self._run(["docker", "cp", tmp_path, f"{self.cname}:{remote_path}"])
            self._run(
                ["docker", "exec", "-u", "root", self.cname,
                 "chown", "evaluser:evaluser", remote_path]
            )
        finally:
            os.unlink(tmp_path)

    # ------------------------------------------------------------------ setup

    def setup(
        self,
        seed_dir: Path,
        universe: str,
        loop_prompt_template: str,
        wheels: list[Path] | None = None,
    ) -> None:
        """Scaffold the project via ``lc init`` and overlay task seed files.

        Faithful to :meth:`EvalSandbox.setup`: write the global config, run
        ``lc init --no-git --no-venv``, overlay the task seed dir, regenerate the
        baseline universe, stage the loop prompt, then ``git init`` + seed commit.
        Wheels are installed system-wide (via ``sudo``) so ``lc`` / ``astra``
        resolve before they're invoked.
        """
        if wheels:
            self._install_wheels(wheels)

        # Pin the global config explicitly for reproducibility (lc would
        # auto-create it, but writing it makes the runtime deterministic).
        self.exec(
            "mkdir -p ~/.lightcone"
            " && printf 'container:\\n  runtime: auto\\n' > ~/.lightcone/config.yaml"
        )

        # Scaffold from the wheel under test. --no-venv: deps are system-wide.
        # --no-git: we git init below, after the overlay, so the seed commit
        # captures the task files too.
        result = self.exec(
            f"mkdir -p {self.WORK_DIR} && lc init {self.WORK_DIR} --no-git --no-venv",
            timeout=120,
        )
        if result.exit_code != 0:
            raise RuntimeError(
                f"`lc init` failed (exit {result.exit_code}):\n{result.output[-2000:]}"
            )

        # Overlay task seed files (astra.yaml, data/, task.yaml).
        self._upload_directory(seed_dir, self.WORK_DIR)

        # Regenerate baseline.yaml from the task astra.yaml's defaults (the one
        # lc init produced matches the boilerplate astra.yaml, now replaced).
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

        # Initial commit captures the full project state.
        self.exec(
            f"cd {self.WORK_DIR}"
            " && git config --global user.name Eval"
            " && git config --global user.email eval@lightcone"
            " && git init -q && git add -A && git commit -q -m 'seed'"
        )

    def _install_wheels(self, wheels: list[Path]) -> None:
        """Upload the lightcone-cli wheel(s) and install them system-wide.

        ``--no-deps`` (every runtime dep is baked into the image) and
        ``--force-reinstall`` (the local wheel always overrides any PyPI version).
        ``sudo`` so the install is system-wide and ``lc`` / ``astra`` resolve for
        ``evaluser`` — mirroring the Daytona image's root-installed deps.
        """
        self.exec("mkdir -p /tmp/deps")

        remote_paths: list[str] = []
        for whl in wheels:
            remote_path = f"/tmp/deps/{whl.name}"
            self.upload_file(remote_path, whl.read_bytes())
            remote_paths.append(remote_path)

        whl_cmd = (
            "sudo pip install --no-deps --force-reinstall "
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
        """Upload a local directory tree into the container."""
        for local_path in local_dir.rglob("*"):
            if local_path.is_file():
                rel = local_path.relative_to(local_dir)
                self.upload_file(f"{remote_dir}/{rel}", local_path.read_bytes())

    # ------------------------------------------------------------------ helpers

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        """Run a docker CLI command, raising with captured output on failure."""
        proc = subprocess.run(args, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"`{' '.join(args)}` failed (exit {proc.returncode}):\n"
                f"{(proc.stdout + proc.stderr)[-2000:]}"
            )
        return proc
