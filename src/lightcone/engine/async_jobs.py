"""Submit, track, and cancel coarse-grained Lightcone SLURM jobs.

The batch layer deliberately does not execute recipes itself.  It resolves the
requested ASTRA sub-DAG, allocates one SLURM job from declared resources, and
re-enters the ordinary synchronous ``lc run`` command inside that allocation.
That keeps container wrapping, Dask dispatch, manifests, and validation identical
between synchronous and asynchronous runs.
"""
from __future__ import annotations

import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from astra.helpers import load_yaml, resolve_analysis_tree

from lightcone.engine.manifest import fingerprint_external
from lightcone.engine.resources import RecipeResources, ResourceValueError, parse_recipe_resources
from lightcone.engine.scratch import project_hash, resolve_scratch_root
from lightcone.engine.site_registry import HostSite, detect_current_site
from lightcone.engine.status import get_output_status
from lightcone.engine.tree import (
    TreeOutput,
    collect_tree_outputs,
    find_upstream_output,
    resolve_external_input,
)


class AsyncJobError(RuntimeError):
    """An asynchronous job cannot be safely submitted or managed."""


@dataclass(frozen=True)
class JobResources:
    """Aggregate allocation requirements for a resolved sub-DAG."""

    cpus: int
    memory_mb: int
    gpus: int
    time_limit_seconds: int
    rule_count: int


@dataclass(frozen=True)
class SlurmSelection:
    """Site-policy result used to render an sbatch script."""

    site: str
    profile: str
    constraint: str
    qos: str
    nodes: int = 1
    allocation_cpus: int | None = None
    allocation_memory_mb: int | None = None
    allocation_gpus: int | None = None


@dataclass(frozen=True)
class SlurmSettings:
    """User-controlled SLURM submission settings."""

    account: str
    time_padding: float


@dataclass(frozen=True)
class JobRecord:
    """Small local cache record for one submitted job."""

    job_id: str
    targets: list[str]
    resolved_targets: list[str]
    universe: str
    qos: str
    resources: dict[str, Any]
    sbatch_path: str
    log_path: str
    submitted_at: str
    last_state: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobRecord:
        targets = [str(value) for value in data.get("targets", [])]
        resolved = [str(value) for value in data.get("resolved_targets", targets)]
        return cls(
            job_id=str(data["job_id"]),
            targets=targets,
            resolved_targets=resolved,
            universe=str(data["universe"]),
            qos=str(data["qos"]),
            resources=dict(data.get("resources") or {}),
            sbatch_path=str(data["sbatch_path"]),
            log_path=str(data["log_path"]),
            submitted_at=str(data["submitted_at"]),
            last_state=str(data.get("last_state") or "UNKNOWN"),
        )


JobDisplayState = Literal[
    "queued", "running", "completed", "failed", "cancelled", "unknown"
]


def _qualified_output_id(tree_output: TreeOutput) -> str:
    if tree_output.analysis_id:
        return f"{tree_output.analysis_id}.{tree_output.output_id}"
    return tree_output.output_id


def _resolve_requested_output(
    output_id: str,
    materializable: list[TreeOutput],
) -> TreeOutput:
    matches = [
        tree_output
        for tree_output in materializable
        if _qualified_output_id(tree_output) == output_id
        or tree_output.output_id == output_id
    ]
    if not matches:
        raise AsyncJobError(
            f"Output {output_id!r} not found in astra.yaml or has no recipe."
        )
    if len(matches) > 1:
        choices = ", ".join(_qualified_output_id(match) for match in matches)
        raise AsyncJobError(
            f"Output {output_id!r} is ambiguous; qualify it as one of: {choices}"
        )
    return matches[0]


def resolve_subdag_outputs(
    project_path: Path,
    output_ids: tuple[str, ...],
) -> tuple[list[str], list[TreeOutput]]:
    """Resolve requested outputs and all recipe-producing dependencies."""
    project_path = project_path.resolve()
    spec = resolve_analysis_tree(load_yaml(project_path / "astra.yaml"), project_path)
    all_outputs = collect_tree_outputs(spec)
    materializable = [
        tree_output
        for tree_output in all_outputs
        if tree_output.output_def.get("recipe") is not None
    ]
    selected = (
        [_resolve_requested_output(output_id, materializable) for output_id in output_ids]
        if output_ids
        else materializable
    )

    required: set[str] = set()
    visiting: set[str] = set()

    def visit(tree_output: TreeOutput) -> None:
        key = _qualified_output_id(tree_output)
        if key in required:
            return
        if key in visiting:
            raise AsyncJobError(f"Cycle detected while resolving the sub-DAG at {key!r}.")
        visiting.add(key)
        for input_id in tree_output.output_def.get("inputs") or []:
            upstream = find_upstream_output(tree_output, input_id, all_outputs)
            if upstream is not None:
                visit(upstream)
        visiting.remove(key)
        required.add(key)

    for tree_output in selected:
        visit(tree_output)

    requested = [_qualified_output_id(tree_output) for tree_output in selected]
    resolved = [
        tree_output
        for tree_output in materializable
        if _qualified_output_id(tree_output) in required
    ]
    return requested, resolved


def pending_subdag_outputs(
    project_path: Path,
    tree_outputs: list[TreeOutput],
    *,
    universe: str,
    forced_outputs: set[str] | None = None,
) -> list[TreeOutput]:
    """Return recipes that may run for one universe in dependency order.

    A recipe is pending when its own manifest is missing or stale, when a
    declared input has changed, or when one of its upstream recipes is
    pending.  This mirrors the provenance chain used by the synchronous run
    path while avoiding resource requests for already-materialized upstream
    work.
    """
    project_path = project_path.resolve()
    spec = resolve_analysis_tree(load_yaml(project_path / "astra.yaml"), project_path)
    all_outputs = collect_tree_outputs(spec)
    statuses = {
        (
            f"{status.analysis_id}.{status.output_id}" if status.analysis_id else status.output_id
        ): status
        for status in get_output_status(project_path, universe_id=universe)
    }
    forced = forced_outputs or set()
    pending: dict[str, bool] = {}
    visiting: set[str] = set()

    def needs_run(tree_output: TreeOutput) -> bool:
        key = _qualified_output_id(tree_output)
        if key in pending:
            return pending[key]
        if key in visiting:
            raise AsyncJobError(f"Cycle detected while checking the sub-DAG at {key!r}.")
        visiting.add(key)

        status = statuses.get(key)
        required = key in forced or status is None or status.status != "ok"
        manifest = status.manifest if status is not None else None
        recorded_inputs = (manifest or {}).get("input_versions") or {}

        for input_id in tree_output.output_def.get("inputs") or []:
            upstream = find_upstream_output(tree_output, input_id, all_outputs)
            if upstream is not None:
                upstream_key = _qualified_output_id(upstream)
                if needs_run(upstream):
                    required = True
                    continue
                upstream_status = statuses.get(upstream_key)
                upstream_manifest = (
                    upstream_status.manifest if upstream_status is not None else None
                )
                current_version = (upstream_manifest or {}).get("data_version")
            else:
                source = resolve_external_input(tree_output, input_id, spec)
                if source is None:
                    required = True
                    continue
                source_path = Path(source)
                if not source_path.is_absolute():
                    source_path = project_path / source_path
                current_version = fingerprint_external(source_path)

            if not current_version or recorded_inputs.get(input_id) != current_version:
                required = True

        visiting.remove(key)
        pending[key] = required
        return required

    for tree_output in tree_outputs:
        needs_run(tree_output)
    return [
        tree_output for tree_output in tree_outputs if pending[_qualified_output_id(tree_output)]
    ]


def aggregate_job_resources(
    tree_outputs: list[TreeOutput],
    *,
    time_padding: float,
) -> JobResources:
    """Aggregate a sub-DAG to one node shape and conservative walltime."""
    if not tree_outputs:
        raise AsyncJobError("The selected sub-DAG contains no materializable recipes.")
    if not math.isfinite(time_padding) or time_padding <= 0:
        raise AsyncJobError("slurm.time_padding must be a finite number greater than zero.")

    parsed: list[RecipeResources] = []
    missing: list[str] = []
    for tree_output in tree_outputs:
        label = _qualified_output_id(tree_output)
        recipe = tree_output.output_def.get("recipe") or {}
        resources = recipe.get("resources") or {}
        if not resources.get("time_limit"):
            missing.append(label)
            continue
        try:
            parsed.append(
                parse_recipe_resources(
                    recipe,
                    require_time_limit=True,
                    label=f"output {label!r}",
                )
            )
        except ResourceValueError as exc:
            raise AsyncJobError(str(exc)) from exc

    if missing:
        names = "\n".join(f"  - {name}" for name in missing)
        raise AsyncJobError(
            "Async submission requires resources.time_limit on every recipe "
            "in the resolved sub-DAG. Missing:\n"
            f"{names}\n"
            "Run the Lightcone resource-estimation skill, update astra.yaml, "
            "then retry `lc run --async`."
        )

    return JobResources(
        cpus=max(resource.cpus for resource in parsed),
        memory_mb=max(resource.memory_mb for resource in parsed),
        gpus=max(resource.gpus for resource in parsed),
        time_limit_seconds=math.ceil(
            sum(resource.time_limit_seconds or 0 for resource in parsed) * time_padding
        ),
        rule_count=len(parsed),
    )


def select_slurm_policy(
    resources: JobResources,
    *,
    site: HostSite | None = None,
) -> SlurmSelection:
    """Map aggregate resources to the site's deterministic QoS policy."""
    current_site = site or detect_current_site()
    policy = current_site.get("async_slurm")
    if not current_site or not isinstance(policy, dict):
        raise AsyncJobError(
            "Automatic async submission is currently supported only on a "
            "configured SLURM site (v1: NERSC Perlmutter)."
        )

    profile_name = "gpu" if resources.gpus else "cpu"
    profiles = policy.get("profiles") or {}
    profile = profiles.get(profile_name)
    qos_policy = policy.get("qos") or {}
    if not isinstance(profile, dict):
        raise AsyncJobError(f"Site {current_site.key!r} has no {profile_name!r} profile.")

    limits = {
        "CPUs": (resources.cpus, int(profile["node_cpus"])),
        "memory MB": (resources.memory_mb, int(profile["node_memory_mb"])),
        "GPUs": (resources.gpus, int(profile["node_gpus"])),
    }
    exceeded = [
        f"{name} {value} > {maximum}"
        for name, (value, maximum) in limits.items()
        if value > maximum
    ]
    if exceeded:
        raise AsyncJobError(
            "A single recipe exceeds one Perlmutter node ("
            + ", ".join(exceeded)
            + "). Multi-node recipe shapes are outside async v1; restructure the work."
        )

    shared_max = int(qos_policy["shared"]["max_time_seconds"])
    if profile_name == "gpu":
        shared_gpus = int(profile["shared_gpus"])
        shared_cpus = resources.gpus * int(profile["shared_cpus_per_gpu"])
        shared_memory = resources.gpus * int(profile["shared_memory_mb_per_gpu"])
        fits_shared_shape = (
            1 <= resources.gpus <= shared_gpus
            and resources.cpus <= shared_cpus
            and resources.memory_mb <= shared_memory
        )
    else:
        fits_shared_shape = (
            resources.cpus <= int(profile["shared_cpus"])
            and resources.memory_mb <= int(profile["shared_memory_mb"])
        )

    if fits_shared_shape and resources.time_limit_seconds <= shared_max:
        qos = "shared"
    else:
        regular_max = int(qos_policy["regular"]["max_time_seconds"])
        if resources.time_limit_seconds > regular_max:
            raise AsyncJobError(
                "Aggregated padded walltime "
                f"({format_slurm_time(resources.time_limit_seconds)}) exceeds the "
                f"regular QoS cap ({format_slurm_time(regular_max)}). "
                "Restructure or split the work before submitting."
            )
        qos = "regular"

    allocation_cpus = None
    allocation_memory_mb = None
    allocation_gpus = None
    if qos == "shared":
        if profile_name == "gpu":
            allocation_cpus = shared_cpus
            allocation_memory_mb = shared_memory
            allocation_gpus = resources.gpus
        else:
            allocation_cpus = resources.cpus
            allocation_memory_mb = resources.memory_mb or None

    return SlurmSelection(
        site=str(current_site.key),
        profile=profile_name,
        constraint=str(profile["constraint"]),
        qos=qos,
        allocation_cpus=allocation_cpus,
        allocation_memory_mb=allocation_memory_mb,
        allocation_gpus=allocation_gpus,
    )


_ACCOUNT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def load_slurm_settings(*, account_override: str | None = None) -> SlurmSettings:
    """Load account and padding from ``~/.lightcone/config.yaml``."""
    config_path = Path.home() / ".lightcone" / "config.yaml"
    try:
        data = yaml.safe_load(config_path.read_text()) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise AsyncJobError(f"Could not read {config_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise AsyncJobError(f"{config_path} must contain a YAML mapping.")
    slurm = data.get("slurm") or {}
    if not isinstance(slurm, dict):
        raise AsyncJobError(f"{config_path} slurm entry must be a mapping.")

    account_value = account_override or slurm.get("account")
    account = str(account_value).strip() if account_value else ""
    if not account:
        raise AsyncJobError(
            "No SLURM account configured. Set `slurm.account` in "
            "~/.lightcone/config.yaml or pass `lc run --async --account <account>`."
        )
    if _ACCOUNT_RE.fullmatch(account) is None:
        raise AsyncJobError(f"Invalid SLURM account {account!r}.")

    raw_padding = slurm.get("time_padding", 1.5)
    if isinstance(raw_padding, bool):
        raise AsyncJobError("slurm.time_padding must be a number greater than zero.")
    try:
        padding = float(raw_padding)
    except (TypeError, ValueError) as exc:
        raise AsyncJobError("slurm.time_padding must be a number greater than zero.") from exc
    if not math.isfinite(padding) or padding <= 0:
        raise AsyncJobError("slurm.time_padding must be a finite number greater than zero.")
    return SlurmSettings(account=account, time_padding=padding)


def format_slurm_time(seconds: int) -> str:
    """Format seconds as a SLURM-compatible ``HH:MM:SS`` duration."""
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _slug(value: str, *, maximum: int = 48) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-.") or "all"
    return slug[:maximum]


def jobs_dir(project_path: Path) -> Path:
    """Return the project-local async job-record directory."""
    return project_path / ".lightcone" / "jobs"


def _environment_commands() -> tuple[list[str], str]:
    """Return environment setup lines and the same-environment ``lc`` path."""
    executable = Path(sys.executable).absolute()
    bin_dir = executable.parent
    activate = bin_dir / "activate"
    lines: list[str] = []
    if activate.is_file():
        lines.append(f"source {shlex.quote(str(activate))}")
    lines.append(f"export PATH={shlex.quote(str(bin_dir))}:\"$PATH\"")
    sibling_lc = bin_dir / "lc"
    lc_executable = sibling_lc if sibling_lc.is_file() else Path(shutil.which("lc") or "lc")
    return lines, str(lc_executable)


_SLURM_SUBMISSION_ENV_PASSTHROUGH = frozenset({"SLURM_CLUSTERS", "SLURM_CONF", "SLURM_CONF_SERVER"})


def _submission_environment(source: dict[str, str] | None = None) -> dict[str, str]:
    """Drop the parent allocation's job-scoped variables before ``sbatch``.

    Submitting from a compute node otherwise exports values such as
    ``SLURM_CPUS_PER_TASK`` into the child job.  They can conflict with the
    allocation that ``sbatch`` creates (notably ``SLURM_TRES_PER_TASK``) and
    make the child job's ``srun`` fail before any worker starts.
    """
    environment = dict(os.environ if source is None else source)
    return {
        key: value
        for key, value in environment.items()
        if not key.startswith("SLURM_") or key in _SLURM_SUBMISSION_ENV_PASSTHROUGH
    }


def render_sbatch_script(
    *,
    project_path: Path,
    account: str,
    resources: JobResources,
    selection: SlurmSelection,
    log_template: Path,
    job_name: str,
    lc_args: list[str],
) -> str:
    """Render one sbatch script which re-enters the synchronous run path."""
    setup_lines, lc_executable = _environment_commands()
    command = shlex.join([lc_executable, *lc_args])
    lines = [
        "#!/bin/bash",
        f"#SBATCH --job-name={_slug(job_name, maximum=64)}",
        f"#SBATCH --account={account}",
        f"#SBATCH --qos={selection.qos}",
        f"#SBATCH --constraint={selection.constraint}",
        f"#SBATCH --nodes={selection.nodes}",
        f"#SBATCH --time={format_slurm_time(resources.time_limit_seconds)}",
        f"#SBATCH --output={log_template}",
        f"#SBATCH --error={log_template}",
    ]
    if selection.allocation_gpus:
        lines.append(f"#SBATCH --gpus={selection.allocation_gpus}")
    if selection.allocation_cpus:
        lines.append(f"#SBATCH --cpus-per-task={selection.allocation_cpus}")
    if selection.allocation_memory_mb:
        lines.append(f"#SBATCH --mem={selection.allocation_memory_mb}M")
    lines.extend(
        [
            "",
            "set -euo pipefail",
            f"cd {shlex.quote(str(project_path))}",
            # Never inherit an interactive/external scheduler into the batch
            # job.  The ordinary run path must create its scheduler inside
            # this new allocation.
            "unset DASK_SCHEDULER_ADDRESS",
            *setup_lines,
            f"exec {command}",
            "",
        ]
    )
    return "\n".join(lines)


def _write_record(project_path: Path, record: JobRecord) -> None:
    directory = jobs_dir(project_path)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{record.job_id}.json"
    temporary = directory / f".{record.job_id}.{os.getpid()}.tmp"
    temporary.write_text(json.dumps(asdict(record), indent=2, sort_keys=True) + "\n")
    os.replace(temporary, destination)


def submit_job(
    project_path: Path,
    *,
    output_ids: tuple[str, ...],
    universe: str,
    account_override: str | None = None,
    jobs: int | None = None,
    rerun_triggers: str = "code,input,mtime,params",
    force: bool = False,
    verbose: bool = False,
) -> JobRecord:
    """Resolve, render, submit, and record one universe-scoped async job."""
    project_path = project_path.resolve()
    if not output_ids:
        raise AsyncJobError(
            "Async submission requires at least one explicit output. "
            "Use `lc run --async <output>` for an expensive materialized boundary."
        )
    settings = load_slurm_settings(account_override=account_override)
    requested, subdag = resolve_subdag_outputs(project_path, output_ids)
    pending_subdag = pending_subdag_outputs(
        project_path,
        subdag,
        universe=universe,
        forced_outputs=set(requested) if force else None,
    )
    if not pending_subdag:
        names = ", ".join(requested)
        raise AsyncJobError(
            f"Selected output(s) {names} are already up to date for universe "
            f"{universe!r}; there is nothing to submit."
        )
    resources = aggregate_job_resources(pending_subdag, time_padding=settings.time_padding)
    selection = select_slurm_policy(resources)

    directory = jobs_dir(project_path)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target_slug = _slug("-".join(requested) if output_ids else "all")
    script_path = directory / f"{stamp}-{target_slug}.sbatch"

    log_directory = (
        resolve_scratch_root(project_path)
        / ".lightcone"
        / "jobs"
        / project_hash(project_path)
    )
    log_directory.mkdir(parents=True, exist_ok=True)
    log_template = log_directory / f"{stamp}-{target_slug}-%j.log"

    lc_args = ["run", *requested, "--universe", universe]
    if jobs is not None:
        lc_args.extend(["--jobs", str(jobs)])
    if rerun_triggers != "code,input,mtime,params":
        lc_args.extend(["--rerun-triggers", rerun_triggers])
    if force:
        lc_args.append("--force")
    if verbose:
        lc_args.append("--verbose")

    script = render_sbatch_script(
        project_path=project_path,
        account=settings.account,
        resources=resources,
        selection=selection,
        log_template=log_template,
        job_name=f"lc-{target_slug}-{universe}",
        lc_args=lc_args,
    )
    script_path.write_text(script)
    script_path.chmod(0o750)

    try:
        result = subprocess.run(
            ["sbatch", "--parsable", str(script_path)],
            capture_output=True,
            text=True,
            check=False,
            env=_submission_environment(),
        )
    except FileNotFoundError as exc:
        raise AsyncJobError("`sbatch` was not found on PATH.") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise AsyncJobError(f"sbatch failed: {detail}\nRendered script: {script_path}")
    match = re.match(r"\s*(\d+)", result.stdout)
    if match is None:
        raise AsyncJobError(
            f"Could not parse job id from sbatch output {result.stdout.strip()!r}."
        )
    job_id = match.group(1)

    record = JobRecord(
        job_id=job_id,
        targets=requested,
        resolved_targets=[
            _qualified_output_id(tree_output) for tree_output in pending_subdag
        ],
        universe=universe,
        qos=selection.qos,
        resources={
            **asdict(resources),
            "constraint": selection.constraint,
            "profile": selection.profile,
        },
        sbatch_path=str(script_path),
        log_path=str(log_template).replace("%j", job_id),
        submitted_at=datetime.now(UTC).isoformat(),
        last_state="PENDING",
    )
    _write_record(project_path, record)
    return record


def load_job_records(project_path: Path) -> list[JobRecord]:
    """Load valid project job records, oldest first."""
    directory = jobs_dir(project_path)
    if not directory.is_dir():
        return []
    records: list[JobRecord] = []
    for path in directory.glob("*.json"):
        try:
            data = json.loads(path.read_text())
            if isinstance(data, dict):
                records.append(JobRecord.from_dict(data))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(records, key=lambda record: record.submitted_at)


def job_display_state(raw_state: str) -> JobDisplayState:
    """Collapse SLURM's detailed states to Lightcone's status vocabulary."""
    state = raw_state.strip().upper().split(maxsplit=1)[0].rstrip("+")
    if state in {"PENDING", "CONFIGURING", "REQUEUED", "REQUEUE_FED", "SUBMITTED"}:
        return "queued"
    if state in {"RUNNING", "COMPLETING", "STAGE_OUT", "RESIZING", "SUSPENDED"}:
        return "running"
    if state == "COMPLETED":
        return "completed"
    if state.startswith("CANCELLED"):
        return "cancelled"
    if state in {
        "BOOT_FAIL",
        "DEADLINE",
        "FAILED",
        "NODE_FAIL",
        "OUT_OF_MEMORY",
        "PREEMPTED",
        "REVOKED",
        "SPECIAL_EXIT",
        "TIMEOUT",
    }:
        return "failed"
    return "unknown"


def _parse_state_lines(output: str, requested: set[str]) -> dict[str, str]:
    states: dict[str, str] = {}
    for line in output.splitlines():
        pieces = [piece.strip() for piece in line.split("|", 1)]
        if len(pieces) != 2:
            continue
        job_id, state = pieces
        if job_id in requested and state:
            states[job_id] = state
    return states


def _scheduler_query(command: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return None


def query_job_states(job_ids: list[str]) -> tuple[dict[str, str], bool]:
    """Batch-query active jobs with squeue, then missing jobs with sacct.

    Returns ``(states, scheduler_available)``.  An offline archive keeps
    cached states instead of converting every record to ``unknown``.
    """
    if not job_ids:
        return {}, True
    requested = set(job_ids)
    states: dict[str, str] = {}
    scheduler_available = False

    squeue = _scheduler_query(
        ["squeue", "--noheader", "--jobs", ",".join(job_ids), "--format=%i|%T"]
    )
    if squeue is not None and squeue.returncode == 0:
        scheduler_available = True
        states.update(_parse_state_lines(squeue.stdout, requested))

    missing = requested - states.keys()
    if missing:
        sacct = _scheduler_query(
            [
                "sacct",
                "--noheader",
                "--parsable2",
                "--allocations",
                "--jobs",
                ",".join(sorted(missing)),
                "--format=JobIDRaw,State",
            ]
        )
        if sacct is not None and sacct.returncode == 0:
            scheduler_available = True
            states.update(_parse_state_lines(sacct.stdout, missing))
    return states, scheduler_available


def refresh_job_records(project_path: Path) -> list[JobRecord]:
    """Refresh non-terminal records from SLURM and persist changed states."""
    records = load_job_records(project_path)
    active = [
        record
        for record in records
        if job_display_state(record.last_state) not in {"completed", "failed", "cancelled"}
    ]
    states, scheduler_available = query_job_states([record.job_id for record in active])
    if not scheduler_available:
        return records

    refreshed: list[JobRecord] = []
    for record in records:
        if record not in active:
            refreshed.append(record)
            continue
        raw_state = states.get(record.job_id, "UNKNOWN")
        updated = replace(record, last_state=raw_state)
        if updated != record:
            _write_record(project_path, updated)
        refreshed.append(updated)
    return refreshed


def latest_job_for_output(
    records: list[JobRecord],
    *,
    output_id: str,
    universe: str,
) -> JobRecord | None:
    """Return the newest job whose resolved sub-DAG contains an output."""
    matches = [
        record
        for record in records
        if record.universe == universe
        and output_id in (record.resolved_targets or record.targets)
    ]
    return max(matches, key=lambda record: record.submitted_at, default=None)


def _resolve_cancel_record(records: list[JobRecord], reference: str) -> JobRecord:
    if reference.isdigit():
        matches = [record for record in records if record.job_id == reference]
    else:
        matches = [
            record
            for record in records
            if reference in (record.resolved_targets or record.targets)
            or any(
                target.rsplit(".", 1)[-1] == reference
                for target in (record.resolved_targets or record.targets)
            )
        ]
    active = [
        record
        for record in matches
        if job_display_state(record.last_state) not in {"completed", "failed", "cancelled"}
    ]
    if not active:
        raise AsyncJobError(f"No active recorded job matches {reference!r}.")
    if len(active) > 1:
        job_ids = ", ".join(record.job_id for record in active)
        raise AsyncJobError(
            f"{reference!r} matches multiple active jobs ({job_ids}); cancel by job id."
        )
    return active[0]


def cancel_job(project_path: Path, reference: str) -> JobRecord:
    """Cancel one recorded job by job id or unambiguous output id."""
    records = refresh_job_records(project_path)
    record = _resolve_cancel_record(records, reference)
    try:
        result = subprocess.run(
            ["scancel", record.job_id], capture_output=True, text=True, check=False
        )
    except FileNotFoundError as exc:
        raise AsyncJobError("`scancel` was not found on PATH.") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise AsyncJobError(f"scancel failed for job {record.job_id}: {detail}")
    cancelled = replace(record, last_state="CANCELLED")
    _write_record(project_path, cancelled)
    return cancelled


__all__ = [
    "AsyncJobError",
    "JobRecord",
    "JobResources",
    "SlurmSelection",
    "aggregate_job_resources",
    "cancel_job",
    "format_slurm_time",
    "job_display_state",
    "latest_job_for_output",
    "load_job_records",
    "load_slurm_settings",
    "pending_subdag_outputs",
    "query_job_states",
    "refresh_job_records",
    "render_sbatch_script",
    "resolve_subdag_outputs",
    "select_slurm_policy",
    "submit_job",
]
