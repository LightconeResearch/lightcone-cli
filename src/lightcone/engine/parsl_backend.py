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
