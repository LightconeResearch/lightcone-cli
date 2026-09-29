"""Recipe resource requests and admission to stock Dask workers."""

from __future__ import annotations

import math
import re
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from lightcone.engine.project import ProjectError
from lightcone.engine.units import whole_bytes


class TaskResources(BaseModel):
    """Reserve CPU, memory, and GPU capacity on stock Dask workers."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    cpus: int = Field(default=1, gt=0)
    gpus: int = Field(default=0, ge=0)
    memory_bytes: int | None = Field(default=None, gt=0)

    @field_validator("cpus", mode="before")
    @classmethod
    def _whole_cpus(cls, value: object) -> object:
        # ASTRA permits fractional CPUs. This executor reserves whole CPUs;
        # accepting 4.0 is exact, whereas rounding 0.5 would hide a policy change.
        if isinstance(value, float):
            if not math.isfinite(value) or not value.is_integer():
                raise ValueError("fractional CPUs are not supported; request whole CPUs")
            return int(value)
        return value

    @classmethod
    def parse(cls, value: object) -> Self:
        """Parse ASTRA's ``recipe.resources`` into explicit execution units.

        Args:
            value: The recipe resource mapping, or ``None`` when omitted.

        Returns:
            A validated CPU, memory, and GPU request.

        Raises:
            ProjectError: A requirement is invalid or cannot be honored.
        """
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise ProjectError("recipe.resources must be a mapping")
        if "time_limit" in value:
            raise ProjectError(
                "recipe time_limit is not supported; use cluster allocation walltime"
            )
        if extra := value.keys() - {"cpus", "memory", "gpus"}:
            names = ", ".join(sorted(map(str, extra)))
            raise ProjectError(f"unsupported recipe resource requirements: {names}")
        parsed = {"cpus": value.get("cpus", 1), "gpus": value.get("gpus", 0)}
        if "memory" in value:
            parsed["memory_bytes"] = _memory(value["memory"])
        try:
            return cls.model_validate(parsed)
        except ValidationError as exc:
            detail = "; ".join(
                f"{'.'.join(map(str, item['loc']))}: {item['msg']}"
                for item in exc.errors(include_url=False, include_input=False)
            )
            raise ProjectError(f"invalid recipe resources: {detail}") from exc

    def requirements(
        self, workers: dict[str, Any], *, whole_worker: bool = False
    ) -> dict[str, float]:
        """Choose Dask resource reservations that fit an individual worker.

        Args:
            workers: The ``workers`` mapping from Dask's scheduler information.
            whole_worker: Reserve a worker's entire CPU, memory, and GPU budget for
                an arbitrary command without declared resource requirements.

        Returns:
            Dask's numeric ``CPU``, ``MEMORY``, and optional ``GPU`` reservations.
            GPU recipes reserve the worker's full GPU budget, so only one GPU
            recipe uses that worker's device mask at a time.

        Raises:
            ProjectError: Capacity is unknown, a request cannot fit, or an
                unspecified budget is ambiguous across heterogeneous workers.
        """
        capacities: set[tuple[float, float, float]] = set()
        for info in workers.values():
            resources = info.get("resources", {}) if isinstance(info, dict) else {}
            values = []
            for name in ("CPU", "MEMORY", "GPU"):
                value = (
                    resources.get(name, 0 if name == "GPU" else None)
                    if isinstance(resources, dict) else None
                )
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or (value < 0 if name == "GPU" else value <= 0)
                    or not float(value).is_integer()
                ):
                    raise ProjectError(
                        "cluster workers must advertise positive whole CPU and MEMORY budgets "
                        "and a nonnegative whole GPU count; "
                        "relaunch the cluster with the current Lightcone installation"
                    )
                values.append(float(value))
            capacities.add((values[0], values[1], values[2]))
        if not capacities:
            raise ProjectError("cluster has no workers available for execution")
        if (
            (whole_worker or self.memory_bytes is None)
            and len({(cpus, memory) for cpus, memory, _ in capacities}) != 1
        ):
            raise ProjectError(
                "unspecified task resources require workers with identical CPU and memory budgets"
            )
        available_cpus, available_memory, _ = next(iter(capacities))
        requested = {
            "CPU": available_cpus if whole_worker else float(self.cpus),
            "MEMORY": (
                available_memory
                if whole_worker or self.memory_bytes is None
                else float(self.memory_bytes)
            ),
        }
        matches = {
            gpus for cpus, memory, gpus in capacities
            if cpus >= requested["CPU"] and memory >= requested["MEMORY"] and gpus >= self.gpus
        }
        if not matches:
            raise ProjectError(
                f"task needs {requested['CPU']:g} CPUs and "
                f"{requested['MEMORY'] / 1024**3:g} GiB and {self.gpus} GPUs on one worker; "
                "no worker in this cluster can satisfy that request"
            )
        if self.gpus or whole_worker:
            if len(matches) != 1:
                raise ProjectError(
                    "GPU execution requires matching workers with identical GPU budgets"
                )
            if gpus := next(iter(matches)):
                requested["GPU"] = gpus
        return requested


def _memory(value: object) -> int:
    if not isinstance(value, str) or not (
        match := re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([KMGTPE]i?B?|kB?|B)", value)
    ):
        raise ProjectError("recipe memory must include units, e.g. 512Mi, 16Gi, or 8GB")
    unit = match[2].lower()
    exponent = 0 if unit == "b" else "kmgtpe".index(unit[0]) + 1
    factor: int = (1024 if "i" in unit else 1000) ** exponent
    try:
        return whole_bytes(match[1], factor)
    except ValueError as exc:
        raise ProjectError(f"recipe {exc}") from exc
