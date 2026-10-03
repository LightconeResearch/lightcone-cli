"""Ordered resource offers, each naming the provider that supplies it."""

from __future__ import annotations

import os
import re
import socket
from pathlib import Path
from typing import Annotated, Any, Self

import yaml
from pydantic import Field, ValidationError, model_validator

from .model import (
    ComputeError,
    ComputeModel,
    Offer,
    Resources,
    Startup,
    Text,
    TimeLimits,
    validation_message,
)
from .runtime import DEFAULT_CONNECTION_ROOT


def local_disabled_reason(enabled: bool = True) -> str | None:
    """Explain a local-compute refusal while retaining inspection and termination."""
    if os.environ.get("NERSC_HOST") and re.fullmatch(
        r"login[0-9]+", socket.gethostname().split(".", 1)[0].lower(),
    ):
        return (
            "local compute is disabled on NERSC login nodes; use an interactive compute node "
            "or configure a Slurm offer and launch with --cpus and --memory"
        )
    if not enabled:
        return "local compute is disabled by the compute configuration"
    return None


class _UniqueLoader(yaml.SafeLoader):
    """Do not silently replace a setting or limit through duplicate YAML keys."""


def _unique_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise ComputeError("catalog mapping keys must be unique strings")
        result[key] = loader.construct_object(value_node)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


class LocalSettings(ComputeModel):
    """Policy for local launches, including the implicit workstation offer."""

    enabled: bool = True
    resources: Resources | None = None
    time: TimeLimits | None = None

    @model_validator(mode="after")
    def cpu_only(self) -> Self:
        if self.resources is not None and self.resources.gpus:
            raise ValueError(
                "local.resources supports CPUs and memory; configure GPU offers explicitly"
            )
        return self


class Catalog(ComputeModel):
    """Configuration for new requests, never a registry of live clusters."""

    version: Annotated[int, Field(ge=1, le=1)]
    connection_root: Annotated[Text, Field(min_length=1)] = DEFAULT_CONNECTION_ROOT
    offers: list[Offer] = Field(default_factory=list)
    local: LocalSettings = Field(default_factory=LocalSettings)

    @model_validator(mode="after")
    def unique_offers(self) -> Self:
        names: set[str] = set()
        for offer in self.offers:
            if offer.name in names:
                raise ValueError(f"duplicate offer name: {offer.name}")
            names.add(offer.name)
        return self

    @property
    def providers(self) -> list[str]:
        """Name each native authority to query, local always among them.

        A provider reaches the one authority where lc runs: this host, or
        this Slurm environment. Local stays even when disabled, so its
        allocations can still be inspected and stopped.
        """
        return list(dict.fromkeys([*(offer.provider for offer in self.offers), "local"]))

    @classmethod
    def load(cls, path: Path | None = None) -> Catalog:
        """Load configured offers and apply the local-compute policy.

        Explicit paths and existing catalogs must be readable and valid. Loading
        the built-in offer writes no catalog and allocates no compute.
        """
        configured = path is not None or "LC_COMPUTE_CONFIG" in os.environ
        path = path if path is not None else Path(
            os.environ.get("LC_COMPUTE_CONFIG", "~/.lightcone/compute.yaml")
        )
        path = path.expanduser()
        try:
            raw = yaml.load(path.read_text(), Loader=_UniqueLoader)
        except FileNotFoundError as exc:
            if configured or path.is_symlink():
                raise ComputeError(f"cannot read compute catalog {path}: {exc}") from exc
            raw = {"version": 1}
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise ComputeError(f"cannot read compute catalog {path}: {exc}") from exc
        try:
            catalog = cls.model_validate(raw)
            return catalog._with_local()
        except (ValidationError, ComputeError) as exc:
            detail = validation_message(exc) if isinstance(exc, ValidationError) else str(exc)
            raise ComputeError(
                f"invalid compute catalog {path}:\n{detail}"
            ) from exc

    def _with_local(self) -> Catalog:
        """Keep explicit local offers, or add the built-in one."""
        offers = list(self.offers)
        local = self.local
        if local_disabled_reason(local.enabled) is not None:
            local = local.replace(enabled=False)
        explicit = any(offer.provider == "local" for offer in offers)
        if explicit and (local.resources is not None or local.time is not None):
            raise ComputeError(
                "local.resources and local.time cannot be combined with explicit local "
                "offers; set those offers' resources and time instead"
            )
        if not explicit and local.enabled:
            if any(offer.name == "local" for offer in offers):
                raise ComputeError(
                    "offer name 'local' is reserved for the built-in local backend; "
                    "rename the configured offer"
                )
            from dask.system import CPU_COUNT
            from distributed.system import MEMORY_LIMIT

            resources = local.resources or Resources.from_bytes(
                cpus=CPU_COUNT, memory_bytes=MEMORY_LIMIT,
            )
            # Interactive work comes and goes: end when idle, not at a fixed age.
            offers.append(Offer(
                name="local", provider="local", resources=resources,
                max_nodes=1, time=local.time or TimeLimits(idle="30m"),
                startup=Startup(class_="fast"),
            ))
        if not local.enabled:
            offers = [offer for offer in offers if offer.provider != "local"]
        return self.replace(offers=offers, local=local)
