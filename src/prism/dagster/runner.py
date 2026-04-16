"""ASTRA Container Runner — executes recipes in Docker/Podman, locally, or via SLURM."""
from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Maximum number of characters to keep from stdout/stderr for metadata.
_TAIL_CHARS = 2000


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
    """Translate ASTRA resource requirements to Docker CLI flags."""
    flags: list[str] = []
    if cpus := resources.get("cpus"):
        flags.append(f"--cpus={cpus}")
    if memory := resources.get("memory"):
        flags.append(f"--memory={memory.lower()}")
    if gpus := resources.get("gpus"):
        flags.append(f"--gpus={gpus}")
    return flags


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
        """Initialise an ASTRA container runner.

        Args:
            project_root: Absolute path to the ASTRA project directory.
                All recipe commands are executed with this as their working
                directory unless *cwd_override* is supplied at call time.
            backend: Execution backend to use.  One of ``"docker"``,
                ``"local"``, ``"venv"``, or ``"slurm"``.  The ``"docker"``
                backend automatically falls back to ``"venv"`` (or
                ``"local"``) when the container run fails.
            default_container: Analysis-level container image resolved from
                ``astra.yaml``.  Per-recipe containers override this value.
            target_config: Parsed SLURM target configuration dict (from
                ``~/.prism/targets/<name>.yaml``).  Used only when *backend*
                is ``"slurm"``.
            container_runtime: Local container runtime binary name
                (``"docker"`` or ``"podman"``).  When ``None``, the runner
                uses whatever is available on ``PATH``.
        """
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
        # prism init) continue to work rather than surfacing a confusing error.
        logger.warning(
            "No .venv found for '%s'; executing locally without dep isolation. "
            "Run 'prism init' to create a project venv with dependencies installed.",
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

        Uses the ``.venv/`` created by ``prism init``.  Ensures that
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
                        "Run 'prism init' or create a virtual environment first."
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

        from prism.container import find_dependency_files, hash_file_contents

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

    def _run_slurm_interactive(
        self,
        command: str,
        container: str | None,
        output_id: str,
        universe_id: str,
        resources: dict[str, Any],
        external_inputs: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> ExecutionResult:
        """Execute a recipe via srun inside an existing interactive allocation.

        Runs synchronously — no job submission or polling needed.
        """
        effective_cwd = cwd or str(self.project_root)
        scheduler = self.target_config.get("scheduler", {})
        container_runtime = scheduler.get("container_runtime", "podman-hpc")

        output_path = Path(effective_cwd) / "results" / universe_id
        output_path.mkdir(parents=True, exist_ok=True)

        # Build the execution command
        if container and container_runtime == "podman-hpc":
            exec_command = _podman_hpc_run_command(
                command, container, self.project_root, resources, scheduler,
                external_inputs=external_inputs,
            )
        else:
            # No container — symlink external inputs into data/ directory
            if external_inputs:
                data_dir = Path(effective_cwd) / "data"
                data_dir.mkdir(parents=True, exist_ok=True)
                for input_id, source in sorted(external_inputs.items()):
                    link = data_dir / input_id
                    if link.is_symlink() or link.exists():
                        link.unlink()
                    link.symlink_to(source)
            exec_command = command

        cmd = ["srun", "bash", "-c", exec_command]

        logger.info(
            "Running %s/%s interactively (SLURM_JOB_ID=%s)",
            output_id, universe_id, os.environ.get("SLURM_JOB_ID"),
        )

        try:
            returncode, stdout_tail, stderr_tail = _run_streaming(
                cmd, cwd=effective_cwd,
            )
        except FileNotFoundError:
            return ExecutionResult(
                exit_code=127,
                output_path=output_path,
                metadata={"stderr": "srun: command not found"},
            )

        return ExecutionResult(
            exit_code=returncode,
            output_path=output_path,
            metadata={
                "backend": "slurm-interactive",
                "slurm_job_id": os.environ.get("SLURM_JOB_ID", ""),
                "container_runtime": container_runtime if container else None,
                "stdout": stdout_tail,
                "stderr": stderr_tail,
            },
        )

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
        """Execute a recipe via SLURM.

        When ``SLURM_JOB_ID`` is set (i.e. we are inside an interactive
        ``salloc`` session), runs the command synchronously via ``srun``
        for fast iteration.  Otherwise, generates an sbatch script,
        submits it, and polls for completion.
        """
        # Fast path: interactive allocation detected
        if os.environ.get("SLURM_JOB_ID"):
            return self._run_slurm_interactive(
                command=command,
                container=container,
                output_id=output_id,
                universe_id=universe_id,
                resources=resources,
                external_inputs=external_inputs,
                cwd=cwd,
            )

        scheduler = self.target_config.get("scheduler", {})
        container_runtime = scheduler.get("container_runtime", "podman-hpc")

        output_path = self.project_root / "results" / universe_id
        output_path.mkdir(parents=True, exist_ok=True)

        # Generate the sbatch script
        resource_limits = self.target_config.get("resource_limits", {})
        script = generate_sbatch_script(
            command=command,
            container=container,
            container_runtime=container_runtime,
            project_root=self.project_root,
            output_id=output_id,
            universe_id=universe_id,
            resources=resources,
            scheduler_config=scheduler,
            resource_limits=resource_limits,
            external_inputs=external_inputs,
        )

        # Write script to a temp file inside the project so it's on the
        # shared filesystem visible to compute nodes.
        scripts_dir = self.project_root / "results" / ".slurm"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        job_name = f"{output_id}_{universe_id}"
        script_path = scripts_dir / f"{job_name}.sh"
        script_path.write_text(script)
        script_path.chmod(0o755)

        logger.info("Submitting SLURM job for %s/%s", output_id, universe_id)
        logger.debug("sbatch script:\n%s", script)

        # Submit via sbatch
        try:
            submit_result = subprocess.run(
                ["sbatch", str(script_path)],
                capture_output=True,
                text=True,
                cwd=str(self.project_root),
            )
        except FileNotFoundError:
            return ExecutionResult(
                exit_code=127,
                output_path=output_path,
                metadata={"stderr": "sbatch: command not found"},
            )

        if submit_result.returncode != 0:
            return ExecutionResult(
                exit_code=submit_result.returncode,
                output_path=output_path,
                metadata={
                    "stderr": submit_result.stderr,
                    "backend": "slurm",
                },
            )

        # Parse job ID from "Submitted batch job 12345"
        job_id = _parse_sbatch_job_id(submit_result.stdout)
        if job_id is None:
            return ExecutionResult(
                exit_code=1,
                output_path=output_path,
                metadata={
                    "stderr": f"Could not parse job ID from: {submit_result.stdout}",
                    "backend": "slurm",
                },
            )

        logger.info("SLURM job submitted: %s", job_id)

        # Poll for completion
        poll_config = self.target_config.get("poll", {})
        poll_interval = poll_config.get("interval_seconds", 15)
        poll_timeout = poll_config.get("timeout_seconds", 14400)  # 4h default
        exit_code, job_metadata = _poll_slurm_job(
            job_id, poll_interval=poll_interval, poll_timeout=poll_timeout,
        )

        # Collect stdout/stderr from SLURM output files
        slurm_stdout = ""
        slurm_stderr = ""
        stdout_file = scripts_dir / f"{job_name}.out"
        stderr_file = scripts_dir / f"{job_name}.err"
        if stdout_file.exists():
            slurm_stdout = stdout_file.read_text()[-2000:]
        if stderr_file.exists():
            slurm_stderr = stderr_file.read_text()[-2000:]

        return ExecutionResult(
            exit_code=exit_code,
            output_path=output_path,
            metadata={
                "backend": "slurm",
                "slurm_job_id": job_id,
                "container_runtime": container_runtime,
                "stdout": slurm_stdout,
                "stderr": slurm_stderr,
                "sbatch_script": str(script_path),
                **job_metadata,
            },
        )


# ---------------------------------------------------------------------------
# SLURM helpers
# ---------------------------------------------------------------------------


def translate_resources_to_slurm_directives(
    resources: dict[str, Any],
    scheduler_config: dict[str, Any] | None = None,
    *,
    resource_limits: dict[str, Any] | None = None,
) -> list[str]:
    """Translate ASTRA resource requirements to SLURM #SBATCH directives.

    Returns a list of directive strings (without the ``#SBATCH`` prefix).

    When *resource_limits* is provided (non-``None``) and no explicit
    ``time_limit`` appears in *resources*, a default ``--time`` directive is
    emitted using ``resource_limits["max_walltime_minutes"]`` (falling back
    to 30 minutes).  This ensures SLURM jobs always have a walltime.
    """
    scheduler_config = scheduler_config or {}
    directives: list[str] = []

    # Extra SLURM args from CLI passthrough (e.g. --partition, --qos, --constraint)
    extra_args = scheduler_config.get("extra_slurm_args", [])

    # Helper to check if a flag is already in extra args (CLI overrides target)
    def _in_extra(flag: str) -> bool:
        return any(a.startswith(flag) for a in extra_args)

    # Extract constraint from extra args for account suffix resolution
    constraint = scheduler_config.get("constraint")
    for arg in extra_args:
        if arg.startswith("--constraint"):
            constraint = arg.split("=", 1)[1] if "=" in arg else None

    account = scheduler_config.get("account")
    # Apply site-specific account suffix (e.g. _g for GPU on Perlmutter)
    if account and not _in_extra("--account"):
        site_key = scheduler_config.get("site")
        if site_key and constraint:
            from prism.dagster.site_registry import resolve_account
            account = resolve_account(site_key, account, constraint)
        directives.append(f"--account={account}")
    if not _in_extra("--partition"):
        if partition := scheduler_config.get("partition"):
            directives.append(f"--partition={partition}")
    if not _in_extra("--qos"):
        if qos := scheduler_config.get("qos"):
            directives.append(f"--qos={qos}")
    if not _in_extra("--constraint"):
        if constraint:
            directives.append(f"--constraint={constraint}")

    if nodes := resources.get("nodes"):
        directives.append(f"--nodes={nodes}")
    if cpus := resources.get("cpus"):
        directives.append(f"--cpus-per-task={cpus}")
    if memory := resources.get("memory"):
        directives.append(f"--mem={memory}")
    if not _in_extra("--gpus"):
        if gpus := resources.get("gpus"):
            directives.append(f"--gpus={gpus}")
    if time_limit := resources.get("time_limit"):
        directives.append(f"--time={_normalise_time_limit(time_limit)}")
    elif resource_limits is not None:
        # No explicit time_limit — apply a default so SLURM doesn't reject
        # the job.  Use the target's max_walltime_minutes, or 30 min.
        default_minutes = resource_limits.get("max_walltime_minutes", 30)
        logger.warning(
            "No time_limit in recipe resources; defaulting to %d minutes "
            "(from resource_limits.max_walltime_minutes)",
            default_minutes,
        )
        directives.append(f"--time={_normalise_time_limit(default_minutes)}")

    # Append any extra SLURM flags passed through from the CLI
    directives.extend(extra_args)

    return directives


def _normalise_time_limit(value: str | int) -> str:
    """Convert time_limit values like '2h', '30m', 120 to HH:MM:SS."""
    if isinstance(value, int):
        # Assume minutes
        hours, minutes = divmod(value, 60)
        return f"{hours:02d}:{minutes:02d}:00"
    value = str(value).strip()
    match = re.match(r"^(\d+)([hm]?)$", value, re.IGNORECASE)
    if match:
        num, unit = int(match.group(1)), match.group(2).lower()
        if unit == "h":
            return f"{num:02d}:00:00"
        elif unit == "m":
            hours, minutes = divmod(num, 60)
            return f"{hours:02d}:{minutes:02d}:00"
        else:
            # bare number = minutes
            hours, minutes = divmod(num, 60)
            return f"{hours:02d}:{minutes:02d}:00"
    # Already in HH:MM:SS or similar — pass through
    return value


def generate_sbatch_script(
    command: str,
    container: str | None,
    container_runtime: str,
    project_root: Path,
    output_id: str,
    universe_id: str,
    resources: dict[str, Any],
    scheduler_config: dict[str, Any] | None = None,
    resource_limits: dict[str, Any] | None = None,
    external_inputs: dict[str, str] | None = None,
) -> str:
    """Generate an sbatch script for a recipe execution.

    Uses ``podman-hpc`` as the container runtime on Perlmutter. Containers are
    run via ``podman-hpc run`` with optional ``--gpu`` / ``--mpi`` flags.
    Images must be pre-migrated (``podman-hpc migrate``).

    If no container image is specified, the command runs directly (no
    container wrapping).
    """
    scheduler_config = scheduler_config or {}
    job_name = f"prism_{output_id}_{universe_id}"

    lines = ["#!/bin/bash"]

    # Standard SBATCH header
    lines.append(f"#SBATCH --job-name={job_name}")

    # Output / error files go next to the script
    lines.append(f"#SBATCH --output=results/.slurm/{output_id}_{universe_id}.out")
    lines.append(f"#SBATCH --error=results/.slurm/{output_id}_{universe_id}.err")

    # Resource directives
    directives = translate_resources_to_slurm_directives(
        resources, scheduler_config, resource_limits=resource_limits,
    )

    for d in directives:
        lines.append(f"#SBATCH {d}")

    lines.append("")
    lines.append("# --- Prism / ASTRA recipe execution ---")
    lines.append(f"cd {project_root}")
    lines.append("")

    # Build the execution command based on container runtime
    if container and container_runtime == "podman-hpc":
        lines.append(_podman_hpc_run_command(
            command, container, project_root, resources, scheduler_config,
            external_inputs=external_inputs,
        ))
    else:
        # No container — symlink external inputs into data/ directory
        if external_inputs:
            lines.append("mkdir -p data")
            for input_id, source in sorted(external_inputs.items()):
                lines.append(f"ln -sfn {source} data/{input_id}")
            lines.append("")
        # Run directly
        lines.append(command)

    lines.append("")
    return "\n".join(lines)


def _podman_hpc_run_command(
    command: str,
    container: str,
    project_root: Path,
    resources: dict[str, Any],
    scheduler_config: dict[str, Any],
    external_inputs: dict[str, str] | None = None,
) -> str:
    """Build a podman-hpc run invocation for use inside an sbatch script.

    Key podman-hpc flags used:
    - ``--rm``: Clean up container after exit.
    - ``--gpu``: Bind NVIDIA GPU devices and drivers into the container.
    - ``--mpi``: Inject Cray MPICH for optimized MPI on Slingshot.
    - ``-v``: Volume mount the project root at /workspace.
    - ``-w``: Set the working directory inside the container.
    """
    parts = ["podman-hpc", "run", "--rm"]

    # GPU support
    if resources.get("gpus"):
        parts.append("--gpu")

    # MPI support — if the scheduler config opts in
    container_flags = scheduler_config.get("container_flags", [])
    if "--mpi" in container_flags:
        parts.append("--mpi")
    if "--nccl" in container_flags:
        parts.append("--nccl")
    if "--cuda-mpi" in container_flags:
        parts.append("--cuda-mpi")

    # Any extra user-specified flags
    for flag in container_flags:
        if flag not in ("--mpi", "--nccl", "--cuda-mpi", "--gpu"):
            parts.append(flag)

    # Volume mount project root
    parts.extend(["-v", f"{project_root}:/workspace", "-w", "/workspace"])

    # Read-only volume mounts for external inputs
    for input_id, source in sorted((external_inputs or {}).items()):
        parts.extend(["-v", f"{source}:/workspace/data/{input_id}:ro"])

    parts.append(container)
    parts.extend(["sh", "-c", _shell_quote(command)])

    return " ".join(parts)


def _shell_quote(s: str) -> str:
    """Wrap a string in single quotes for shell, escaping internal quotes."""
    return shlex.quote(s)


def _parse_sbatch_job_id(stdout: str) -> str | None:
    """Extract job ID from sbatch output like 'Submitted batch job 12345'."""
    match = re.search(r"Submitted batch job (\d+)", stdout)
    return match.group(1) if match else None


def _poll_slurm_job(
    job_id: str,
    poll_interval: int = 15,
    poll_timeout: int = 14400,
) -> tuple[int, dict[str, Any]]:
    """Poll a SLURM job until completion, returning (exit_code, metadata).

    Uses ``sacct`` to query the final status.  Falls back to ``squeue`` if
    sacct is not available.
    """
    start = time.monotonic()
    metadata: dict[str, Any] = {}

    while True:
        elapsed = time.monotonic() - start
        if elapsed > poll_timeout:
            logger.warning(
                "SLURM job %s timed out after %ds", job_id, poll_timeout,
            )
            metadata["timeout"] = True
            return 1, metadata

        # Check sacct for completed job
        exit_code, meta = _check_sacct(job_id)
        if exit_code is not None:
            metadata.update(meta)
            return exit_code, metadata

        logger.debug(
            "Job %s still running (%.0fs elapsed), polling in %ds",
            job_id, elapsed, poll_interval,
        )
        time.sleep(poll_interval)


def _check_sacct(job_id: str) -> tuple[int | None, dict[str, Any]]:
    """Query sacct for a completed job. Returns (exit_code, metadata) or (None, {})."""
    try:
        result = subprocess.run(
            [
                "sacct", "-j", job_id,
                "--format=JobID,State,ExitCode,Elapsed,NodeList",
                "--noheader", "--parsable2",
            ],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        # sacct not available, try squeue fallback
        return _check_squeue_fallback(job_id)

    if result.returncode != 0:
        return None, {}

    for line in result.stdout.strip().splitlines():
        parts = line.split("|")
        if len(parts) < 5:
            continue
        sacct_job_id, state, exit_code_str, elapsed, nodelist = parts[:5]
        # Only look at the main job step (not .batch, .extern, etc.)
        if "." in sacct_job_id:
            continue

        state = state.strip()
        if state in ("COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "NODE_FAIL",
                      "OUT_OF_MEMORY", "PREEMPTED"):
            # For non-COMPLETED states always treat as failure — sacct often
            # reports 0:0 for CANCELLED jobs which would be a false success.
            if state != "COMPLETED":
                exit_code = 1
            else:
                try:
                    exit_code = int(exit_code_str.split(":")[0])
                except (ValueError, IndexError):
                    exit_code = 0
            return exit_code, {
                "slurm_state": state,
                "elapsed": elapsed,
                "nodelist": nodelist,
            }

    return None, {}


def _check_squeue_fallback(job_id: str) -> tuple[int | None, dict[str, Any]]:
    """Fallback: use squeue to check if a job is still running."""
    try:
        result = subprocess.run(
            ["squeue", "-j", job_id, "--noheader", "--format=%T"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        logger.error("Neither sacct nor squeue found — cannot poll SLURM job")
        return 1, {"error": "sacct and squeue not found"}

    state = result.stdout.strip()
    if not state:
        # Job no longer in queue — assume completed (sacct would be better)
        return 0, {"slurm_state": "COMPLETED (assumed)"}
    return None, {}
