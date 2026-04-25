"""ASTRA container runner — executes recipes locally or in a container.

The runner does not know about SLURM.  When a pilot is active, Dagster's
``dagster-dask`` executor ships each step to a Dask worker on a compute
node; on that worker, the runner executes the asset body exactly as it
would on the orchestrator host (it shells out to the configured container
runtime, e.g. ``podman-hpc run …``).  All sbatch-related machinery lives
in :mod:`lightcone.engine.pilots`.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Maximum number of characters to keep from stdout/stderr for metadata.
_TAIL_CHARS = 2000


def _run_streaming(
    cmd: list[str] | str,
    *,
    shell: bool = False,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run a command, streaming stdout/stderr to the terminal in real time."""
    import selectors

    stream_env = dict(env) if env else dict(os.environ)
    stream_env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, shell=shell, cwd=cwd, env=stream_env,
    )

    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)
    sel.register(proc.stderr, selectors.EVENT_READ)

    stdout_tail: list[str] = []
    stderr_tail: list[str] = []
    stdout_len = stderr_len = 0
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
    """Find ``.venv`` by checking *cwd* first, then walking up to *project_root*."""
    if cwd:
        cwd_path = Path(cwd)
        venv = cwd_path / ".venv"
        if (venv / "bin" / "python").exists():
            return venv
        current = cwd_path.parent
        root_resolved = project_root.resolve()
        while current >= root_resolved:
            venv = current / ".venv"
            if (venv / "bin" / "python").exists():
                return venv
            if current == root_resolved:
                break
            current = current.parent

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
    """Translate ASTRA resource requirements to ``docker``/``podman`` flags."""
    flags: list[str] = []
    if cpus := resources.get("cpus"):
        flags.append(f"--cpus={cpus}")
    if memory := resources.get("memory"):
        flags.append(f"--memory={memory.lower()}")
    if gpus := resources.get("gpus"):
        flags.append(f"--gpus={gpus}")
    return flags


class ASTRAContainerRunner:
    """Execute ASTRA recipes via a container runtime, a venv, or local subprocess.

    The runner picks an execution mode for each call:

    * If a container is supplied (and a runtime is configured), it shells out
      to ``<runtime> run …`` mounting the project root.
    * Otherwise, if a project ``.venv`` exists, the recipe runs there.
    * As a last resort, it runs in the current Python environment.

    Container failures fall back to venv (or local) so missing daemons
    don't block iteration.
    """

    def __init__(
        self,
        project_root: str,
        backend: str = "docker",
        default_container: str | None = None,
        container_runtime: str | None = None,
    ):
        self.project_root = Path(project_root)
        self.backend = backend
        self.default_container = default_container
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
        """Execute a recipe."""
        cli_args = _build_cli_args(params or {}, universe_id)
        full_command = (command + " " + " ".join(cli_args)).strip()
        results_dir = self.project_root / "results" / universe_id
        results_dir.mkdir(parents=True, exist_ok=True)

        effective_cwd = cwd_override or str(self.project_root)

        if self.backend == "local":
            return self._run_local(full_command, output_id, universe_id, cwd=effective_cwd)
        if self.backend == "venv":
            return self._run_venv(full_command, output_id, universe_id, cwd=effective_cwd)

        # Container backend.  Falls back to venv (or local) on failure.
        effective_container = container or self.default_container
        if effective_container:
            result = self._run_container(
                command=full_command, container=effective_container,
                universe_id=universe_id, resources=resources or {},
                runtime=self.container_runtime or "docker",
                external_inputs=external_inputs,
            )
            if result.exit_code == 0:
                return result
            logger.warning(
                "%s execution failed for '%s' (exit %d). Falling back to venv.\n  stderr: %s",
                self.container_runtime or "docker", output_id, result.exit_code,
                result.metadata.get("stderr", "")[:200],
            )

        venv_python = self.project_root / ".venv" / "bin" / "python"
        if venv_python.exists():
            return self._run_venv(
                command=full_command, output_id=output_id, universe_id=universe_id,
                warn=effective_container is not None, cwd=effective_cwd,
            )
        logger.warning(
            "No .venv found for '%s'; executing in the current Python env.", output_id,
        )
        return self._run_local(
            command=full_command, output_id=output_id, universe_id=universe_id,
            warn=effective_container is not None, cwd=effective_cwd,
        )

    def _run_container(
        self,
        command: str,
        container: str,
        universe_id: str,
        resources: dict[str, Any],
        runtime: str = "docker",
        external_inputs: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Execute a recipe in a container.

        Mounts the project root at ``/workspace`` so scripts read data and
        write results using their normal relative paths.  ``podman-hpc``
        gets ``--gpu`` / ``--mpi`` injected based on resource requests.
        """
        cmd: list[str] = [runtime, "run", "--rm"]
        if runtime == "podman-hpc":
            if resources.get("gpus"):
                cmd.append("--gpu")
            if int(resources.get("nodes", 1) or 1) > 1:
                cmd.append("--mpi")
        else:
            cmd.extend(translate_resources_to_docker_flags(resources))
        cmd.extend(["-v", f"{self.project_root}:/workspace", "-w", "/workspace"])
        for input_id, source in sorted((external_inputs or {}).items()):
            cmd.extend(["-v", f"{source}:/workspace/data/{input_id}:ro"])
        cmd.extend([container, "sh", "-c", command])

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
                "stdout": stdout_tail, "stderr": stderr_tail,
                "backend": runtime, "container_command": " ".join(cmd),
            },
        )

    def _run_local(
        self, command: str, output_id: str, universe_id: str,
        warn: bool = False, cwd: str | None = None,
    ) -> ExecutionResult:
        """Execute as a local subprocess in the current Python env."""
        if warn:
            logger.warning(
                "Executing '%s' locally (no container).", output_id,
            )
        full_command = _substitute_python(command, sys.executable)
        returncode, stdout_tail, stderr_tail = _run_streaming(
            full_command, shell=True, cwd=cwd or str(self.project_root),
        )
        return ExecutionResult(
            exit_code=returncode,
            output_path=self.project_root / "results" / universe_id,
            metadata={"stdout": stdout_tail, "stderr": stderr_tail, "backend": "local"},
        )

    def _run_venv(
        self, command: str, output_id: str, universe_id: str,
        warn: bool = False, cwd: str | None = None,
    ) -> ExecutionResult:
        """Execute in the project's ``.venv``, ensuring deps are installed."""
        if warn:
            logger.warning(
                "Executing '%s' in project venv (no container).", output_id,
            )
        venv_path = _find_venv(cwd, self.project_root)
        if venv_path is None:
            return ExecutionResult(
                exit_code=1,
                output_path=self.project_root / "results" / universe_id,
                metadata={"stderr": "No .venv found.", "backend": "venv"},
            )

        venv_python = venv_path / "bin" / "python"
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
        return ExecutionResult(
            exit_code=returncode,
            output_path=self.project_root / "results" / universe_id,
            metadata={
                "stdout": stdout_tail, "stderr": stderr_tail,
                "backend": "venv", "venv_path": str(venv_path),
            },
        )

    def _ensure_venv_deps(self, venv_path: Path) -> None:
        """Install ``requirements*.txt`` into *venv_path* if they have changed."""
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
                capture_output=True, text=True, cwd=str(self.project_root),
            )
            if install_result.returncode != 0:
                logger.warning(
                    "pip install -r %s failed: %s",
                    req_file.name, install_result.stderr[:200],
                )
                all_installed = False
        if all_installed:
            marker.write_text(current_hash + "\n")
        self._venv_deps_checked = True
