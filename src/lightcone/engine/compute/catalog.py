"""Ordered resource offers, each naming the provider that supplies it."""

from __future__ import annotations

import os
import re
import socket
import sys
from pathlib import Path
from typing import Annotated, Any, Self

import yaml
from pydantic import AfterValidator, Field, ValidationError, model_validator

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
from .runtime import DEFAULT_CONNECTION_ROOT, configured_directory


def local_disabled_reason(allowed: bool = True) -> str | None:
    """Explain a local-compute refusal while retaining inspection and termination."""
    if os.environ.get("NERSC_HOST") and re.fullmatch(
        r"login[0-9]+", socket.gethostname().split(".", 1)[0].lower(),
    ):
        return (
            "local compute is disabled on NERSC login nodes; use an interactive compute node "
            "or configure a Slurm offer and launch with --cpus and --memory"
        )
    if not allowed:
        return "local compute is disabled by allow_local: false in the compute catalog"
    return None


def cuda_device_count() -> int:
    """Count the GPUs CUDA_VISIBLE_DEVICES exposes on Linux, reading it as CUDA does.

    The mask is the allocation, so nothing probes hardware. CUDA stops at the
    first entry that is neither an index nor a device UUID, so ``-1`` exposes none.
    """
    if sys.platform != "linux":
        return 0
    count = 0
    for entry in os.environ.get("CUDA_VISIBLE_DEVICES", "").split(","):
        if not re.fullmatch(r"[0-9]+|(?:GPU|MIG)-\S+", entry.strip()):
            break
        count += 1
    return count


def _resolved_root(value: str) -> str:
    try:
        return str(configured_directory(Path(value)))
    except ComputeError as exc:
        raise ValueError(str(exc)) from exc


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


class Catalog(ComputeModel):
    """Configuration for new requests, never a registry of live clusters."""

    #: Resolved once here, so providers append managed paths to a physical root.
    connection_root: Annotated[Text, AfterValidator(_resolved_root)] = Field(
        DEFAULT_CONNECTION_ROOT, validate_default=True,
    )
    offers: list[Offer] = Field(default_factory=list)
    #: False blocks local launch and execution; inspection and termination remain.
    allow_local: bool = True

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
        try:
            path = path.expanduser()
            raw = yaml.load(path.read_text(), Loader=_UniqueLoader)
            # With no required keys, an empty document is the empty catalog.
            raw = {} if raw is None else raw
        except FileNotFoundError as exc:
            if configured or path.is_symlink():
                raise ComputeError(f"cannot read compute catalog {path}: {exc}") from exc
            raw = {}
        except (OSError, RuntimeError, UnicodeError, yaml.YAMLError) as exc:
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
        """Keep explicit local offers, or add the built-in one with the mask's GPUs."""
        offers = list(self.offers)
        if local_disabled_reason(self.allow_local):
            offers = [offer for offer in offers if offer.provider != "local"]
        elif not any(offer.provider == "local" for offer in offers):
            if any(offer.name == "local" for offer in offers):
                raise ComputeError(
                    "offer name 'local' is reserved for the built-in local backend; "
                    "rename the configured offer"
                )
            from dask.system import CPU_COUNT
            from distributed.system import MEMORY_LIMIT

            # Interactive work comes and goes: end when idle, not at a fixed age.
            offers.append(Offer(
                name="local", provider="local",
                resources=Resources.from_bytes(
                    cpus=CPU_COUNT, memory_bytes=MEMORY_LIMIT, gpus=cuda_device_count(),
                ),
                max_nodes=1, time=TimeLimits(idle="30m"), startup=Startup(class_="fast"),
            ))
        return self.replace(offers=offers)
