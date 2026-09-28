"""Resource allocation and borrowed Dask clients through native providers."""

from __future__ import annotations

import math
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from .catalog import Catalog
from .model import (
    ComputeError,
    Connection,
    Identity,
    LaunchPlan,
    Provider,
    ProviderFactory,
    Request,
    Snapshot,
    UnavailableOfferError,
    validate_name,
)


def _local(connection: Connection) -> Provider:
    from .local import LocalProvider

    return LocalProvider(connection)


def _slurm(connection: Connection) -> Provider:
    from .slurm import SlurmProvider

    return SlurmProvider(connection)


# The lifecycle seam is intentionally small: execution never dispatches on a provider.
PROVIDERS: dict[str, ProviderFactory] = {"local": _local, "slurm": _slurm}


def validate_id(value: str) -> None:
    """Reject invalid execution targets before preparing a project."""
    if value.startswith("clu_"):
        Identity.decode(value)
    else:
        validate_name(value)


class Compute:
    """One command's view of configuration and fresh native observations."""

    def __init__(self, config_path: Path | None = None) -> None:
        self.catalog = Catalog.load(config_path)

    def provider(self, connection: Connection) -> Provider:
        """Construct an adapter for an explicitly configured native authority."""
        factory = PROVIDERS.get(connection.provider)
        if factory is None:
            raise ComputeError(f"unsupported compute provider: {connection.provider}")
        return factory(connection)

    def resolve(self, cluster_id: str) -> tuple[Provider, Identity]:
        """Route an immutable ID, or resolve one unambiguous name from native state."""
        validate_id(cluster_id)
        if cluster_id.startswith("clu_"):
            identity = Identity.decode(cluster_id)
        else:
            snapshots, errors = self.discover()
            if errors:
                detail = "; ".join(f"{name}: {error}" for name, error in errors.items())
                raise ComputeError(
                    f"cannot resolve cluster name while discovery is incomplete: {detail}; "
                    "use its full cluster ID to address a known allocation directly"
                )
            matches = {
                snapshot.identity for snapshot in snapshots
                if snapshot.identity.name == cluster_id and snapshot.phase != "ended"
            }
            if not matches:
                raise ComputeError(
                    f"no current cluster named {cluster_id!r}; see lc compute status "
                    "or create one with lc compute launch"
                )
            if len(matches) > 1:
                ids = ", ".join(sorted(item.encode() for item in matches))
                raise ComputeError(
                    f"cluster name {cluster_id!r} is ambiguous; use a full cluster ID: {ids}"
                )
            identity = matches.pop()
        return self.provider(self.catalog.connection_for(identity.namespace)), identity

    def resources(self) -> dict[str, Any]:
        """Describe configured policy, without inventing live free capacity."""
        return {
            "schema_version": 1,
            "units": {"cpus": "logical CPUs per node", "memory": "GiB per node"},
            "offers": [
                {
                    "name": offer.name,
                    "resources": offer.resources.as_dict(),
                    "max_nodes": offer.max_nodes,
                    "time": {
                        "default_seconds": offer.default_seconds,
                        "max_seconds": offer.max_seconds,
                    },
                    "startup": offer.startup,
                }
                for offer in self.catalog.offers
            ],
        }

    def plan(self, request: Request, *, name: str | None = None) -> LaunchPlan:
        """Select the first eligible fixed shape; a failed launch never retries elsewhere."""
        if name is not None:
            validate_name(name)
        unavailable: list[str] = []
        for offer in self.catalog.offers:
            if request.num_nodes > offer.max_nodes:
                continue
            if request.startup is not None and request.startup != offer.startup:
                continue
            if request.seconds is not None and request.seconds > offer.max_seconds:
                continue
            if (
                offer.resources.cpus < request.cpus
                if request.min_cpus
                else offer.resources.cpus != request.cpus
            ):
                continue
            if (
                offer.resources.memory < request.memory
                if request.min_memory
                else offer.resources.memory != request.memory
            ):
                continue
            try:
                return replace(
                    self.provider(self.catalog.connections[offer.connection]).plan(offer, request),
                    name=name,
                )
            except UnavailableOfferError as exc:
                unavailable.append(f"{offer.name}: {exc}")
        raise ComputeError(
            "no configured offer matches this resource request; see lc compute resources"
            + ("; " + "; ".join(unavailable) if unavailable else "")
        )

    def launch(self, plan: LaunchPlan) -> Identity:
        """Choose an unused name from native observations, then submit exactly once."""
        if plan.name is not None:
            validate_name(plan.name)
        snapshots, errors = self.discover()
        if errors:
            detail = "; ".join(f"{name}: {error}" for name, error in errors.items())
            raise ComputeError(
                f"cannot check cluster names while discovery is incomplete: {detail}"
            )
        names = {item.identity.name for item in snapshots if item.phase != "ended"}
        name = plan.name
        if name is None:
            for _ in range(10):
                name = f"lc-{uuid4().hex[:12]}"
                if name not in names:
                    break
            else:
                raise ComputeError("could not generate an unused cluster name; no allocation made")
        elif name in names:
            raise ComputeError(f"cluster name {name!r} is already in use; choose another name")
        return self.provider(plan.connection).launch(replace(plan, name=name))

    def discover(self) -> tuple[list[Snapshot], dict[str, str]]:
        """Query each native authority once, retaining partial discovery failures."""
        snapshots: list[Snapshot] = []
        errors: dict[str, str] = {}
        for connection in self.catalog.connections.values():
            try:
                snapshots.extend(self.provider(connection).discover())
            except ComputeError as exc:
                errors[connection.name] = str(exc)
        return snapshots, errors

    def status(self, cluster_id: str, *, wait: bool = False, timeout: float = 300) -> Snapshot:
        """Observe native state and live Dask readiness within a finite deadline."""
        if not math.isfinite(timeout) or timeout <= 0:
            raise ComputeError("timeout must be finite and positive")
        provider, identity = self.resolve(cluster_id)
        deadline = time.monotonic() + timeout
        while True:
            snapshot = provider.inspect(identity)
            if snapshot.phase == "active":
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    if not wait:
                        return snapshot
                    raise ComputeError(
                        f"cluster did not become ready within {timeout:g}s; "
                        "allocation is unchanged",
                        cluster_id=identity.encode(),
                    )
                try:
                    with provider.connect(identity, timeout=min(10, remaining)) as client:
                        info = client.scheduler_info()
                    snapshot.workers = len(info["workers"])
                    snapshot.observation = "reachable"
                    snapshot.ready = (
                        snapshot.num_nodes is not None and snapshot.workers >= snapshot.num_nodes
                    )
                except ComputeError as exc:
                    snapshot.observation = "unreachable"
                    snapshot.ready = False
                    snapshot.reason = str(exc)
            if not wait or snapshot.ready or snapshot.phase in ("ended", "stopping"):
                return snapshot
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ComputeError(
                    f"cluster did not become ready within {timeout:g}s; allocation is unchanged",
                    cluster_id=identity.encode(),
                )
            time.sleep(min(1, remaining))

    def down(self, cluster_id: str) -> Identity:
        """Ask the native provider to end the allocation, independently of Dask health."""
        provider, identity = self.resolve(cluster_id)
        provider.terminate(identity)
        return identity


@contextmanager
def connect(
    cluster_id: str,
    *,
    timeout: float = 10,
    config_path: Path | None = None,
) -> Iterator[Any]:
    """Borrow a validated standard Dask client; detach without closing its cluster."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ComputeError("timeout must be finite and positive")
    validate_id(cluster_id)
    provider, identity = Compute(config_path).resolve(cluster_id)
    snapshot = provider.inspect(identity)
    if snapshot.phase != "active":
        raise ComputeError(
            f"cluster is {snapshot.phase}: {snapshot.reason}", cluster_id=identity.encode()
        )
    with provider.connect(identity, timeout=timeout) as client:
        workers = client.scheduler_info().get("workers", {})
        if snapshot.num_nodes is None or len(workers) < snapshot.num_nodes:
            raise ComputeError(
                "cluster does not have its expected workers; inspect lc compute status",
                cluster_id=identity.encode(),
            )
        yield client
