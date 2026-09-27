"""Ordered resource offers and stable native service namespaces."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

import yaml

from .model import (
    GIB,
    ComputeError,
    Connection,
    Offer,
    Resources,
    duration,
    memory_bytes,
    positive_int,
)

# Isolate hosts sharing a home directory while keeping fresh CLI invocations stable.
_LOCAL_NAMESPACE = UUID("22c84e48-2f0a-4cd2-90a2-30ce2e909bd1")


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


def _mapping(value: object, where: str, allowed: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ComputeError(f"{where} must be a mapping")
    if allowed is not None and (extra := set(value) - allowed):
        raise ComputeError(f"unknown {where} field(s): {', '.join(sorted(extra))}")
    return value


def _name(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
        raise ComputeError(f"{where} must be a nonempty string without control characters")
    return value


@dataclass(frozen=True)
class Catalog:
    """Configuration for new requests, never a registry of live clusters."""

    connections: dict[str, Connection]
    offers: tuple[Offer, ...]

    @classmethod
    def load(cls, path: Path | None = None) -> Catalog:
        """Load configured offers, or a small local offer if the default file is absent.

        Explicit paths and existing catalogs must be readable and valid. Loading
        the built-in offer writes no catalog and allocates no compute.
        """
        configured = path is not None or "LC_COMPUTE_CONFIG" in os.environ
        path = path if path is not None else Path(
            os.environ.get("LC_COMPUTE_CONFIG", "~/lightcone-compute.yaml")
        )
        path = path.expanduser()
        try:
            raw = yaml.load(path.read_text(), Loader=_UniqueLoader)
        except FileNotFoundError as exc:
            if configured or path.is_symlink():
                raise ComputeError(f"cannot read compute catalog {path}: {exc}") from exc
            return cls(
                connections={
                    "local": Connection(
                        "local", str(uuid5(_LOCAL_NAMESPACE, socket.gethostname())), "local"
                    )
                },
                offers=(Offer("local", "local", Resources(1, GIB), 1, 1800, 7200, "fast"),),
            )
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise ComputeError(f"cannot read compute catalog {path}: {exc}") from exc
        data = _mapping(raw, "catalog", {"version", "connections", "offers"})
        if type(data.get("version")) is not int or data["version"] != 1:
            raise ComputeError("compute catalog version must be 1")
        connections: dict[str, Connection] = {}
        namespaces: set[str] = set()
        contexts: set[tuple[str, str]] = set()
        for name, entry in _mapping(data.get("connections"), "connections").items():
            _name(name, "connection name")
            item = _mapping(
                entry, f"connection {name}", {"namespace", "provider", "context", "launch"}
            )
            namespace = _name(item.get("namespace"), f"connection {name} namespace")
            try:
                if str(UUID(namespace)) != namespace:
                    raise ValueError
            except ValueError as exc:
                raise ComputeError(f"connection {name} namespace must be a canonical UUID") from exc
            provider = _name(item.get("provider"), f"connection {name} provider")
            context = item.get("context", "")
            if not isinstance(context, str) or any(ord(c) < 32 for c in context):
                raise ComputeError(f"connection {name} context must be a string")
            if namespace in namespaces or (provider, context) in contexts:
                raise ComputeError("connections must have unique namespaces and native contexts")
            namespaces.add(namespace)
            contexts.add((provider, context))
            connections[name] = Connection(
                name,
                namespace,
                provider,
                context,
                _mapping(item.get("launch", {}), f"connection {name} launch"),
            )
        raw_offers = data.get("offers")
        if not isinstance(raw_offers, list):
            raise ComputeError("offers must be an ordered list")
        offers: list[Offer] = []
        names: set[str] = set()
        for raw_offer in raw_offers:
            item = _mapping(
                raw_offer,
                "offer",
                {
                    "name",
                    "connection",
                    "resources",
                    "max_nodes",
                    "time",
                    "startup",
                    "config",
                },
            )
            name = _name(item.get("name"), "offer name")
            if name in names:
                raise ComputeError(f"duplicate offer name: {name}")
            names.add(name)
            connection = _name(item.get("connection"), f"offer {name} connection")
            if connection not in connections:
                raise ComputeError(f"offer {name} references unknown connection {connection}")
            resources = _mapping(
                item.get("resources"), f"offer {name} resources", {"cpus", "memory"}
            )
            timing = _mapping(item.get("time"), f"offer {name} time", {"default", "max"})
            default, maximum = duration(timing.get("default")), duration(timing.get("max"))
            if default > maximum:
                raise ComputeError(f"offer {name} default time exceeds its maximum")
            startup = item.get("startup", {"class": "unknown"})
            if isinstance(startup, dict):
                startup = _mapping(startup, f"offer {name} startup", {"class", "source"}).get(
                    "class"
                )
            if startup not in ("fast", "batch", "unknown"):
                raise ComputeError(f"offer {name} startup class must be fast, batch, or unknown")
            offers.append(
                Offer(
                    name,
                    connection,
                    Resources(
                        positive_int(resources.get("cpus"), "cpus"),
                        memory_bytes(resources.get("memory")),
                    ),
                    positive_int(item.get("max_nodes"), "max_nodes"),
                    default,
                    maximum,
                    startup,
                    _mapping(item.get("config", {}), f"offer {name} config"),
                )
            )
        return cls(connections, tuple(offers))

    def connection_for(self, namespace: str) -> Connection:
        """Find the configured authority without relying on current offers."""
        for connection in self.connections.values():
            if connection.namespace == namespace:
                return connection
        raise ComputeError("cluster's connection namespace is absent from the compute catalog")
