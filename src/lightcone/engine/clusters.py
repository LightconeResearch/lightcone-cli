"""Cluster configuration, lifecycle, and SLURM-side rendering.

A *cluster* is a long-lived SLURM allocation that hosts a Dask scheduler and
workers.  Users define one in ``~/.lightcone/clusters/<name>.yaml`` and bring
it up with ``lc cluster start <name>``.  Subsequent ``lc run`` invocations
connect to the live Dask cluster via ``dagster_dask`` and dispatch assets
with zero queue wait.

This module owns the entire cluster domain: config CRUD, sbatch rendering,
state files, lifecycle calls (``sbatch``/``squeue``/``scancel``), QoS
preflight, and worker-env auto-bootstrap.  It is intentionally
self-contained so it can be extracted to a standalone
``dagster-slurm-cluster`` package later via ``git mv``.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml

from lightcone.engine.site_registry import SITE_DEFAULTS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def get_clusters_dir() -> Path:
    """Return ``~/.lightcone/clusters/`` (created on demand)."""
    p = Path.home() / ".lightcone" / "clusters"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_cache_dir() -> Path:
    """Return ``~/.lightcone/cache/`` (site-keyed cluster info)."""
    p = Path.home() / ".lightcone" / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_envs_dir() -> Path:
    """Return ``~/.lightcone/envs/`` (auto-provisioned worker venvs)."""
    p = Path.home() / ".lightcone" / "envs"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# Config CRUD
# ---------------------------------------------------------------------------


def list_clusters() -> list[str]:
    """Return names of configured clusters (alphabetical)."""
    return sorted(p.stem for p in get_clusters_dir().glob("*.yaml"))


def load_cluster_config(name: str) -> dict[str, Any] | None:
    """Load ``~/.lightcone/clusters/<name>.yaml`` or ``None`` if absent."""
    path = get_clusters_dir() / f"{name}.yaml"
    if not path.exists():
        return None
    with open(path) as f:
        return yaml.safe_load(f) or {}


def save_cluster_config(name: str, config: dict[str, Any]) -> Path:
    """Write a cluster config and return its path."""
    path = get_clusters_dir() / f"{name}.yaml"
    with open(path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    return path


def resolve_cluster(
    project_path: Path,
    cli_cluster: str | None,
) -> tuple[str, dict[str, Any]] | None:
    """Resolve which cluster to use for a project, or ``None`` for local.

    Resolution order:

    1. Explicit ``--cluster NAME`` CLI flag.
    2. ``cluster:`` field in ``<project>/.lightcone/lightcone.yaml``.
    3. The single configured cluster if exactly one exists in
       ``~/.lightcone/clusters/``.
    4. ``None`` — caller falls back to local execution.
    """
    if cli_cluster:
        config = load_cluster_config(cli_cluster)
        if config is None:
            raise FileNotFoundError(
                f"No cluster named '{cli_cluster}'. Configured: {list_clusters() or 'none'}"
            )
        return cli_cluster, config

    project_cfg_path = project_path / ".lightcone" / "lightcone.yaml"
    if project_cfg_path.exists():
        with open(project_cfg_path) as f:
            project_cfg = yaml.safe_load(f) or {}
        name = project_cfg.get("cluster")
        if name:
            config = load_cluster_config(name)
            if config is None:
                raise FileNotFoundError(
                    f"Project requests cluster '{name}' but it is not configured. "
                    f"Run: lc cluster add {name}"
                )
            return name, config

    clusters = list_clusters()
    if len(clusters) == 1:
        return clusters[0], load_cluster_config(clusters[0]) or {}

    return None


# ---------------------------------------------------------------------------
# Cluster cache (site-keyed)
# ---------------------------------------------------------------------------


def cache_path_for_site(site: str) -> Path:
    return get_cache_dir() / f"{site}.cluster.yaml"


def load_cluster_cache(site: str) -> Any:
    """Load cached ``ClusterInfo`` for *site* or ``None``."""
    from lightcone.engine.slurm_info import cluster_info_from_dict

    path = cache_path_for_site(site)
    if not path.exists():
        return None
    with open(path) as f:
        data = yaml.safe_load(f)
    return cluster_info_from_dict(data) if data else None


def save_cluster_cache(site: str, info: Any) -> Path:
    from lightcone.engine.slurm_info import cluster_info_to_dict

    path = cache_path_for_site(site)
    with open(path, "w") as f:
        f.write("# AUTO-GENERATED by `lc cluster refresh-cache`. Do not edit.\n")
        yaml.dump(cluster_info_to_dict(info), f, default_flow_style=False, sort_keys=False)
    return path


def is_cache_stale(site: str, max_age_days: int = 30) -> bool:
    path = cache_path_for_site(site)
    if not path.exists():
        return True
    with open(path) as f:
        data = yaml.safe_load(f)
    if not data or not data.get("timestamp"):
        return True
    try:
        ts = datetime.fromisoformat(data["timestamp"])
        return (datetime.now(UTC) - ts).days > max_age_days
    except (ValueError, TypeError):
        return True


def refresh_cluster_cache(site: str) -> Any:
    """Re-query the local SLURM scheduler and rewrite the cache for *site*."""
    from lightcone.engine.slurm_info import discover_cluster

    info = discover_cluster()
    save_cluster_cache(site, info)
    return info


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class WorkerPool:
    """One homogeneous pool of Dask workers in a cluster."""

    nodes: int
    threads_per_node: int = 64
    memory: str = "200GB"
    resources: dict[str, int] = field(default_factory=dict)
    constraint: str | None = None


@dataclass
class ClusterSpec:
    """Static config for a cluster — what the user wrote in YAML."""

    name: str
    site: str
    account: str
    qos: str
    walltime: str                       # ``"24h"``, ``"30m"``, or ``HH:MM:SS``
    workers: list[WorkerPool]
    container_runtime: str
    scratch_root: str                   # may contain ``$VAR``; expanded in script
    extra_sbatch: list[str] = field(default_factory=list)
    worker_init: str | None = None      # only set if user overrode the site default


@dataclass
class ClusterState:
    """Live state recorded after ``lc cluster start`` (``<name>.state.json``)."""

    name: str
    job_id: str
    site: str
    submitted_at: str                   # ISO 8601 UTC
    walltime_seconds: int
    scheduler_file: str


@dataclass
class ClusterInfo:
    """Spec + state + live SLURM/Dask state, joined for display."""

    spec: ClusterSpec
    state: ClusterState | None
    slurm_state: Literal[
        "PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "DEAD", "NONE",
    ]
    scheduler_address: str | None       # ``"tcp://host:port"`` once Dask is up


# ---------------------------------------------------------------------------
# Spec construction from YAML
# ---------------------------------------------------------------------------


def spec_from_config(name: str, config: dict[str, Any]) -> ClusterSpec:
    """Materialize a :class:`ClusterSpec` from a loaded YAML dict.

    Pulls site defaults from ``site_registry`` for fields the user omitted
    (``scratch_root``, ``container_runtime``, ``worker_init``).
    """
    site = config.get("site")
    if not site:
        raise ValueError(f"Cluster '{name}': missing required field 'site'")
    site_defaults = SITE_DEFAULTS.get(site, {})
    cluster_defaults = site_defaults.get("cluster", {})

    raw_workers = config.get("workers") or []
    if not raw_workers:
        raise ValueError(f"Cluster '{name}': must declare at least one worker pool")

    workers = [
        WorkerPool(
            nodes=int(w["nodes"]),
            threads_per_node=int(w.get("threads_per_node", 64)),
            memory=str(w.get("memory", "200GB")),
            resources={k: int(v) for k, v in (w.get("resources") or {}).items()},
            constraint=w.get("constraint"),
        )
        for w in raw_workers
    ]

    return ClusterSpec(
        name=name,
        site=site,
        account=config.get("account") or "",
        qos=config.get("qos") or cluster_defaults.get("default_qos", "debug"),
        walltime=str(config.get("walltime") or cluster_defaults.get("default_walltime", "30m")),
        workers=workers,
        container_runtime=(
            config.get("container_runtime")
            or site_defaults.get("container_runtime")
            or "podman-hpc"
        ),
        scratch_root=str(
            config.get("scratch_root") or cluster_defaults.get("scratch_root", "$HOME/scratch")
        ),
        extra_sbatch=list(config.get("extra_sbatch") or []),
        worker_init=config.get("worker_init"),
    )


# ---------------------------------------------------------------------------
# Walltime helpers
# ---------------------------------------------------------------------------


def parse_walltime_seconds(value: str | int) -> int:
    """Convert ``"24h"``/``"30m"``/``"01:30:00"``/``int`` minutes to seconds."""
    if isinstance(value, int):
        return value * 60
    s = str(value).strip()
    if s.endswith("h"):
        return int(s[:-1]) * 3600
    if s.endswith("m"):
        return int(s[:-1]) * 60
    parts = s.split(":")
    if len(parts) == 3:
        h, m, sec = (int(p) for p in parts)
        return h * 3600 + m * 60 + sec
    raise ValueError(f"unparseable walltime: {value!r}")


def walltime_to_slurm(value: str | int) -> str:
    """Normalise to ``HH:MM:SS`` for SBATCH directives."""
    sec = parse_walltime_seconds(value)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


# ---------------------------------------------------------------------------
# Sbatch rendering
# ---------------------------------------------------------------------------


def _resolve_worker_init(spec: ClusterSpec) -> str:
    """Return the bash snippet that activates the worker env on compute nodes."""
    if spec.worker_init:
        return spec.worker_init.rstrip() + "\n"
    site_defaults = SITE_DEFAULTS.get(spec.site, {})
    template = (site_defaults.get("cluster") or {}).get("worker_init_template")
    if template:
        return template.rstrip() + "\n"
    # Last resort: assume the env is at the conventional path.
    return f"source $HOME/.lightcone/envs/{spec.site}/bin/activate\n"


def _resources_to_dask_arg(resources: dict[str, int]) -> str:
    """Translate ``{"GPU": 4}`` → ``"GPU=4"`` for ``dask worker --resources``."""
    return ",".join(f"{k}={v}" for k, v in sorted(resources.items()))


def _total_nodes(spec: ClusterSpec) -> int:
    return sum(w.nodes for w in spec.workers)


def render_cluster_sbatch(spec: ClusterSpec) -> str:
    """Render the sbatch script that brings up the persistent Dask cluster.

    The head node runs ``dask scheduler``; one ``srun`` step per worker pool
    launches ``dask worker``s with their pool-specific ``--resources`` tags
    (and optional per-pool ``--constraint``).  The script holds the
    allocation until the scheduler exits or walltime expires.
    """
    if not spec.account:
        raise ValueError(f"Cluster '{spec.name}': missing 'account'")

    total_nodes = _total_nodes(spec)
    walltime = walltime_to_slurm(spec.walltime)
    scratch = spec.scratch_root
    sched_file = f"{scratch}/lightcone/clusters/{spec.name}.json"
    local_dir = f"{scratch}/lightcone/clusters/scratch"

    lines: list[str] = [
        "#!/bin/bash",
        f"#SBATCH --job-name=lc-cluster-{spec.name}",
        f"#SBATCH --nodes={total_nodes}",
        f"#SBATCH --time={walltime}",
        f"#SBATCH --qos={spec.qos}",
        f"#SBATCH --account={spec.account}",
        f"#SBATCH --output=results/.slurm/lc-cluster-{spec.name}-%j.out",
    ]
    # Promote a pool-uniform constraint to a top-level SBATCH directive
    # (most common case: every pool wants ``cpu`` or every pool wants ``gpu``).
    pool_constraints = [w.constraint for w in spec.workers]
    uniform = (
        all(c is not None for c in pool_constraints)
        and len(set(pool_constraints)) == 1
    )
    if uniform:
        lines.append(f"#SBATCH --constraint={pool_constraints[0]}")
        per_pool_constraint = False
    else:
        per_pool_constraint = any(c for c in pool_constraints)

    lines.extend(f"#SBATCH {arg}" for arg in spec.extra_sbatch)
    lines += [
        "",
        "set -euo pipefail",
        "",
        _resolve_worker_init(spec).rstrip(),
        "",
        f'SCHED_FILE="{sched_file}"',
        'mkdir -p "$(dirname "$SCHED_FILE")"',
        "",
        "dask scheduler \\",
        '    --scheduler-file "$SCHED_FILE" \\',
        "    --port 8786 \\",
        "    --dashboard-address :8787 &",
        "SCHED_PID=$!",
        "",
    ]

    for i, pool in enumerate(spec.workers):
        srun_parts = [
            "srun",
            f"--nodes={pool.nodes}",
            f"--ntasks={pool.nodes}",
            "--ntasks-per-node=1",
        ]
        if per_pool_constraint and pool.constraint:
            srun_parts.append(f"--constraint={pool.constraint}")
        srun_parts.append("dask worker")
        srun_parts.append('--scheduler-file "$SCHED_FILE"')
        srun_parts.append(f"--nworkers {pool.threads_per_node}")
        srun_parts.append(f'--memory-limit "{pool.memory}"')
        srun_parts.append(f'--local-directory "{local_dir}"')
        if pool.resources:
            srun_parts.append(f'--resources "{_resources_to_dask_arg(pool.resources)}"')
        rendered = " \\\n     ".join(srun_parts)
        lines.append(f"# pool {i}: {pool.nodes} node(s)" + (
            f", resources={pool.resources}" if pool.resources else ""
        ))
        lines.append(rendered + " &")
        lines.append("")

    lines.append('wait "$SCHED_PID"')
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# QoS preflight (port of runner._validate_and_adjust_qos)
# ---------------------------------------------------------------------------


def validate_against_qos(spec: ClusterSpec, strategy: str = "fit") -> ClusterSpec:
    """Check the cluster against the cluster cache; adjust or raise.

    ``"fit"``: clamp ``walltime`` and total ``nodes`` to fit the configured
    ``qos`` if exceeded.

    ``"switch"``: pick another QoS from the site-registry-declared choices
    that fits, holding hardware constraint fixed.

    Returns a (possibly adjusted) :class:`ClusterSpec`.  No-ops if the cache
    is missing or the QoS isn't found in it.
    """
    from lightcone.engine.slurm_info import (
        check_qos_eligibility,
        recommend_qos,
    )

    cluster = load_cluster_cache(spec.site)
    if cluster is None:
        if is_cache_stale(spec.site):
            logger.warning(
                "No cluster cache for site '%s' — skipping QoS preflight. "
                "Run `lc cluster refresh-cache %s` to enable it.",
                spec.site, spec.site,
            )
        return spec

    site_defaults = SITE_DEFAULTS.get(spec.site, {})
    overrides: dict[str, str] = site_defaults.get("cache_key_overrides", {}) or {}
    constraints = {w.constraint for w in spec.workers if w.constraint}
    constraint = next(iter(constraints)) if len(constraints) == 1 else None

    cache_key = _resolve_cache_key(spec.qos, constraint, cluster.qos, overrides)
    qos_info = cluster.qos.get(cache_key)
    if qos_info is None:
        return spec

    nodes_total = _total_nodes(spec)
    gpus_per_node = max((sum(w.resources.values()) for w in spec.workers), default=0)
    walltime_minutes = parse_walltime_seconds(spec.walltime) // 60

    request = {
        "nodes": nodes_total,
        "gpus_per_node": gpus_per_node,
        "time_limit_minutes": walltime_minutes,
    }
    current = check_qos_eligibility(qos_info, request)
    if current.eligible:
        return spec

    if strategy == "fit" and current.clamped_resources:
        clamped = current.clamped_resources
        adjusted = spec
        # Walltime clamp is the safe one — apply unconditionally.
        if "time_limit_minutes" in clamped:
            new_minutes = clamped["time_limit_minutes"]
            logger.warning(
                "Reducing cluster '%s' walltime to %d min to fit qos '%s'.",
                spec.name, new_minutes, spec.qos,
            )
            adjusted = _replace(adjusted, walltime=f"{new_minutes}m")
        if "nodes" in clamped:
            new_nodes = clamped["nodes"]
            logger.warning(
                "Cluster '%s' requests %d nodes; qos '%s' allows %d. "
                "Reduce the worker pool sizes manually if you need fewer nodes.",
                spec.name, nodes_total, spec.qos, new_nodes,
            )
            # We can't safely re-distribute nodes across pools; surface and stop.
        verify = check_qos_eligibility(qos_info, {
            "nodes": _total_nodes(adjusted),
            "gpus_per_node": gpus_per_node,
            "time_limit_minutes": parse_walltime_seconds(adjusted.walltime) // 60,
        })
        if verify.eligible:
            return adjusted

    qos_choices = list((site_defaults.get("suggested_options") or {})
                       .get("qos", {}).get("choices", {}).keys()) or [spec.qos]
    recs = recommend_qos(
        cluster, request,
        qos_choices=qos_choices, constraint=constraint,
        preferred_qos=spec.qos, cache_key_overrides=overrides,
    )
    best = next((r for r in recs if r.eligible), None)
    if best:
        logger.warning(
            "qos '%s' cannot host cluster '%s' (%s). Switching to '%s'.",
            spec.qos, spec.name, "; ".join(current.violations), best.qos,
        )
        return _replace(spec, qos=best.qos)

    raise ValueError(
        f"Cluster '{spec.name}' violates qos '{spec.qos}' "
        f"({'; '.join(current.violations)}) and no eligible alternative "
        f"was found. Adjust the cluster config and retry."
    )


def _resolve_cache_key(
    qos: str,
    constraint: str | None,
    cache_qos_keys: Any,
    overrides: dict[str, str],
) -> str:
    """Map a ``(qos, constraint)`` to a sacctmgr cache record name."""
    if constraint:
        key = f"{qos}/{constraint}"
        if key in overrides:
            return overrides[key]
    if qos in overrides:
        return overrides[qos]
    if constraint:
        prefixed = f"{constraint}_{qos}"
        if prefixed in cache_qos_keys:
            return prefixed
    return qos


def _replace(spec: ClusterSpec, **changes: Any) -> ClusterSpec:
    """Return a copy of *spec* with *changes* applied (dataclasses.replace shim)."""
    from dataclasses import replace
    return replace(spec, **changes)


# ---------------------------------------------------------------------------
# Worker env auto-bootstrap
# ---------------------------------------------------------------------------


def env_path_for_site(site: str) -> Path:
    return get_envs_dir() / site


def ensure_worker_env(spec: ClusterSpec) -> Path:
    """Create ``~/.lightcone/envs/<site>/`` with the worker dependencies.

    Idempotent: if the venv's Python exists, returns immediately.
    """
    env = env_path_for_site(spec.site)
    if (env / "bin" / "python").exists():
        return env

    logger.info("Provisioning worker env at %s …", env)
    subprocess.run(["uv", "venv", str(env)], check=True)
    subprocess.run(
        [
            "uv", "pip", "install",
            "--python", str(env / "bin" / "python"),
            "lightcone-cli", "dagster-dask", "distributed",
        ],
        check=True,
    )
    logger.info("Worker env ready: %s", env)
    return env


# ---------------------------------------------------------------------------
# Lifecycle: state file, sbatch, squeue, scancel
# ---------------------------------------------------------------------------


def _state_path(name: str) -> Path:
    return get_clusters_dir() / f"{name}.state.json"


def _write_state(state: ClusterState) -> Path:
    path = _state_path(state.name)
    with open(path, "w") as f:
        json.dump(asdict(state), f, indent=2)
    return path


def _read_state(name: str) -> ClusterState | None:
    path = _state_path(name)
    if not path.exists():
        return None
    with open(path) as f:
        data = json.load(f)
    return ClusterState(**data)


def _expand_path(value: str) -> str:
    """Expand ``$VAR`` and ``~`` for paths read on the orchestrator side."""
    return os.path.expandvars(os.path.expanduser(value))


def _read_scheduler_address(scheduler_file: str) -> str | None:
    """Return the live ``tcp://host:port`` from a Dask scheduler-file."""
    path = Path(_expand_path(scheduler_file))
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    return data.get("address")


_SLURM_STATE_MAP = {
    "PD": "PENDING", "PENDING": "PENDING",
    "R": "RUNNING", "RUNNING": "RUNNING",
    "CG": "RUNNING", "COMPLETING": "RUNNING",
    "CD": "COMPLETED", "COMPLETED": "COMPLETED",
    "F": "FAILED", "FAILED": "FAILED",
    "TO": "FAILED", "TIMEOUT": "FAILED", "NF": "FAILED", "NODE_FAIL": "FAILED",
    "CA": "CANCELLED", "CANCELLED": "CANCELLED",
}


def _query_slurm_state(job_id: str) -> str:
    """Return one of PENDING/RUNNING/COMPLETED/FAILED/CANCELLED/DEAD."""
    try:
        result = subprocess.run(
            ["squeue", "-h", "-j", job_id, "-o", "%T"],
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        return "DEAD"
    out = result.stdout.strip()
    if out:
        return _SLURM_STATE_MAP.get(out.split()[0], "RUNNING")
    # Not in squeue — try sacct for terminal state.
    try:
        result = subprocess.run(
            ["sacct", "-j", job_id, "-o", "State", "-n", "-X", "-P"],
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        return "DEAD"
    out = result.stdout.strip().splitlines()
    if not out:
        return "DEAD"
    raw = out[0].split()[0].rstrip("+")
    return _SLURM_STATE_MAP.get(raw, "DEAD")


def cluster_info(name: str) -> ClusterInfo | None:
    """Combine on-disk config + state + live SLURM state into one view.

    Returns ``None`` if the cluster has no config.  When the cluster has a
    config but no state, ``slurm_state == "NONE"``.
    """
    config = load_cluster_config(name)
    if config is None:
        return None
    spec = spec_from_config(name, config)
    state = _read_state(name)
    if state is None:
        return ClusterInfo(spec=spec, state=None, slurm_state="NONE", scheduler_address=None)
    slurm_state = _query_slurm_state(state.job_id)
    address = _read_scheduler_address(state.scheduler_file) if slurm_state == "RUNNING" else None
    return ClusterInfo(spec=spec, state=state, slurm_state=slurm_state, scheduler_address=address)


def find_running_cluster(name: str) -> ClusterInfo | None:
    """Return the cluster if it has a state file and is RUNNING with a reachable scheduler."""
    info = cluster_info(name)
    if info is None or info.state is None:
        return None
    if info.slurm_state != "RUNNING" or not info.scheduler_address:
        return None
    return info


def start_cluster(
    name: str,
    project_root: Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
    strategy: str = "fit",
) -> ClusterInfo:
    """Submit the cluster via ``sbatch`` and record state.

    Provisions the worker env first if missing.  Performs QoS preflight.
    Writes ``~/.lightcone/clusters/<name>.state.json``.  Does not block on
    the scheduler coming up — call :func:`wait_for_scheduler` for that.
    """
    config = load_cluster_config(name)
    if config is None:
        raise FileNotFoundError(
            f"No cluster named '{name}'. Configured: {list_clusters() or 'none'}"
        )
    if cli_overrides:
        for k, v in cli_overrides.items():
            if v is not None:
                config[k] = v

    if _read_state(name) is not None:
        existing = cluster_info(name)
        if existing and existing.slurm_state in {"PENDING", "RUNNING"}:
            raise RuntimeError(
                f"Cluster '{name}' already has an active job ({existing.state.job_id}, "
                f"state={existing.slurm_state}). Run `lc cluster stop {name}` first."
            )
        # Stale state — clean up.
        _state_path(name).unlink()

    spec = spec_from_config(name, config)
    spec = validate_against_qos(spec, strategy=strategy)
    ensure_worker_env(spec)

    script = render_cluster_sbatch(spec)
    project_root = project_root or Path.cwd()
    scripts_dir = project_root / "results" / ".slurm"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    script_path = scripts_dir / f"lc-cluster-{name}.sbatch"
    script_path.write_text(script)
    script_path.chmod(0o755)

    submit = subprocess.run(
        ["sbatch", str(script_path)],
        capture_output=True, text=True, cwd=str(project_root),
    )
    if submit.returncode != 0:
        raise RuntimeError(f"sbatch failed: {submit.stderr.strip() or submit.stdout.strip()}")

    job_id = _parse_job_id(submit.stdout)
    if job_id is None:
        raise RuntimeError(f"could not parse job id from sbatch output: {submit.stdout!r}")

    state = ClusterState(
        name=name,
        job_id=job_id,
        site=spec.site,
        submitted_at=datetime.now(UTC).isoformat(),
        walltime_seconds=parse_walltime_seconds(spec.walltime),
        scheduler_file=f"{spec.scratch_root}/lightcone/clusters/{name}.json",
    )
    _write_state(state)
    logger.info("Cluster '%s' submitted as job %s", name, job_id)
    return ClusterInfo(spec=spec, state=state, slurm_state="PENDING", scheduler_address=None)


def stop_cluster(name: str) -> None:
    """``scancel`` the job and remove the state file (and stale scheduler-file)."""
    state = _read_state(name)
    if state is None:
        logger.info("No active state for cluster '%s' — nothing to stop.", name)
        return
    subprocess.run(["scancel", state.job_id], check=False)
    sched_file = Path(_expand_path(state.scheduler_file))
    if sched_file.exists():
        sched_file.unlink()
    _state_path(name).unlink()
    logger.info("Cluster '%s' stopped (cancelled job %s)", name, state.job_id)


def wait_for_scheduler(name: str, timeout_s: int = 600) -> ClusterInfo:
    """Block until the cluster is RUNNING and the scheduler-file is readable."""
    import time
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        info = cluster_info(name)
        if info is None or info.state is None:
            raise RuntimeError(f"No state for cluster '{name}'")
        if info.slurm_state in {"FAILED", "CANCELLED", "COMPLETED", "DEAD"}:
            raise RuntimeError(f"Cluster '{name}' ended in state {info.slurm_state}")
        if info.slurm_state == "RUNNING" and info.scheduler_address:
            return info
        time.sleep(5)
    raise TimeoutError(f"Cluster '{name}' scheduler not ready after {timeout_s}s")


def tail_cluster_logs(name: str, project_root: Path | None = None,
                    follow: bool = False, lines: int = 200) -> None:
    """Stream the SLURM output file for a cluster to stdout."""
    state = _read_state(name)
    if state is None:
        raise RuntimeError(f"No active state for cluster '{name}'")
    project_root = project_root or Path.cwd()
    log_path = project_root / "results" / ".slurm" / f"lc-cluster-{name}-{state.job_id}.out"
    if not log_path.exists():
        raise FileNotFoundError(f"Log file not found: {log_path}")
    cmd = ["tail", f"-n{lines}"]
    if follow:
        cmd.append("-f")
    cmd.append(str(log_path))
    subprocess.run(cmd, check=False)


def _parse_job_id(sbatch_stdout: str) -> str | None:
    """Extract the job id from ``Submitted batch job 12345``."""
    for line in sbatch_stdout.splitlines():
        parts = line.split()
        if parts and parts[-1].isdigit():
            return parts[-1]
    return None
