"""Pure helpers shared between the legacy runner and the dagster-slurm adapter.

These were previously private functions inside ``runner.py``. They are moved
here so the new ``compute_adapter`` can reuse them without circularly
depending on the runner (which will eventually shrink to the Docker backend
only, per ADR-0001 Phase E).
"""
from __future__ import annotations

import re
from typing import Any

__all__ = ["normalise_time_limit", "resources_to_slurm_opts"]


def normalise_time_limit(value: str | int) -> str:
    """Convert time_limit values like ``"2h"``, ``"30m"``, ``120`` to ``HH:MM:SS``.

    Bare integers and bare numeric strings are interpreted as minutes.
    Values already in ``HH:MM:SS`` (or any unrecognised format) are returned
    unchanged so upstream tooling can still accept them.
    """
    if isinstance(value, int):
        hours, minutes = divmod(value, 60)
        return f"{hours:02d}:{minutes:02d}:00"
    value = str(value).strip()
    match = re.match(r"^(\d+)([hm]?)$", value, re.IGNORECASE)
    if match:
        num, unit = int(match.group(1)), match.group(2).lower()
        if unit == "h":
            return f"{num:02d}:00:00"
        # bare number or explicit "m" → minutes
        hours, minutes = divmod(num, 60)
        return f"{hours:02d}:{minutes:02d}:00"
    return value


def resources_to_slurm_opts(resources: dict[str, Any] | None) -> dict[str, Any]:
    """Translate a recipe ``resources`` dict to dagster-slurm ``extra_slurm_opts``.

    Mapping (ADR-0001 §4.2):

    * ``cpus`` → ``cpus_per_task``
    * ``memory`` → ``mem``
    * ``gpus`` → ``gpus_per_node``
    * ``nodes`` → ``nodes``
    * ``time_limit`` → ``time_limit`` (normalised to ``HH:MM:SS``)

    Unknown keys are ignored. Missing keys do not appear in the output — the
    dagster-slurm resource's own defaults apply.
    """
    if not resources:
        return {}
    out: dict[str, Any] = {}
    if "cpus" in resources:
        out["cpus_per_task"] = resources["cpus"]
    if "memory" in resources:
        out["mem"] = resources["memory"]
    if "gpus" in resources:
        out["gpus_per_node"] = resources["gpus"]
    if "nodes" in resources:
        out["nodes"] = resources["nodes"]
    if "time_limit" in resources:
        out["time_limit"] = normalise_time_limit(resources["time_limit"])
    return out
