"""Common resource requests, native identities, and provider observations."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from uuid import UUID

from lightcone.engine.project import ProjectError

GIB = 1024**3


class ComputeError(ProjectError):
    """A failed compute operation, retaining any accepted allocation identity."""

    def __init__(
        self, message: str, *, cluster_id: str | None = None, submission_token: str | None = None
    ) -> None:
        super().__init__(message)
        self.cluster_id = cluster_id
        self.submission_token = submission_token


class UnavailableOfferError(ComputeError):
    """A valid offer that cannot supply resources in this execution context."""


def duration(value: object) -> int:
    """Parse an explicit positive whole-minute/hour duration into seconds."""
    match = re.fullmatch(r"([1-9][0-9]*)([mh])", str(value))
    if match is None:
        raise ComputeError("duration must be a positive number of minutes or hours, e.g. 30m or 1h")
    return int(match[1]) * (60 if match[2] == "m" else 3600)


def memory_bytes(value: object) -> int:
    """Convert positive decimal GiB to an exact integer number of bytes."""
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", str(value)):
        raise ComputeError("memory must be a positive number of GiB")
    try:
        numerator, denominator = Decimal(str(value)).as_integer_ratio()
    except InvalidOperation as exc:
        raise ComputeError("memory must be a positive number of GiB") from exc
    amount, remainder = divmod(numerator * GIB, denominator)
    if amount <= 0 or remainder:
        raise ComputeError("memory must be positive GiB exactly representable in bytes")
    return amount


def positive_int(value: object, name: str) -> int:
    """Accept an integer count, excluding booleans and implicit rounding."""
    if isinstance(value, bool) or not re.fullmatch(r"[1-9][0-9]*", str(value)):
        raise ComputeError(f"{name} must be a positive integer")
    return int(str(value))


@dataclass(frozen=True)
class Resources:
    """The fixed per-node allocation envelope; memory is stored in bytes."""

    cpus: int
    memory: int

    def as_dict(self) -> dict[str, int | float]:
        """Render public memory in GiB."""
        return {"cpus": self.cpus, "memory": self.memory / GIB}


@dataclass(frozen=True)
class Request:
    """A provider-independent resource request."""

    cpus: int
    memory: int
    num_nodes: int = 1
    min_cpus: bool = False
    min_memory: bool = False
    seconds: int | None = None
    startup: str | None = None

    def __post_init__(self) -> None:
        for name in ("cpus", "memory", "num_nodes"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ComputeError(f"{name} must be a positive integer")
        if type(self.min_cpus) is not bool or type(self.min_memory) is not bool:
            raise ComputeError("minimum resource selectors must be booleans")
        if self.seconds is not None and (type(self.seconds) is not int or self.seconds <= 0):
            raise ComputeError("time must be a positive integer number of seconds")
        if self.startup not in (None, "fast"):
            raise ComputeError("startup must be fast or omitted")

    @classmethod
    def parse(
        cls,
        cpus: str,
        memory: str,
        *,
        num_nodes: int = 1,
        time: str | None = None,
        startup: str | None = None,
    ) -> Request:
        """Parse the CLI's exact or minimum per-node quantities."""
        if startup not in (None, "fast"):
            raise ComputeError("startup must be fast or omitted")
        return cls(
            positive_int(cpus.removesuffix("+"), "cpus"),
            memory_bytes(memory.removesuffix("+")),
            positive_int(num_nodes, "num_nodes"),
            cpus.endswith("+"),
            memory.endswith("+"),
            duration(time) if time is not None else None,
            startup,
        )

    def as_dict(self) -> dict[str, Any]:
        """Render the request without native provider settings."""
        return {
            "num_nodes": self.num_nodes,
            "resources": {
                "cpus": f"{self.cpus}{'+' if self.min_cpus else ''}",
                "memory": f"{Decimal(self.memory) / GIB:g}{'+' if self.min_memory else ''}",
            },
            "time_seconds": self.seconds,
            "startup": self.startup,
        }


@dataclass(frozen=True)
class Connection:
    """One native authority, independent of the offers that reference it."""

    name: str
    namespace: str
    provider: str
    context: str = ""
    launch: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Offer:
    """A fixed resource shape with user-supplied limits and native bindings."""

    name: str
    connection: str
    resources: Resources
    max_nodes: int
    default_seconds: int
    max_seconds: int
    startup: str = "unknown"
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Identity:
    """A self-contained native identity; its encoding is not a credential."""

    namespace: str
    native_id: str
    token: str
    host: str = ""

    def encode(self) -> str:
        """Encode identity without a local UUID-to-allocation database."""
        payload = json.dumps(
            [1, self.namespace, self.native_id, self.token, self.host], separators=(",", ":")
        ).encode()
        return "clu_" + base64.urlsafe_b64encode(payload).decode().rstrip("=")

    @classmethod
    def decode(cls, value: str) -> Identity:
        """Reject malformed, unsupported, or noncanonical IDs before using them."""
        try:
            if not re.fullmatch(r"clu_[A-Za-z0-9_-]{1,2048}", value):
                raise ValueError
            raw = value[4:]
            data = json.loads(base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=b"-_"))
            if not isinstance(data, list) or len(data) != 5 or type(data[0]) is not int:
                raise ValueError
            version, namespace, native_id, token, host = data
            if version != 1 or not all(isinstance(item, str) for item in data[1:]):
                raise ValueError
            if str(UUID(namespace)) != namespace or not native_id or not token:
                raise ValueError
            if any(ord(char) < 32 for item in data[1:] for char in item):
                raise ValueError
            identity = cls(namespace, native_id, token, host)
            if identity.encode() != value:
                raise ValueError
            return identity
        except (ValueError, TypeError, UnicodeError) as exc:
            raise ComputeError(
                "expected a cluster ID returned by lc compute launch (clu_…)"
            ) from exc


@dataclass(frozen=True)
class LaunchPlan:
    """Resolved immutable sizing and nonsecret provider launch parameters."""

    connection: Connection
    offer: Offer
    request: Request
    seconds: int
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def resources(self) -> Resources:
        return self.offer.resources

    @property
    def num_nodes(self) -> int:
        return self.request.num_nodes

    def as_dict(self) -> dict[str, Any]:
        """Allowlist the plan's public contract; details must contain no credentials."""
        return {
            "schema_version": 1,
            "request": self.request.as_dict(),
            "offer": self.offer.name,
            "connection": self.connection.name,
            "num_nodes": self.num_nodes,
            "resources": self.resources.as_dict(),
            "time_seconds": self.seconds,
            "startup": self.offer.startup,
            "launch": self.details,
        }


@dataclass
class Snapshot:
    """A fresh allocation observation; unknown state is never treated as absence."""

    identity: Identity
    phase: str
    resources: Resources | None = None
    num_nodes: int | None = None
    evidence: str = "unknown"
    reason: str = ""
    native_state: str = ""
    observation: str = "unverified"
    ready: bool | None = None
    workers: int | None = None

    def as_dict(self) -> dict[str, Any]:
        """Keep endpoint credentials and native response objects private."""
        return {
            "schema_version": 1,
            "id": self.identity.encode(),
            "phase": self.phase,
            "allocation": {
                "num_nodes": self.num_nodes,
                "resources": self.resources.as_dict() if self.resources else None,
                "evidence": self.evidence,
            },
            "dask": {"observation": self.observation, "ready": self.ready, "workers": self.workers},
            "reason": self.reason,
            "native_state": self.native_state,
        }


class Provider(Protocol):
    """Native allocation lifecycle, separate from Dask task execution."""

    def plan(self, offer: Offer, request: Request) -> LaunchPlan: ...
    def launch(self, plan: LaunchPlan) -> Identity: ...
    def discover(self) -> Sequence[Snapshot]: ...
    def inspect(self, identity: Identity) -> Snapshot: ...
    def connect(
        self, identity: Identity, *, timeout: float = 10
    ) -> AbstractContextManager[Any]: ...
    def terminate(self, identity: Identity) -> None: ...


ProviderFactory = Callable[[Connection], Provider]
