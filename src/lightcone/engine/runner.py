"""ASTRA Container Runner — executes recipes in Docker/Podman, locally, or via SLURM."""
from __future__ import annotations

import logging
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Maximum number of characters to keep from stdout/stderr for metadata.
_TAIL_CHARS = 2000


def _read_tail(path: str, max_chars: int) -> str:
    """Return the last *max_chars* characters of *path*, empty if missing."""
    try:
        with open(path) as f:
            data = f.read()
    except OSError:
        return ""
    return data[-max_chars:] if len(data) > max_chars else data


def _run_streaming(
    cmd: list[str] | str,
    *,
    shell: bool = False,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run a command, streaming stdout/stderr to the terminal in real time.

    Returns ``(returncode, stdout_tail, stderr_tail)`` where each tail
    contains at most the last ``_TAIL_CHARS`` characters of output.
    """
    import selectors

    stream_env = dict(env) if env else dict(os.environ)
    stream_env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=shell,
        cwd=cwd,
        env=stream_env,
    )

    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)
    sel.register(proc.stderr, selectors.EVENT_READ)

    stdout_tail: list[str] = []
    stderr_tail: list[str] = []
    stdout_len = 0
    stderr_len = 0

    open_streams = 2
    while open_streams > 0:
        for key, _ in sel.select():
            line = key.fileobj.readline()
            if not line:
                sel.unregister(key.fileobj)
                open_streams -= 1
                continue
            if key.fileobj is proc.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                stdout_tail.append(line)
                stdout_len += len(line)
                while stdout_len > _TAIL_CHARS and len(stdout_tail) > 1:
                    stdout_len -= len(stdout_tail.pop(0))
            else:
                sys.stderr.write(line)
                sys.stderr.flush()
                stderr_tail.append(line)
                stderr_len += len(line)
                while stderr_len > _TAIL_CHARS and len(stderr_tail) > 1:
                    stderr_len -= len(stderr_tail.pop(0))

    proc.wait()
    sel.close()
    return proc.returncode, "".join(stdout_tail), "".join(stderr_tail)



def _find_venv(cwd: str | None, project_root: Path) -> Path | None:
    """Find .venv by checking cwd first, then walking up to project_root."""
    if cwd:
        cwd_path = Path(cwd)
        venv = cwd_path / ".venv"
        if (venv / "bin" / "python").exists():
            return venv
        # Walk up to project_root
        current = cwd_path.parent
        root_resolved = project_root.resolve()
        while current >= root_resolved:
            venv = current / ".venv"
            if (venv / "bin" / "python").exists():
                return venv
            if current == root_resolved:
                break
            current = current.parent

    # Fall back to project root
    venv = project_root / ".venv"
    if (venv / "bin" / "python").exists():
        return venv
    return None


def _substitute_python(command: str, python_path: str) -> str:
    """Replace a leading ``python `` with a specific interpreter path."""
    if command.startswith("python "):
        return python_path + command[len("python"):]
    return command


@dataclass
class ExecutionResult:
    """Result of executing a recipe."""
    exit_code: int
    output_path: Path
    metadata: dict[str, Any] = field(default_factory=dict)


def _build_cli_args(params: dict[str, Any], universe_id: str) -> list[str]:
    """Build CLI arguments from universe decisions."""
    args = ["--universe", universe_id]
    for key, value in params.items():
        args.extend([f"--{key}", str(value)])
    return args


def translate_resources_to_docker_flags(resources: dict[str, Any]) -> list[str]:
    """Translate ASTRA resource requirements to Docker CLI flags.

    ``resources.gpus`` is per-node; Docker runs on a single node, so the
    value maps directly to ``--gpus=N``.
    """
    flags: list[str] = []
    if cpus := resources.get("cpus"):
        flags.append(f"--cpus={cpus}")
    if memory := resources.get("memory"):
        flags.append(f"--memory={memory.lower()}")
    if gpus := resources.get("gpus"):
        flags.append(f"--gpus={gpus}")
    return flags


def build_recipe_shell_command(
    command: str,
    container: str | None,
    container_runtime: str | None,
    project_root: Path,
    resources: dict[str, Any],
    cwd: str,
    external_inputs: dict[str, str] | None,
) -> str:
    """Compose the shell command that executes a recipe inside a worker.

    This is the same string the old SLURM backend wrote into an sbatch
    script body (minus the ``#SBATCH`` headers): cd to cwd, optionally
    set up symlinks or volume mounts for external inputs, then run the
    command — wrapping it in ``podman-hpc run`` when a container is
    requested.

    Used by both the Parsl-backed SLURM runner and any direct-shell
    debugging. The output is a single multi-line shell script suitable
    for ``bash -c`` or ``parsl.bash_app``.
    """
    lines = [f"cd {shlex.quote(cwd)}"]

    if container and container_runtime == "podman-hpc":
        lines.append(_podman_hpc_run_command_inline(
            command=command,
            container=container,
            project_root=project_root,
            resources=resources,
            external_inputs=external_inputs,
        ))
        return "\n".join(lines)

    # No container — symlink external inputs into ./data/ for the recipe to read
    if external_inputs:
        lines.append("mkdir -p data")
        for input_id, source in sorted(external_inputs.items()):
            src = shlex.quote(str(source))
            dst = shlex.quote(f"data/{input_id}")
            lines.append(f"ln -sfn {src} {dst}")

    lines.append(command)
    return "\n".join(lines)


def _podman_hpc_run_command_inline(
    command: str,
    container: str,
    project_root: Path,
    resources: dict[str, Any],
    external_inputs: dict[str, str] | None = None,
) -> str:
    """Build a podman-hpc run invocation as a single shell command string.

    Mirrors the old ``_podman_hpc_run_command`` but without a
    ``scheduler_config`` parameter — the ``extra_container_flags`` escape
    hatch is now read from the pilot config when constructing the
    SlurmProvider, and per-task container flags aren't a concept we need.
    """
    parts = ["podman-hpc", "run", "--rm"]

    if resources.get("gpus"):
        parts.append("--gpu")
    if resources.get("nodes", 1) > 1:
        parts.append("--mpi")

    parts.extend(["-v", shlex.quote(f"{project_root}:/workspace"), "-w", "/workspace"])

    for input_id, source in sorted((external_inputs or {}).items()):
        parts.extend(["-v", shlex.quote(f"{source}:/workspace/data/{input_id}:ro")])

    parts.append(container)
    parts.extend(["sh", "-c", shlex.quote(command)])

    return " ".join(parts)


class ASTRAContainerRunner:
    """Executes ASTRA recipes via Docker, local subprocess, or SLURM.

    When backend is "docker", attempts Docker execution first.  If Docker
    fails (missing image, daemon not running, non-zero exit), falls back to
    local subprocess execution with a warning.
    """

    def __init__(
        self,
        project_root: str,
        backend: str = "docker",
        default_container: str | None = None,
        target_config: dict[str, Any] | None = None,
        container_runtime: str | None = None,
    ):
        self.project_root = Path(project_root)
        self.backend = backend
        self.default_container = default_container
        self.target_config = target_config or {}
        self.container_runtime = container_runtime
        self._venv_deps_checked = False

    def execute(
        self,
        command: str,
        output_id: str,
        universe_id: str,
        container: str | None = None,
        inputs: list[str] | None = None,
        resources: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        external_inputs: dict[str, str] | None = None,
        cwd_override: str | None = None,
    ) -> ExecutionResult:
        """Execute a recipe, dispatching to the configured backend.

        For the "docker" backend, falls back to local execution when Docker
        is unavailable or the container run fails.
        """
        cli_args = _build_cli_args(params or {}, universe_id)
        full_command = (command + " " + " ".join(cli_args)).strip()
        results_dir = self.project_root / "results" / universe_id
        results_dir.mkdir(parents=True, exist_ok=True)

        # Effective working directory: cwd_override (for sub-analysis recipes)
        # or project_root
        effective_cwd = cwd_override or str(self.project_root)

        if self.backend == "local":
            return self._run_local(
                full_command, output_id, universe_id, cwd=effective_cwd,
            )

        if self.backend == "slurm":
            return self._run_slurm(
                command=full_command,
                container=container or self.default_container,
                input_ids=inputs or [],
                output_id=output_id,
                universe_id=universe_id,
                resources=resources or {},
                external_inputs=external_inputs,
                cwd=effective_cwd,
            )

        if self.backend == "venv":
            return self._run_venv(
                full_command, output_id, universe_id, cwd=effective_cwd,
            )

        # Container backend — try container runtime, fall back to venv or local.
        # Note: the explicit "local" backend (set via target config) skips dep
        # installation and runs in the current Python env.  The implicit fallback
        # here goes to _run_venv (with dep installation) when .venv is present,
        # and only falls back to _run_local when .venv is absent.
        effective_container = container or self.default_container
        if effective_container:
            result = self._run_container(
                command=full_command,
                container=effective_container,
                universe_id=universe_id,
                resources=resources or {},
                runtime=self.container_runtime or "docker",
            )
            if result.exit_code == 0:
                return result
            # Container failed — fall back to venv (or local if venv is absent)
            logger.warning(
                "%s execution failed for '%s' (exit code %d). "
                "Falling back to venv execution.\n  stderr: %s",
                self.container_runtime or "docker", output_id, result.exit_code,
                result.metadata.get("stderr", "")[:200],
            )

        venv_python = self.project_root / ".venv" / "bin" / "python"
        if venv_python.exists():
            return self._run_venv(
                command=full_command,
                output_id=output_id,
                universe_id=universe_id,
                warn=effective_container is not None,
                cwd=effective_cwd,
            )

        # No .venv available — fall back to the current Python environment so
        # that projects without a venv (e.g. pre-existing installs that predate
        # lc init) continue to work rather than surfacing a confusing error.
        logger.warning(
            "No .venv found for '%s'; executing locally without dep isolation. "
            "Run 'lc init' to create a project venv with dependencies installed.",
            output_id,
        )
        return self._run_local(
            command=full_command,
            output_id=output_id,
            universe_id=universe_id,
            warn=effective_container is not None,
            cwd=effective_cwd,
        )

    def _run_container(
        self,
        command: str,
        container: str,
        universe_id: str,
        resources: dict[str, Any],
        runtime: str = "docker",
    ) -> ExecutionResult:
        """Execute a recipe in a container (Docker or Podman).

        Mounts the project root at /workspace so scripts can read data and
        write results using their normal relative paths.
        """
        cmd = [runtime, "run", "--rm"]
        cmd.extend(translate_resources_to_docker_flags(resources))
        cmd.extend([
            "-v", f"{self.project_root}:/workspace",
            "-w", "/workspace",
            container,
            "sh", "-c", command,
        ])

        try:
            returncode, stdout_tail, stderr_tail = _run_streaming(cmd)
        except FileNotFoundError:
            return ExecutionResult(
                exit_code=127,
                output_path=self.project_root / "results" / universe_id,
                metadata={"stderr": f"{runtime}: command not found"},
            )

        return ExecutionResult(
            exit_code=returncode,
            output_path=self.project_root / "results" / universe_id,
            metadata={
                "stdout": stdout_tail,
                "stderr": stderr_tail,
                "backend": runtime,
                "container_command": " ".join(cmd),
            },
        )

    def _run_local(
        self,
        command: str,
        output_id: str,
        universe_id: str,
        warn: bool = False,
        cwd: str | None = None,
    ) -> ExecutionResult:
        """Execute a recipe as a local subprocess.

        Uses the current Python environment.  Decision parameters are passed
        as CLI arguments.
        """
        if warn:
            logger.warning(
                "Executing '%s' locally (no container). "
                "Results may differ from containerised execution.",
                output_id,
            )

        full_command = _substitute_python(command, sys.executable)

        returncode, stdout_tail, stderr_tail = _run_streaming(
            full_command, shell=True, cwd=cwd or str(self.project_root),
        )

        output_path = self.project_root / "results" / universe_id
        return ExecutionResult(
            exit_code=returncode,
            output_path=output_path,
            metadata={
                "stdout": stdout_tail,
                "stderr": stderr_tail,
                "backend": "local",
            },
        )

    def _run_venv(
        self,
        command: str,
        output_id: str,
        universe_id: str,
        warn: bool = False,
        cwd: str | None = None,
    ) -> ExecutionResult:
        """Execute a recipe in the project's virtual environment.

        Uses the ``.venv/`` created by ``lc init``.  Ensures that
        dependencies from ``requirements*.txt`` are installed before
        running, using a hash-based marker to skip redundant installs.

        For sub-analysis recipes, the venv is resolved by walking up from
        the working directory to the project root.
        """
        if warn:
            logger.warning(
                "Executing '%s' in project venv. "
                "Results may differ from containerised execution.",
                output_id,
            )

        # Find venv: check cwd first, then walk up to project root
        venv_path = _find_venv(cwd, self.project_root)

        if venv_path is None:
            return ExecutionResult(
                exit_code=1,
                output_path=self.project_root / "results" / universe_id,
                metadata={
                    "stderr": (
                        "No .venv found in project root. "
                        "Run 'lc init' or create a virtual environment first."
                    ),
                    "backend": "venv",
                },
            )

        venv_python = venv_path / "bin" / "python"

        # Ensure dependencies are installed
        self._ensure_venv_deps(venv_path)

        full_command = _substitute_python(command, str(venv_python))

        env = {
            **os.environ,
            "VIRTUAL_ENV": str(venv_path),
            "PATH": f"{venv_path / 'bin'}:{os.environ.get('PATH', '')}",
        }

        returncode, stdout_tail, stderr_tail = _run_streaming(
            full_command, shell=True, cwd=cwd or str(self.project_root), env=env,
        )

        output_path = self.project_root / "results" / universe_id
        return ExecutionResult(
            exit_code=returncode,
            output_path=output_path,
            metadata={
                "stdout": stdout_tail,
                "stderr": stderr_tail,
                "backend": "venv",
                "venv_path": str(venv_path),
            },
        )

    def _ensure_venv_deps(self, venv_path: Path) -> None:
        """Install requirements into venv if they have changed.

        Computes a hash of all ``requirements*.txt`` files and compares
        it to a marker file (``.venv/.deps-hash``).  If the hash matches,
        installation is skipped.  Once checked successfully within this
        runner instance, subsequent calls are no-ops.
        """
        if self._venv_deps_checked:
            return

        from lightcone.engine.container import find_dependency_files, hash_file_contents

        dep_files = find_dependency_files(self.project_root)
        req_files = [f for f in dep_files if f.name.startswith("requirements")]
        if not req_files:
            return

        current_hash = hash_file_contents(req_files)

        marker = venv_path / ".deps-hash"
        if marker.exists() and marker.read_text().strip() == current_hash:
            return

        pip_path = venv_path / "bin" / "pip"
        all_installed = True
        for req_file in req_files:
            logger.info("Installing dependencies from %s into .venv ...", req_file.name)
            install_result = subprocess.run(
                [str(pip_path), "install", "-r", str(req_file)],
                capture_output=True,
                text=True,
                cwd=str(self.project_root),
            )
            if install_result.returncode != 0:
                logger.warning(
                    "pip install -r %s failed: %s",
                    req_file.name, install_result.stderr[:200],
                )
                all_installed = False

        # Only record the hash when all installs succeeded — a failed install
        # that wrote the marker would be silently skipped on the next run.
        if all_installed:
            marker.write_text(current_hash + "\n")
        self._venv_deps_checked = True

    def _run_slurm(
        self,
        command: str,
        container: str | None,
        input_ids: list[str],
        output_id: str,
        universe_id: str,
        resources: dict[str, Any],
        external_inputs: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> ExecutionResult:
        """Execute a recipe via Parsl into a pre-loaded pilot allocation.

        Assumes ``parsl.load(config)`` has been called by the CLI layer
        (see ``lc run``). Routes the recipe to one of the configured
        pilot executors based on its resources, builds a ``bash_app``,
        and awaits the result. Stdout/stderr are read from Parsl's
        per-task log files.
        """
        from parsl.app.app import bash_app
        from parsl.app.errors import BashExitFailure

        from lightcone.engine.parsl_backend import (
            pick_executor,
            recipe_resources_to_parsl,
        )

        pilots = self.target_config.get("pilots") or {}
        label = pick_executor(resources, pilots)
        spec = recipe_resources_to_parsl(resources)
        container_runtime = self.target_config.get("container_runtime", "podman-hpc")

        full_cmd = build_recipe_shell_command(
            command=command,
            container=container,
            container_runtime=container_runtime,
            project_root=self.project_root,
            resources=resources,
            cwd=cwd or str(self.project_root),
            external_inputs=external_inputs,
        )

        @bash_app(executors=[label])
        def _run(parsl_resource_specification: dict[str, Any] | None = None,
                 stdout: str | None = None,
                 stderr: str | None = None) -> str:  # noqa: ARG001 — Parsl reads these via name
            return full_cmd

        # Parsl's AUTO_LOGNAME picks unique paths under run_dir/task_logs/
        import parsl
        fut = _run(
            parsl_resource_specification=spec or None,
            stdout=parsl.AUTO_LOGNAME,
            stderr=parsl.AUTO_LOGNAME,
        )

        try:
            fut.result()
            exit_code = 0
        except BashExitFailure as e:
            exit_code = e.exitcode
        except Exception as e:
            # Pilot expired, worker died, etc.
            return ExecutionResult(
                exit_code=1,
                output_path=self.project_root / "results" / universe_id,
                metadata={
                    "backend": "slurm",
                    "executor": label,
                    "stderr": f"parsl task failed: {e!r}",
                },
            )

        stdout_tail = _read_tail(fut.stdout, _TAIL_CHARS) if fut.stdout else ""
        stderr_tail = _read_tail(fut.stderr, _TAIL_CHARS) if fut.stderr else ""

        return ExecutionResult(
            exit_code=exit_code,
            output_path=self.project_root / "results" / universe_id,
            metadata={
                "backend": "slurm",
                "executor": label,
                "stdout": stdout_tail,
                "stderr": stderr_tail,
            },
        )
