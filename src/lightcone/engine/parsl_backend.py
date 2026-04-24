"""Parsl backend — translates lightcone targets into a Parsl Config.

Owns three concerns:
  - Recipe resources (per-task) → WorkQueue per-task spec
  - Target ``pilots:`` dict → ``parsl.Config`` (one executor per pilot)
  - Recipe routing → which executor label handles a given recipe
  - Pre-flight QoS validation at pilot scope (replaces per-recipe clamping)
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Per-task resource mapping
# --------------------------------------------------------------------------

_MEMORY_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(MB|GB|TB)?$", re.IGNORECASE)
_TIME_HMS_RE = re.compile(r"^(\d+):(\d{1,2}):(\d{1,2})$")
_TIME_UNIT_RE = re.compile(r"^(\d+)\s*([hm])?$", re.IGNORECASE)


def _parse_memory_to_mb(value: str) -> int:
    match = _MEMORY_RE.match(value.strip())
    if not match:
        raise ValueError(f"Cannot parse memory value: {value!r}")
    amount = float(match.group(1))
    unit = (match.group(2) or "MB").upper()
    if unit == "MB":
        return int(amount)
    if unit == "GB":
        return int(amount * 1024)
    if unit == "TB":
        return int(amount * 1024 * 1024)
    raise ValueError(f"Unknown memory unit: {unit}")


def _parse_time_to_seconds(value: str | int) -> int:
    """Convert time_limit (str like '2h'/'30m'/'01:30:00' or bare-int minutes) to seconds."""
    if isinstance(value, int):
        return value * 60
    s = str(value).strip()
    if (m := _TIME_HMS_RE.match(s)):
        h, mi, se = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return h * 3600 + mi * 60 + se
    if (m := _TIME_UNIT_RE.match(s)):
        n, unit = int(m.group(1)), (m.group(2) or "m").lower()
        return n * 3600 if unit == "h" else n * 60
    raise ValueError(f"Cannot parse time_limit: {value!r}")


def recipe_resources_to_parsl(resources: dict[str, Any]) -> dict[str, Any]:
    """Translate ASTRA recipe.resources to a WorkQueue per-task spec.

    Output keys: ``cores``, ``memory`` (MB), ``gpus``, ``wall_time`` (seconds).
    Pilot-level keys (``nodes``) are intentionally dropped — those describe
    the allocation, not a single task.
    """
    spec: dict[str, Any] = {}
    if cpus := resources.get("cpus"):
        spec["cores"] = cpus
    if mem := resources.get("memory"):
        spec["memory"] = _parse_memory_to_mb(mem)
    if gpus := resources.get("gpus"):
        spec["gpus"] = gpus
    if (tl := resources.get("time_limit")) is not None:
        spec["wall_time"] = _parse_time_to_seconds(tl)
    return spec


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------


class PilotRoutingError(RuntimeError):
    """Raised when a recipe cannot be routed to any configured pilot."""


def pick_executor(
    resources: dict[str, Any],
    pilots: dict[str, Any],
) -> str:
    """Pick the executor label for a recipe given the configured pilots.

    Routing rule (deterministic, in this order):
      1. ``resources.nodes > 1`` and ``mpi`` pilot exists → ``mpi``
      2. ``resources.gpus > 0`` and ``gpu`` pilot exists → ``gpu``
      3. ``cpu`` pilot exists → ``cpu``

    Raises :class:`PilotRoutingError` if no rule matches — better to fail
    fast at task dispatch than to silently route to the wrong pool.
    """
    if not pilots:
        raise PilotRoutingError(
            "no pilots configured in target; cannot route any recipe"
        )

    if resources.get("nodes", 1) > 1 and "mpi" in pilots:
        return "mpi"

    if resources.get("gpus") and "gpu" in pilots:
        return "gpu"

    if resources.get("gpus"):
        # GPU recipe but no GPU pilot — fail fast rather than dispatch to cpu.
        raise PilotRoutingError(
            f"recipe needs gpus={resources['gpus']} but no 'gpu' pilot "
            f"is configured in target (available: {sorted(pilots)})"
        )

    if "cpu" in pilots:
        return "cpu"

    raise PilotRoutingError(
        f"no suitable pilot for resources={resources}; "
        f"available: {sorted(pilots)} (need 'cpu')"
    )


# --------------------------------------------------------------------------
# Config construction
# --------------------------------------------------------------------------


class MissingWorkQueueError(RuntimeError):
    """ndcctools / work_queue not installed; required for SLURM backend."""


def _walltime_to_hms(value: str | int) -> str:
    """Convert walltime ('2h', '30m', 120, '01:30:00') to HH:MM:SS for SlurmProvider."""
    seconds = _parse_time_to_seconds(value)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _build_pilot_executor(label: str, pilot: dict[str, Any]):
    """Build one WorkQueueExecutor wrapping a SlurmProvider for a single pilot."""
    try:
        from parsl.executors import WorkQueueExecutor
        from parsl.providers import SlurmProvider
    except ImportError as e:
        raise MissingWorkQueueError(
            "WorkQueueExecutor requires the 'ndcctools' package "
            "(provides the 'work_queue' Python module). "
            "Install via: conda install -c conda-forge ndcctools"
        ) from e

    provider_kwargs: dict[str, Any] = {
        "nodes_per_block": pilot["nodes"],
        "walltime": _walltime_to_hms(pilot["walltime"]),
        "init_blocks": 1,
        "min_blocks": 1,
        "max_blocks": 1,
        "exclusive": pilot.get("exclusive", True),
    }
    for key in ("account", "qos", "partition", "constraint", "worker_init"):
        if (val := pilot.get(key)) is not None:
            provider_kwargs[key] = val
    if extra := pilot.get("scheduler_options"):
        provider_kwargs["scheduler_options"] = extra

    provider = SlurmProvider(**provider_kwargs)

    return WorkQueueExecutor(
        label=label,
        provider=provider,
        # Lets recipes declare per-task resources (cores, memory, gpus,
        # wall_time) so WorkQueue can bin-pack heterogeneous tasks within
        # the allocation.
        autolabel=False,
        autocategory=False,
    )


def build_parsl_config(
    target_config: dict[str, Any],
    project_root=None,
):
    """Build a ``parsl.Config`` from a lightcone target dict.

    *project_root* (optional ``Path``) — if given, the DFK's run_dir is
    rooted under ``<project_root>/results/.parsl`` to mirror today's
    ``results/.slurm`` convention.

    Raises ``ValueError`` for missing/empty ``pilots``;
    raises ``MissingWorkQueueError`` if ndcctools is not installed.
    """
    try:
        from parsl.config import Config
    except ImportError as e:
        raise MissingWorkQueueError(
            "WorkQueueExecutor requires the 'ndcctools' package "
            "(provides the 'work_queue' Python module). "
            "Install via: conda install -c conda-forge ndcctools"
        ) from e

    pilots = target_config.get("pilots") or {}
    if not pilots:
        raise ValueError(
            "target.pilots must be a non-empty mapping; "
            "this target has no pilots configured"
        )

    executors = [_build_pilot_executor(label, p) for label, p in pilots.items()]

    run_dir = "runinfo"
    if project_root is not None:
        from pathlib import Path

        run_dir = str(Path(project_root) / "results" / ".parsl")

    return Config(
        executors=executors,
        run_dir=run_dir,
        # Pilot is fixed-size; turn off autoscale so Parsl never tries to
        # provision additional blocks. min == max == init == 1 already
        # pins it, but strategy='none' makes the intent explicit.
        strategy="none",
    )
