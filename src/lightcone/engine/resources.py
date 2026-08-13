"""Canonical ASTRA recipe-resource parsing.

ASTRA records portable resource hints on ``Recipe.resources`` using the
``cpus`` / ``memory`` / ``gpus`` / ``time_limit`` vocabulary.  Lightcone
normalizes those values once here so synchronous Snakemake rules and
asynchronous SLURM submissions cannot interpret them differently.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any


class ResourceValueError(ValueError):
    """A recipe resource value cannot be interpreted safely."""


@dataclass(frozen=True)
class RecipeResources:
    """Normalized resources for one recipe."""

    cpus: int = 1
    memory_mb: int = 0
    gpus: int = 0
    time_limit_seconds: int | None = None

    def snakemake(self) -> dict[str, int]:
        """Return the canonical Snakemake resource-name mapping.

        Snakemake's conventional ``runtime`` resource is expressed in
        minutes.  All other normalized values are already in the units
        consumed by Lightcone's Dask executor.
        """
        result = {"cpus_per_task": self.cpus}
        if self.memory_mb:
            result["mem_mb"] = self.memory_mb
        if self.gpus:
            result["gpus_per_task"] = self.gpus
        if self.time_limit_seconds is not None:
            result["runtime"] = math.ceil(self.time_limit_seconds / 60)
        return result


_MEMORY_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?i?b)\s*$", re.IGNORECASE)
_MEMORY_MB = {
    "b": 1 / 1_000_000,
    "kb": 1 / 1_000,
    "kib": 1024 / 1_000_000,
    "mb": 1,
    "mib": 1024**2 / 1_000_000,
    "gb": 1_000,
    "gib": 1024**3 / 1_000_000,
    "tb": 1_000_000,
    "tib": 1024**4 / 1_000_000,
}


def parse_memory_mb(value: str) -> int:
    """Parse an ASTRA memory string (for example ``8GB``) into MB."""
    match = _MEMORY_RE.fullmatch(value)
    if match is None:
        raise ResourceValueError(
            f"Invalid memory value {value!r}; use a value such as '8GB' or '512MB'."
        )
    amount = float(match.group(1))
    return max(1, math.ceil(amount * _MEMORY_MB[match.group(2).lower()]))


_TIME_TOKEN_RE = re.compile(r"(\d+(?:\.\d+)?)([smhd])", re.IGNORECASE)
_TIME_MULTIPLIER = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_time_seconds(value: str) -> int:
    """Parse an ASTRA duration into seconds.

    Supports compact durations (``30m``, ``2h``, ``1h30m``) and SLURM-style
    ``HH:MM:SS`` / ``D-HH:MM:SS`` strings.
    """
    raw = value.strip()
    if not raw:
        raise ResourceValueError("time_limit must not be empty.")

    if ":" in raw:
        day_part = "0"
        clock = raw
        if "-" in raw:
            day_part, clock = raw.split("-", 1)
        pieces = clock.split(":")
        if len(pieces) not in {2, 3} or not day_part.isdigit():
            raise ResourceValueError(f"Invalid time_limit {value!r}.")
        try:
            numbers = [int(piece) for piece in pieces]
        except ValueError as exc:
            raise ResourceValueError(f"Invalid time_limit {value!r}.") from exc
        if len(numbers) == 2:
            hours = 0
            minutes, seconds = numbers
        else:
            hours, minutes, seconds = numbers
        if minutes >= 60 or seconds >= 60:
            raise ResourceValueError(f"Invalid time_limit {value!r}.")
        total = int(day_part) * 86400 + hours * 3600 + minutes * 60 + seconds
        if total <= 0:
            raise ResourceValueError("time_limit must be greater than zero.")
        return total

    compact_total = 0.0
    position = 0
    for match in _TIME_TOKEN_RE.finditer(raw):
        if match.start() != position:
            raise ResourceValueError(
                f"Invalid time_limit {value!r}; use a value such as '2h' or '30m'."
            )
        compact_total += (
            float(match.group(1)) * _TIME_MULTIPLIER[match.group(2).lower()]
        )
        position = match.end()
    if position != len(raw) or compact_total <= 0:
        raise ResourceValueError(
            f"Invalid time_limit {value!r}; use a value such as '2h' or '30m'."
        )
    return math.ceil(compact_total)


def parse_recipe_resources(
    recipe: dict[str, Any],
    *,
    require_time_limit: bool = False,
    label: str = "recipe",
) -> RecipeResources:
    """Normalize one ASTRA recipe's resource declaration."""
    raw = recipe.get("resources") or {}
    if not isinstance(raw, dict):
        raise ResourceValueError(f"{label} resources must be a mapping.")

    cpus = raw.get("cpus") or 1
    gpus = raw.get("gpus") or 0
    if not isinstance(cpus, int) or isinstance(cpus, bool) or cpus < 1:
        raise ResourceValueError(f"{label} resources.cpus must be an integer >= 1.")
    if not isinstance(gpus, int) or isinstance(gpus, bool) or gpus < 0:
        raise ResourceValueError(f"{label} resources.gpus must be an integer >= 0.")

    memory = raw.get("memory")
    if memory is not None and not isinstance(memory, str):
        raise ResourceValueError(f"{label} resources.memory must be a string such as '8GB'.")
    memory_mb = parse_memory_mb(memory) if memory else 0

    time_limit = raw.get("time_limit")
    if require_time_limit and not time_limit:
        raise ResourceValueError(f"{label} is missing resources.time_limit.")
    if time_limit is not None and not isinstance(time_limit, str):
        raise ResourceValueError(f"{label} resources.time_limit must be a string such as '2h'.")
    time_limit_seconds = parse_time_seconds(time_limit) if time_limit else None

    return RecipeResources(
        cpus=cpus,
        memory_mb=memory_mb,
        gpus=gpus,
        time_limit_seconds=time_limit_seconds,
    )


__all__ = [
    "RecipeResources",
    "ResourceValueError",
    "parse_memory_mb",
    "parse_recipe_resources",
    "parse_time_seconds",
]
