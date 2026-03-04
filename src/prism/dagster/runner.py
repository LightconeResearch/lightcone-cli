"""ASTRA Container Runner — executes recipes in Docker, locally, or via SLURM."""
from __future__ import annotations

import logging
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


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
    ):
        self.project_root = Path(project_root)
        self.backend = backend
        self.default_container = default_container
        self.target_config = target_config or {}

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
    ) -> ExecutionResult:
        """Execute a recipe, dispatching to the configured backend.

        For the "docker" backend, falls back to local execution when Docker
        is unavailable or the container run fails.
        """
        cli_args = _build_cli_args(params or {}, universe_id)
        full_command = (command + " " + " ".join(cli_args)).strip()
        results_dir = self.project_root / "results" / universe_id
        results_dir.mkdir(parents=True, exist_ok=True)

        if self.backend == "local":
            return self._run_local(full_command, output_id, universe_id)

        if self.backend == "slurm":
            return self._run_slurm(
                command=full_command,
                container=container or self.default_container,
                input_ids=inputs or [],
                output_id=output_id,
                universe_id=universe_id,
                resources=resources or {},
                external_inputs=external_inputs,
            )

        # Docker backend — try Docker first, fall back to local
        effective_container = container or self.default_container
        if effective_container:
            result = self._run_docker(
                command=full_command,
                container=effective_container,
                universe_id=universe_id,
                resources=resources or {},
            )
            if result.exit_code == 0:
                return result
            # Docker failed — fall back
            logger.warning(
                "Docker execution failed for '%s' (exit code %d). "
                "Falling back to local execution.\n  stderr: %s",
                output_id, result.exit_code,
                result.metadata.get("stderr", "")[:200],
            )

        return self._run_local(
            command=full_command,
            output_id=output_id,
            universe_id=universe_id,
            warn=effective_container is not None,
        )

    def _run_docker(
        self,
        command: str,
        container: str,
        universe_id: str,
        resources: dict[str, Any],
    ) -> ExecutionResult:
        """Execute a recipe in a Docker container.

        Mounts the project root at /workspace so scripts can read data and
        write results using their normal relative paths.
        """
        cmd = ["docker", "run", "--rm"]
        cmd.extend(translate_resources_to_docker_flags(resources))
        cmd.extend([
            "-v", f"{self.project_root}:/workspace",
            "-w", "/workspace",
            container,
            "sh", "-c", command,
        ])

        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
        except FileNotFoundError:
            # Docker binary not found
            return ExecutionResult(
                exit_code=127,
                output_path=self.project_root / "results" / universe_id,
                metadata={"stderr": "docker: command not found"},
            )

        return ExecutionResult(
            exit_code=result.returncode,
            output_path=self.project_root / "results" / universe_id,
            metadata={
                "stdout": result.stdout[-2000:] if result.stdout else "",
                "stderr": result.stderr[-2000:] if result.stderr else "",
                "backend": "docker",
                "docker_command": " ".join(cmd),
            },
        )

    def _run_local(
        self,
        command: str,
        output_id: str,
        universe_id: str,
        warn: bool = False,
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

        # Use the same Python that is running prism, unless the command
        # explicitly names an interpreter.
        full_command = command
        env_python = sys.executable
        if full_command.startswith("python "):
            full_command = env_python + full_command[len("python"):]

        result = subprocess.run(
            full_command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=str(self.project_root),
        )

        output_path = self.project_root / "results" / universe_id
        return ExecutionResult(
            exit_code=result.returncode,
            output_path=output_path,
            metadata={
                "stdout": result.stdout[-2000:] if result.stdout else "",
                "stderr": result.stderr[-2000:] if result.stderr else "",
                "backend": "local",
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
    ) -> ExecutionResult:
        """Execute a recipe via SLURM on a login node.

        Generates an sbatch script with the appropriate container runtime
        (podman-hpc or shifter), submits it, and polls for completion.
        Assumes we are running on a login node with access to sbatch/squeue/sacct
        and the project directory is on a shared filesystem.
        """
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

    if account := scheduler_config.get("account"):
        directives.append(f"--account={account}")
    if partition := scheduler_config.get("partition"):
        directives.append(f"--partition={partition}")
    if qos := scheduler_config.get("qos"):
        directives.append(f"--qos={qos}")
    if constraint := scheduler_config.get("constraint"):
        directives.append(f"--constraint={constraint}")

    if nodes := resources.get("nodes"):
        directives.append(f"--nodes={nodes}")
    if cpus := resources.get("cpus"):
        directives.append(f"--cpus-per-task={cpus}")
    if memory := resources.get("memory"):
        directives.append(f"--mem={memory}")
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
