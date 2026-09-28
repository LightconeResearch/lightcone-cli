"""Ordered resource offers and stable native service namespaces."""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Self

import yaml
from pydantic import Field, ValidationError, model_validator

from .model import (
    ComputeError,
    ComputeModel,
    Connection,
    Name,
    Offer,
    Resources,
    Startup,
    TimeLimits,
    validation_message,
)

# Native boot/session evidence identifies the host, independently of its hostname.
_LOCAL_NAMESPACE = "22c84e48-2f0a-4cd2-90a2-30ce2e909bd1"


class _UniqueLoader(yaml.SafeLoader):
    """Do not silently replace a connection or limit through duplicate YAML keys."""


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

    version: Annotated[int, Field(ge=1, le=1)]
    connections: dict[Name, Connection]
    offers: list[Offer]

    @model_validator(mode="after")
    def relationships(self) -> Self:
        namespaces: set[str] = set()
        contexts: set[tuple[str, str]] = set()
        for connection in self.connections.values():
            context = (connection.provider, connection.context)
            if connection.namespace in namespaces or context in contexts:
                raise ValueError("connections must have unique namespaces and native contexts")
            namespaces.add(connection.namespace)
            contexts.add(context)
        names: set[str] = set()
        for offer in self.offers:
            if offer.name in names:
                raise ValueError(f"duplicate offer name: {offer.name}")
            names.add(offer.name)
            if offer.connection not in self.connections:
                raise ValueError(
                    f"offer {offer.name} references unknown connection {offer.connection}"
                )
        return self

    @classmethod
    def load(cls, path: Path | None = None) -> Catalog:
        """Load configured offers, or a small local offer if the default file is absent.

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
            return cls(
                version=1,
                connections={"local": Connection(namespace=_LOCAL_NAMESPACE, provider="local")},
                offers=[Offer(
                    name="local", connection="local",
                    resources=Resources(cpus=1, memory_gib=Decimal(1)),
                    max_nodes=1, time=TimeLimits(default="30m", max="2h"),
                    startup=Startup(class_="fast"),
                )],
            )
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise ComputeError(f"cannot read compute catalog {path}: {exc}") from exc
        try:
            return cls.model_validate(raw)
        except ValidationError as exc:
            raise ComputeError(
                f"invalid compute catalog {path}:\n{validation_message(exc)}"
            ) from exc

    def connection_for(self, namespace: str) -> Connection:
        """Find the configured authority without relying on current offers."""
        for connection in self.connections.values():
            if connection.namespace == namespace:
                return connection
        raise ComputeError("cluster's connection namespace is absent from the compute catalog")
