"""Substrate-agnostic cluster plumbing.

Anything in here is shared by every cluster type: dataclasses, state-file
CRUD, scheduler-file address resolution, walltime parsing, and the
file-level config CRUD on ``~/.lightcone/clusters/``.

Substrate-specific code lives in sibling modules (``_slurm.py``, future
``_k8s.py``).
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from astra.helpers import load_yaml, save_yaml

from lightcone.engine.site_registry import SITE_DEFAULTS

#: Cluster types known to the dispatcher. Extend when adding a substrate.
ClusterType = Literal["slurm"]


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def get_clusters_dir() -> Path:
    """Return ``~/.lightcone/clusters/`` (created on demand)."""
    p = Path.home() / ".lightcone" / "clusters"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_cache_dir() -> Path:
    """Return ``~/.lightcone/cache/`` (site-keyed cluster discovery cache)."""
    p = Path.home() / ".lightcone" / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_envs_dir() -> Path:
    """Return ``~/.lightcone/envs/`` (auto-provisioned worker venvs)."""
    p = Path.home() / ".lightcone" / "envs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def env_path_for_site(site: str) -> Path:
    return get_envs_dir() / site


# ---------------------------------------------------------------------------
# Config CRUD
# ---------------------------------------------------------------------------


def list_clusters() -> list[str]:
    """Return names of configured clusters (alphabetical)."""
    return sorted(p.stem for p in get_clusters_dir().glob("*.yaml"))


def load_cluster_config(name: str) -> dict[str, Any] | None:
    """Load ``~/.lightcone/clusters/<name>.yaml`` or ``None`` if absent."""
    path = get_clusters_dir() / f"{name}.yaml"
    try:
        return load_yaml(path) or {}
    except FileNotFoundError:
        return None


def save_cluster_config(name: str, config: dict[str, Any]) -> Path:
    """Write a cluster config and return its path."""
    path = get_clusters_dir() / f"{name}.yaml"
    save_yaml(config, path)
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
                f"No cluster named '{cli_cluster}'. "
                f"Configured: {list_clusters() or 'none'}"
            )
        return cli_cluster, config

    project_cfg_path = project_path / ".lightcone" / "lightcone.yaml"
    try:
        project_cfg = load_yaml(project_cfg_path) or {}
    except FileNotFoundError:
        project_cfg = {}
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
# Dataclasses
# ---------------------------------------------------------------------------


#: Substrate-neutral runtime states.  ``NONE`` means no submission record
#: exists; the rest mirror conventional batch-system lifecycles.  SLURM
#: state codes are mapped onto these in ``_slurm._SLURM_STATE_MAP``.
RuntimeState = Literal[
    "NONE", "PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "DEAD",
]


@dataclass
class WorkerPool:
    """One homogeneous pool of Dask workers."""

    nodes: int
    threads_per_node: int = 64
    memory: str = "200GB"
    resources: dict[str, int] = field(default_factory=dict)
    constraint: str | None = None


@dataclass
class ClusterSpec:
    """Static cluster config — what the user wrote in YAML."""

    name: str
    type: ClusterType
    site: str
    account: str
    qos: str
    walltime: str                       # ``"24h"``, ``"30m"``, or ``HH:MM:SS``
    workers: list[WorkerPool]
    container_runtime: str
    scratch_root: str                   # may contain ``$VAR``; expanded in script
    extra_sbatch: list[str] = field(default_factory=list)
    worker_init: str | None = None


@dataclass
class ClusterRecord:
    """Submission record persisted to ``<name>.state.json`` after start."""

    name: str
    type: ClusterType
    job_id: str
    site: str
    submitted_at: str                   # ISO 8601 UTC
    walltime_seconds: int
    scheduler_file: str


@dataclass
class ClusterInfo:
    """Spec + submission record + live runtime state, joined for display."""

    spec: ClusterSpec
    record: ClusterRecord | None
    state: RuntimeState
    scheduler_address: str | None       # ``"tcp://host:port"`` once Dask is up




# ---------------------------------------------------------------------------
# Spec construction from YAML
# ---------------------------------------------------------------------------


def spec_from_config(name: str, config: dict[str, Any]) -> ClusterSpec:
    """Materialize a :class:`ClusterSpec` from a loaded YAML dict.

    Pulls site defaults from ``site_registry`` for fields the user omitted
    (``scratch_root``, ``container_runtime``, ``worker_init_template``).
    Requires the ``type:`` discriminator (``slurm`` today).
    """
    cluster_type = config.get("type")
    if cluster_type is None:
        raise ValueError(
            f"Cluster '{name}': missing required field 'type' "
            f"(set `type: slurm` in the YAML)"
        )
    if cluster_type != "slurm":
        raise ValueError(
            f"Cluster '{name}': unknown type {cluster_type!r}. Supported: 'slurm'."
        )

    site = config.get("site")
    if not site:
        raise ValueError(f"Cluster '{name}': missing required field 'site'")
    site_defaults = SITE_DEFAULTS.get(site, {})
    cluster_defaults = site_defaults.get(cluster_type, {})

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
        type=cluster_type,
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
# State file CRUD
# ---------------------------------------------------------------------------


def state_path(name: str) -> Path:
    return get_clusters_dir() / f"{name}.state.json"


def write_record(record: ClusterRecord) -> Path:
    path = state_path(record.name)
    with open(path, "w") as f:
        json.dump(asdict(record), f, indent=2)
    return path


def read_record(name: str) -> ClusterRecord | None:
    try:
        with open(state_path(name)) as f:
            return ClusterRecord(**json.load(f))
    except FileNotFoundError:
        return None


# ---------------------------------------------------------------------------
# Scheduler-file address resolution (Dask writes tcp://… into a JSON file)
# ---------------------------------------------------------------------------


def expand_path(value: str) -> str:
    """Expand ``$VAR`` and ``~`` for paths read on the orchestrator side."""
    return os.path.expandvars(os.path.expanduser(value))


def read_scheduler_address(scheduler_file: str) -> str | None:
    """Return the live ``tcp://host:port`` from a Dask scheduler-file."""
    try:
        with open(expand_path(scheduler_file)) as f:
            return json.load(f).get("address")
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


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
    """Normalise to ``HH:MM:SS``."""
    sec = parse_walltime_seconds(value)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"
