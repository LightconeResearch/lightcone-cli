"""Common resource requests, native identities, and provider observations."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from decimal import Decimal, localcontext
from typing import Annotated, Any, Literal, Protocol, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    ValidationError,
    model_validator,
)

from lightcone.engine.project import ProjectError
from lightcone.engine.units import duration_seconds, whole_bytes

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
    """Parse an explicit positive duration into seconds."""
    try:
        return duration_seconds(value)
    except ValueError as exc:
        raise ComputeError(str(exc)) from exc


def memory_bytes(value: object) -> int:
    """Convert positive decimal GiB to an exact integer number of bytes."""
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", str(value)):
        raise ComputeError("memory must be a positive number of GiB")
    try:
        return whole_bytes(str(value), GIB)
    except ValueError as exc:
        raise ComputeError("memory must be positive GiB exactly representable in bytes") from exc


def gib_from_bytes(value: int) -> Decimal:
    """Convert native bytes to exact GiB without depending on Decimal's context."""
    if type(value) is not int or value <= 0:
        raise ValueError("memory_bytes must be a positive integer")
    with localcontext() as context:
        context.prec = len(str(value)) + 30
        return Decimal(value) / GIB


def positive_int(value: object, name: str) -> int:
    """Accept an integer count, excluding booleans and implicit rounding."""
    if isinstance(value, bool) or not re.fullmatch(r"[1-9][0-9]*", str(value)):
        raise ComputeError(f"{name} must be a positive integer")
    return int(str(value))


def validate_name(value: str) -> None:
    """Keep cluster names portable across native providers and safe in commands."""
    if not re.fullmatch(r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?", value):
        raise ComputeError(
            "cluster names must be 1–63 lowercase letters, digits, or hyphens; "
            "start with a letter and end with a letter or digit"
        )


def validation_message(error: ValidationError) -> str:
    """Describe invalid fields without echoing configuration values or credentials."""
    return "\n".join(
        f"{'.'.join(map(str, item['loc'])) or 'model'}: {item['msg']}"
        for item in error.errors(include_url=False, include_context=False, include_input=False)
    )


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


def _gib(value: object) -> Decimal:
    # Decimal may render small exact values in exponent notation. Preserve all
    # digits when applying the same quantity rules as the CLI.
    literal = format(value, "f") if isinstance(value, Decimal) else value
    try:
        memory_bytes(literal)
    except ComputeError as exc:
        raise ValueError(str(exc)) from exc
    return Decimal(str(literal))


def _duration(value: str) -> str:
    try:
        duration(value)
    except ComputeError as exc:
        raise ValueError(str(exc)) from exc
    return value


Text = Annotated[str, Field(pattern=r"^[^\x00-\x1f]*$")]
Name = Annotated[Text, AfterValidator(_name)]
Namespace = Annotated[str, AfterValidator(_namespace)]
PositiveInt = Annotated[int, Field(gt=0, strict=True)]
Count = Annotated[int, BeforeValidator(_count, json_schema_input_type=int | str)]
GiB = Annotated[
    Decimal,
    BeforeValidator(_gib, json_schema_input_type=int | float | str),
    PlainSerializer(lambda value: format(value, "f"), return_type=str, when_used="json"),
]
Duration = Annotated[str, AfterValidator(_duration)]


class ComputeModel(BaseModel):
    """Validated compute values shared by catalog loading and native providers."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, populate_by_name=True)

    def replace(self, **changes: Any) -> Self:
        """Return a validated replacement; Pydantic's model_copy skips validation."""
        return type(self).model_validate({**self.model_dump(), **changes})


class Resources(ComputeModel):
    """A per-node resource envelope with explicitly named memory units."""

    cpus: Count
    memory_gib: GiB = Field(validation_alias="memory", serialization_alias="memory")

    @property
    def memory_bytes(self) -> int:
        return memory_bytes(format(self.memory_gib, "f"))

    @classmethod
    def from_bytes(cls, *, cpus: int, memory_bytes: int) -> Self:
        """Represent native byte counts exactly, independently of Decimal precision."""
        return cls(cpus=cpus, memory_gib=gib_from_bytes(memory_bytes))

    def as_dict(self) -> dict[str, int | float]:
        """Render public memory in GiB."""
        return {"cpus": self.cpus, "memory": self.memory_bytes / GIB}


class Request(ComputeModel):
    """A provider-independent resource request."""

    cpus: PositiveInt
    memory_bytes: PositiveInt
    num_nodes: PositiveInt = 1
    min_cpus: bool = False
    min_memory: bool = False
    seconds: PositiveInt | None = None
    startup: Literal["fast"] | None = None

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
        try:
            return cls.model_validate({
                "cpus": positive_int(cpus.removesuffix("+"), "cpus"),
                "memory_bytes": memory_bytes(memory.removesuffix("+")),
                "num_nodes": positive_int(num_nodes, "num_nodes"),
                "min_cpus": cpus.endswith("+"),
                "min_memory": memory.endswith("+"),
                "seconds": duration(time) if time is not None else None,
                "startup": startup,
            })
        except ValidationError as exc:
            raise ComputeError(f"invalid compute request:\n{validation_message(exc)}") from exc

    def as_dict(self) -> dict[str, Any]:
        """Render the request without native provider settings."""
        return {
            "num_nodes": self.num_nodes,
            "resources": {
                "cpus": f"{self.cpus}{'+' if self.min_cpus else ''}",
                "memory": f"{gib_from_bytes(self.memory_bytes):f}{'+' if self.min_memory else ''}",
            },
            "time_seconds": self.seconds,
            "startup": self.startup,
        }


class Connection(ComputeModel):
    """One native authority, independent of the offers that reference it."""

    namespace: Namespace
    provider: Name
    context: Text = ""
    launch: dict[str, Any] = Field(default_factory=dict)


class TimeLimits(ComputeModel):
    """Configured durations with derived seconds for native allocation requests."""

    default: Duration
    max: Duration

    @property
    def default_seconds(self) -> int:
        return duration(self.default)

    @property
    def max_seconds(self) -> int:
        return duration(self.max)

    @model_validator(mode="after")
    def ordered_limits(self) -> Self:
        if self.default_seconds > self.max_seconds:
            raise ValueError("default time exceeds its maximum")
        return self


class Startup(ComputeModel):
    """An operator's queue-speed classification and optional supporting source."""

    class_: Literal["fast", "batch", "unknown"] = Field(
        validation_alias="class", serialization_alias="class"
    )
    source: Any = None

    @model_validator(mode="before")
    @classmethod
    def shorthand(cls, value: Any) -> Any:
        return {"class": value} if isinstance(value, str) else value


class Offer(ComputeModel):
    """A fixed resource shape with user-supplied limits and native bindings."""

    name: Name
    connection: Name
    resources: Resources
    max_nodes: Count
    time: TimeLimits
    startup: Startup = Field(default_factory=lambda: Startup(class_="unknown"))
    config: dict[str, Any] = Field(default_factory=dict)


class Identity(ComputeModel):
    """A self-contained native identity; its encoding is not a credential."""

    namespace: Namespace
    native_id: Name
    token: Name
    host: Text = ""
    name: Text = ""

    @model_validator(mode="after")
    def generated_name(self) -> Self:
        if not self.name:
            object.__setattr__(self, "name", f"lc-{self.token[:12]}")
        return self

    def encode(self) -> str:
        """Encode identity without a local UUID-to-allocation database."""
        payload = json.dumps(
            [1, self.namespace, self.native_id, self.token, self.host, self.name],
            separators=(",", ":"),
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
            if not isinstance(data, list) or len(data) != 6 or type(data[0]) is not int:
                raise ValueError
            version, namespace, native_id, token, host, name = data
            if version != 1 or not all(isinstance(item, str) for item in data[1:]):
                raise ValueError
            if str(UUID(namespace)) != namespace or not native_id or not token:
                raise ValueError
            if any(ord(char) < 32 for item in data[1:] for char in item):
                raise ValueError
            validate_name(name)
            identity = cls(
                namespace=namespace, native_id=native_id, token=token, host=host, name=name
            )
            if identity.encode() != value:
                raise ValueError
            return identity
        except (ValueError, TypeError, UnicodeError) as exc:
            raise ComputeError(
                "expected a cluster ID returned by lc compute launch (clu_…)"
            ) from exc


class LaunchPlan(ComputeModel):
    """Resolved immutable sizing and nonsecret provider launch parameters."""

    connection: Connection
    offer: Offer
    request: Request
    seconds: PositiveInt
    details: dict[str, Any] = Field(default_factory=dict)
    name: str | None = None

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
            "name": self.name,
            "request": self.request.as_dict(),
            "offer": self.offer.name,
            "connection": self.offer.connection,
            "num_nodes": self.num_nodes,
            "resources": self.resources.as_dict(),
            "time_seconds": self.seconds,
            "startup": self.offer.startup.class_,
            "launch": self.details,
        }


class Snapshot(ComputeModel):
    """A fresh allocation observation; unknown state is never treated as absence."""

    model_config = ConfigDict(frozen=False, validate_assignment=True)
    __hash__ = None  # type: ignore[assignment]

    identity: Identity
    phase: str
    resources: Resources | None = None
    num_nodes: PositiveInt | None = None
    evidence: str = "unknown"
    reason: str = ""
    native_state: str = ""
    observation: str = "unverified"
    ready: bool | None = None
    workers: Annotated[int, Field(ge=0)] | None = None

    def as_dict(self) -> dict[str, Any]:
        """Keep endpoint credentials and native response objects private."""
        return {
            "schema_version": 1,
            "id": self.identity.encode(),
            "name": self.identity.name,
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
