"""SLURM cluster discovery and QoS advisor.

Queries ``sacctmgr`` and ``scontrol`` to discover available QoS, their
resource limits, and the user's associations.  Results are cached in
``~/.prism/cache/`` and used by the runner to validate and auto-switch
QoS before job submission.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class QoSInfo:
    """Parsed QoS limits from SLURM."""

    name: str
    max_wall_minutes: int | None = None
    max_nodes: int | None = None
    max_gpus_total: int | None = None
    max_jobs_per_user: int | None = None
    max_submit_per_user: int | None = None
    priority: int = 0


@dataclass
class PartitionInfo:
    """Parsed partition limits from SLURM."""

    name: str
    max_time_minutes: int | None = None
    max_nodes: int | None = None
    total_nodes: int = 0
    has_gpus: bool = False
    gpu_model: str | None = None


@dataclass
class ClusterInfo:
    """Snapshot of everything discovered from the scheduler."""

    qos: dict[str, QoSInfo] = field(default_factory=dict)
    user_qos: list[str] = field(default_factory=list)
    user_accounts: list[str] = field(default_factory=list)
    partitions: dict[str, PartitionInfo] = field(default_factory=dict)
    timestamp: str = ""


@dataclass
class QoSRecommendation:
    """Result of checking resources against a specific QoS."""

    qos: str
    eligible: bool
    violations: list[str] = field(default_factory=list)
    clamped_resources: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse_max_tres(tres_str: str) -> dict[str, int]:
    """Parse a SLURM TRES string into a dict.

    >>> parse_max_tres("gres/gpu=2,node=1")
    {'gres/gpu': 2, 'node': 1}
    >>> parse_max_tres("")
    {}
    """
    if not tres_str or not tres_str.strip():
        return {}
    result: dict[str, int] = {}
    for part in tres_str.split(","):
        part = part.strip()
        if "=" not in part:
            continue
        key, val = part.rsplit("=", 1)
        try:
            result[key] = int(val)
        except ValueError:
            continue
    return result


def parse_slurm_walltime(wall_str: str) -> int | None:
    """Parse a SLURM walltime string to minutes.

    Supports formats: ``D-HH:MM:SS``, ``HH:MM:SS``, ``MM:SS``, ``MM``.
    Returns ``None`` for empty or unparseable strings.

    >>> parse_slurm_walltime("2-00:00:00")
    2880
    >>> parse_slurm_walltime("00:30:00")
    30
    """
    if not wall_str or not wall_str.strip():
        return None

    wall_str = wall_str.strip()
    days = 0
    if "-" in wall_str:
        day_part, wall_str = wall_str.split("-", 1)
        try:
            days = int(day_part)
        except ValueError:
            return None

    parts = wall_str.split(":")
    try:
        if len(parts) == 3:
            h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
            return days * 24 * 60 + h * 60 + m + (1 if s > 0 else 0)
        elif len(parts) == 2:
            m, s = int(parts[0]), int(parts[1])
            return days * 24 * 60 + m + (1 if s > 0 else 0)
        elif len(parts) == 1:
            return days * 24 * 60 + int(parts[0])
    except ValueError:
        return None
    return None


def infer_qos_constraint(qos_name: str) -> str | None:
    """Infer the hardware constraint from a QoS name.

    Heuristic: names starting with ``gpu_`` target GPU nodes.
    Names starting with ``shared`` without ``gpu`` may be CPU shared.

    >>> infer_qos_constraint("gpu_debug")
    'gpu'
    >>> infer_qos_constraint("debug")
    'cpu'
    >>> infer_qos_constraint("gpu_shared")
    'gpu'
    """
    name = qos_name.lower()
    if name.startswith("gpu_"):
        return "gpu"
    if name.startswith("gpu"):
        return "gpu"
    return "cpu"


# ---------------------------------------------------------------------------
# SLURM query functions
# ---------------------------------------------------------------------------

_SACCTMGR_QOS_FIELDS = (
    "Name,Priority,MaxTRES,MaxWall,MaxJobsPU,MaxSubmitPU"
)


def _run_command(cmd: list[str]) -> str | None:
    """Run a command, return stdout or None on failure."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            logger.debug("Command %s failed: %s", cmd, result.stderr.strip())
            return None
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.debug("Command %s error: %s", cmd, exc)
        return None


def query_qos() -> dict[str, QoSInfo]:
    """Query all QoS definitions via ``sacctmgr show qos -P``.

    Returns an empty dict if ``sacctmgr`` is unavailable.
    """
    if not shutil.which("sacctmgr"):
        logger.debug("sacctmgr not found, skipping QoS discovery")
        return {}

    output = _run_command(["sacctmgr", "show", "qos", "-P"])
    if not output:
        return {}

    lines = output.strip().split("\n")
    if not lines:
        return {}

    header = lines[0].split("|")
    col = {name: idx for idx, name in enumerate(header)}

    result: dict[str, QoSInfo] = {}
    for line in lines[1:]:
        fields = line.split("|")
        if len(fields) < len(header):
            continue

        name = fields[col.get("Name", 0)]
        if not name:
            continue

        tres = parse_max_tres(fields[col["MaxTRES"]]) if "MaxTRES" in col else {}

        priority = 0
        if "Priority" in col:
            try:
                priority = int(fields[col["Priority"]])
            except ValueError:
                pass

        max_wall = None
        if "MaxWall" in col:
            max_wall = parse_slurm_walltime(fields[col["MaxWall"]])

        max_jobs = None
        if "MaxJobsPU" in col and fields[col["MaxJobsPU"]].strip():
            try:
                max_jobs = int(fields[col["MaxJobsPU"]])
            except ValueError:
                pass

        max_submit = None
        if "MaxSubmitPU" in col and fields[col["MaxSubmitPU"]].strip():
            try:
                max_submit = int(fields[col["MaxSubmitPU"]])
            except ValueError:
                pass

        result[name] = QoSInfo(
            name=name,
            max_wall_minutes=max_wall,
            max_nodes=tres.get("node"),
            max_gpus_total=tres.get("gres/gpu"),
            max_jobs_per_user=max_jobs,
            max_submit_per_user=max_submit,
            priority=priority,
        )

    return result


def query_user_associations(
    user: str | None = None,
) -> tuple[list[str], list[str]]:
    """Query user's accounts and allowed QoS via ``sacctmgr``.

    Returns ``(accounts, qos_names)``.  Both lists are empty if the
    command is unavailable.
    """
    if not shutil.which("sacctmgr"):
        return [], []

    if user is None:
        user = os.environ.get("USER", "")

    output = _run_command([
        "sacctmgr", "show", "association",
        "where", f"user={user}",
        "format=Account,QOS", "-P",
    ])
    if not output:
        return [], []

    accounts: list[str] = []
    all_qos: set[str] = set()

    lines = output.strip().split("\n")
    for line in lines[1:]:
        parts = line.split("|")
        if len(parts) < 2:
            continue
        account = parts[0].strip()
        if account and account not in accounts:
            accounts.append(account)
        qos_str = parts[1].strip()
        if qos_str:
            for q in qos_str.split(","):
                q = q.strip()
                if q:
                    all_qos.add(q)

    return accounts, sorted(all_qos)


def query_partitions() -> dict[str, PartitionInfo]:
    """Query partition info via ``scontrol show partition``.

    Returns an empty dict if ``scontrol`` is unavailable.
    """
    if not shutil.which("scontrol"):
        return {}

    output = _run_command(["scontrol", "show", "partition"])
    if not output:
        return {}

    result: dict[str, PartitionInfo] = {}
    # scontrol outputs blocks separated by blank lines
    for block in output.split("\n\n"):
        if not block.strip():
            continue
        info: dict[str, str] = {}
        for token in re.split(r"\s+", block):
            if "=" in token:
                key, _, val = token.partition("=")
                info[key] = val

        name = info.get("PartitionName", "")
        if not name:
            continue

        max_time = None
        if info.get("MaxTime") and info["MaxTime"] != "UNLIMITED":
            max_time = parse_slurm_walltime(info["MaxTime"])

        max_nodes = None
        if info.get("MaxNodes") and info["MaxNodes"] != "UNLIMITED":
            try:
                max_nodes = int(info["MaxNodes"])
            except ValueError:
                pass

        total_nodes = 0
        if info.get("TotalNodes"):
            try:
                total_nodes = int(info["TotalNodes"])
            except ValueError:
                pass

        tres_str = info.get("TRES", "")
        has_gpus = "gres/gpu" in tres_str
        gpu_model = None
        gpu_match = re.search(r"gres/gpu:(\w+)", tres_str)
        if gpu_match:
            gpu_model = gpu_match.group(1)

        result[name] = PartitionInfo(
            name=name,
            max_time_minutes=max_time,
            max_nodes=max_nodes,
            total_nodes=total_nodes,
            has_gpus=has_gpus,
            gpu_model=gpu_model,
        )

    return result


def discover_cluster() -> ClusterInfo:
    """Run all SLURM queries and return a ``ClusterInfo`` snapshot."""
    qos = query_qos()
    accounts, user_qos = query_user_associations()
    partitions = query_partitions()
    return ClusterInfo(
        qos=qos,
        user_qos=user_qos,
        user_accounts=accounts,
        partitions=partitions,
        timestamp=datetime.now(UTC).isoformat(),
    )


# ---------------------------------------------------------------------------
# QoS advisor
# ---------------------------------------------------------------------------

def check_qos_eligibility(
    qos_info: QoSInfo,
    resources: dict[str, Any],
) -> QoSRecommendation:
    """Check whether *resources* fit within *qos_info* limits.

    *resources* keys: ``nodes``, ``gpus_per_node``, ``time_limit_minutes``.
    """
    violations: list[str] = []
    clamped: dict[str, Any] = {}

    nodes = resources.get("nodes", 1)
    gpus_per_node = resources.get("gpus_per_node", 0)
    total_gpus = nodes * gpus_per_node
    time_min = resources.get("time_limit_minutes")

    if qos_info.max_nodes is not None and nodes > qos_info.max_nodes:
        violations.append(
            f"needs {nodes} nodes, max is {qos_info.max_nodes}"
        )
        clamped["nodes"] = qos_info.max_nodes

    if qos_info.max_gpus_total is not None and total_gpus > qos_info.max_gpus_total:
        violations.append(
            f"needs {total_gpus} GPUs total, max is {qos_info.max_gpus_total}"
        )
        clamped["gpus_total"] = qos_info.max_gpus_total

    if (
        qos_info.max_wall_minutes is not None
        and time_min is not None
        and time_min > qos_info.max_wall_minutes
    ):
        violations.append(
            f"needs {time_min} min, max is {qos_info.max_wall_minutes} min"
        )
        clamped["time_limit_minutes"] = qos_info.max_wall_minutes

    return QoSRecommendation(
        qos=qos_info.name,
        eligible=len(violations) == 0,
        violations=violations,
        clamped_resources=clamped if clamped else None,
    )


def recommend_qos(
    cluster: ClusterInfo,
    resources: dict[str, Any],
    preferred_qos: str | None = None,
    constraint: str | None = None,
    qos_entries: list[dict[str, Any]] | None = None,
) -> list[QoSRecommendation]:
    """Return QoS recommendations sorted by preference and eligibility.

    *qos_entries* is the target's ``qos`` list (dicts with ``name``,
    ``constraint``, ``use_for``).  If provided, only QoS in this list
    are considered.  GPU recipes (``resources["gpus_per_node"] > 0``)
    filter to entries with ``constraint`` containing ``"gpu"``.

    Sorting:
      1. *preferred_qos* first (if eligible)
      2. Eligible options by priority (descending)
      3. Ineligible options last
    """
    # Determine which QoS names to consider
    needs_gpu = resources.get("gpus_per_node", 0) > 0
    candidates: list[str] = []

    if qos_entries is not None:
        for entry in qos_entries:
            name = entry.get("name", "")
            entry_constraint = entry.get("constraint", "")
            if needs_gpu and "gpu" not in entry_constraint:
                continue
            if not needs_gpu and "gpu" in entry_constraint:
                continue
            if name in cluster.qos:
                candidates.append(name)
    else:
        candidates = list(cluster.qos.keys())

    # Check each candidate
    recommendations: list[QoSRecommendation] = []
    for name in candidates:
        qos_info = cluster.qos[name]
        rec = check_qos_eligibility(qos_info, resources)
        recommendations.append(rec)

    # Sort: preferred first (if eligible), then eligible by priority desc,
    # then ineligible
    def sort_key(rec: QoSRecommendation) -> tuple[int, int, int]:
        is_preferred = 1 if rec.qos == preferred_qos else 0
        return (
            -int(rec.eligible),          # eligible first
            -is_preferred,               # preferred within eligible
            -cluster.qos[rec.qos].priority,  # higher priority first
        )

    recommendations.sort(key=sort_key)
    return recommendations


# ---------------------------------------------------------------------------
# Serialization (for cache files)
# ---------------------------------------------------------------------------

def cluster_info_to_dict(info: ClusterInfo) -> dict[str, Any]:
    """Serialize a ``ClusterInfo`` for YAML storage."""
    qos_dict = {}
    for name, q in info.qos.items():
        qos_dict[name] = {
            "max_wall_minutes": q.max_wall_minutes,
            "max_nodes": q.max_nodes,
            "max_gpus_total": q.max_gpus_total,
            "max_jobs_per_user": q.max_jobs_per_user,
            "max_submit_per_user": q.max_submit_per_user,
            "priority": q.priority,
        }
    part_dict = {}
    for name, p in info.partitions.items():
        part_dict[name] = {
            "max_time_minutes": p.max_time_minutes,
            "max_nodes": p.max_nodes,
            "total_nodes": p.total_nodes,
            "has_gpus": p.has_gpus,
            "gpu_model": p.gpu_model,
        }
    return {
        "timestamp": info.timestamp,
        "user_accounts": info.user_accounts,
        "user_qos": info.user_qos,
        "qos": qos_dict,
        "partitions": part_dict,
    }


def cluster_info_from_dict(d: dict[str, Any]) -> ClusterInfo:
    """Deserialize a ``ClusterInfo`` from a YAML-loaded dict."""
    qos: dict[str, QoSInfo] = {}
    for name, q in (d.get("qos") or {}).items():
        qos[name] = QoSInfo(
            name=name,
            max_wall_minutes=q.get("max_wall_minutes"),
            max_nodes=q.get("max_nodes"),
            max_gpus_total=q.get("max_gpus_total"),
            max_jobs_per_user=q.get("max_jobs_per_user"),
            max_submit_per_user=q.get("max_submit_per_user"),
            priority=q.get("priority", 0),
        )
    partitions: dict[str, PartitionInfo] = {}
    for name, p in (d.get("partitions") or {}).items():
        partitions[name] = PartitionInfo(
            name=name,
            max_time_minutes=p.get("max_time_minutes"),
            max_nodes=p.get("max_nodes"),
            total_nodes=p.get("total_nodes", 0),
            has_gpus=p.get("has_gpus", False),
            gpu_model=p.get("gpu_model"),
        )
    return ClusterInfo(
        qos=qos,
        user_qos=d.get("user_qos", []),
        user_accounts=d.get("user_accounts", []),
        partitions=partitions,
        timestamp=d.get("timestamp", ""),
    )


# ---------------------------------------------------------------------------
# Auto-generated descriptions
# ---------------------------------------------------------------------------

def generate_use_for(qos_info: QoSInfo) -> str:
    """Generate a human-readable ``use_for`` string from QoS limits.

    >>> generate_use_for(QoSInfo("gpu_debug", max_wall_minutes=30, max_nodes=8, priority=69119))
    'quick tests, max 8 nodes, 30 min'
    """
    parts: list[str] = []

    if qos_info.max_wall_minutes is not None and qos_info.max_wall_minutes <= 30:
        parts.append("quick tests")
    elif qos_info.max_wall_minutes is not None and qos_info.max_wall_minutes >= 2880:
        parts.append("long-running jobs")

    if "preempt" in qos_info.name.lower():
        parts.append("cheaper, may be preempted")

    if qos_info.max_nodes is not None:
        parts.append(f"max {qos_info.max_nodes} nodes")

    if qos_info.max_gpus_total is not None:
        parts.append(f"max {qos_info.max_gpus_total} GPUs")

    if qos_info.max_wall_minutes is not None:
        if qos_info.max_wall_minutes < 60:
            parts.append(f"{qos_info.max_wall_minutes} min")
        else:
            hours = qos_info.max_wall_minutes / 60
            if hours == int(hours):
                parts.append(f"{int(hours)} h")
            else:
                parts.append(f"{hours:.1f} h")

    if not parts:
        parts.append("general purpose")

    return ", ".join(parts)


# QoS names to filter out in setup wizards (internal, not user-facing)
_INTERNAL_QOS = {
    "normal", "resv", "resv_shared", "cron", "xfer",
    "batchdisable", "largemem",
}


def is_user_facing_qos(name: str) -> bool:
    """Return True if the QoS is suitable for user selection."""
    if name in _INTERNAL_QOS:
        return False
    if "special" in name or "nstaff" in name:
        return False
    return True
