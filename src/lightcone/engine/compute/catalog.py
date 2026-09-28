"""Ordered resource offers and stable native service namespaces."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, Self
from uuid import UUID

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

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


def _name(value: str) -> str:
    if not value.strip():
        raise ValueError("must be a nonempty string")
    return value


def _namespace(value: str) -> str:
    try:
        if str(UUID(value)) == value:
            return value
    except ValueError:
        pass
    raise ValueError("must be a canonical UUID")


def _count(value: object) -> int:
    try:
        return positive_int(value, "count")
    except ComputeError as exc:
        raise ValueError(str(exc)) from exc


def _memory(value: object) -> int:
    try:
        return memory_bytes(value)
    except ComputeError as exc:
        raise ValueError(str(exc)) from exc


def _seconds(value: object) -> int:
    try:
        return duration(value)
    except ComputeError as exc:
        raise ValueError(str(exc)) from exc


_Text = Annotated[str, Field(pattern=r"^[^\x00-\x1f]*$")]
_Name = Annotated[_Text, AfterValidator(_name)]
_Namespace = Annotated[str, AfterValidator(_namespace)]
_Count = Annotated[int, BeforeValidator(_count, json_schema_input_type=int | str)]
_Memory = Annotated[int, BeforeValidator(_memory, json_schema_input_type=int | float | str)]
_Seconds = Annotated[int, BeforeValidator(_seconds, json_schema_input_type=str)]
_StartupClass = Literal["fast", "batch", "unknown"]


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _ConnectionConfig(_Config):
    namespace: _Namespace
    provider: _Name
    context: _Text = ""
    launch: dict[str, Any] = Field(default_factory=dict)


class _ResourcesConfig(_Config):
    cpus: _Count
    memory: _Memory


class _TimeConfig(_Config):
    default: _Seconds
    max: _Seconds

    @model_validator(mode="after")
    def ordered_limits(self) -> Self:
        if self.default > self.max:
            raise ValueError("default time exceeds its maximum")
        return self


class _StartupConfig(_Config):
    class_: _StartupClass = Field(alias="class")
    source: Any = None


class _OfferConfig(_Config):
    name: _Name
    connection: _Name
    resources: _ResourcesConfig
    max_nodes: _Count
    time: _TimeConfig
    startup: _StartupClass | _StartupConfig = "unknown"
    config: dict[str, Any] = Field(default_factory=dict)


class _CatalogConfig(_Config):
    version: Annotated[int, Field(ge=1, le=1)]
    connections: dict[_Name, _ConnectionConfig]
    offers: list[_OfferConfig]

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
            os.environ.get("LC_COMPUTE_CONFIG", "~/.lightcone/compute.yaml")
        )
        path = path.expanduser()
        try:
            raw = yaml.load(path.read_text(), Loader=_UniqueLoader)
        except FileNotFoundError as exc:
            if configured or path.is_symlink():
                raise ComputeError(f"cannot read compute catalog {path}: {exc}") from exc
            return cls(
                connections={"local": Connection("local", _LOCAL_NAMESPACE, "local")},
                offers=(Offer("local", "local", Resources(1, GIB), 1, 1800, 7200, "fast"),),
            )
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise ComputeError(f"cannot read compute catalog {path}: {exc}") from exc
        try:
            config = _CatalogConfig.model_validate(raw)
        except ValidationError as exc:
            details = "\n".join(
                f"{'.'.join(map(str, error['loc'])) or 'catalog'}: {error['msg']}"
                for error in exc.errors(
                    include_url=False, include_context=False, include_input=False
                )
            )
            raise ComputeError(f"invalid compute catalog {path}:\n{details}") from exc
        return cls(
            connections={
                name: Connection(name, item.namespace, item.provider, item.context, item.launch)
                for name, item in config.connections.items()
            },
            offers=tuple(
                Offer(
                    item.name,
                    item.connection,
                    Resources(item.resources.cpus, item.resources.memory),
                    item.max_nodes,
                    item.time.default,
                    item.time.max,
                    item.startup if isinstance(item.startup, str) else item.startup.class_,
                    item.config,
                )
                for item in config.offers
            ),
        )

    def connection_for(self, namespace: str) -> Connection:
        """Find the configured authority without relying on current offers."""
        for connection in self.connections.values():
            if connection.namespace == namespace:
                return connection
        raise ComputeError("cluster's connection namespace is absent from the compute catalog")
