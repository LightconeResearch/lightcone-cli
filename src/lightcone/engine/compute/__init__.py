"""Resource allocation and borrowed Dask clients through native providers."""

from __future__ import annotations

import math
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from .catalog import Catalog, local_disabled_reason
from .model import (
    PROVIDERS,
    ComputeError,
    Identity,
    LaunchPlan,
    Offer,
    Provider,
    Request,
    Snapshot,
    UnavailableOfferError,
    validate_name,
)

#: What a driver leaving early must say: closing a client cannot prove that a
#: remote subprocess has stopped.
UNSTOPPED = (
    "lc did not stop the allocation; tasks that did not report may still be "
    "running, and any files they wrote remain"
)


def validate_id(value: str) -> None:
    """Reject invalid execution targets before preparing a project."""
    if value.startswith("clu_"):
        Identity.decode(value)
    else:
        validate_name(value)


class Compute:
    """One command's view of configuration and fresh native observations."""

    def __init__(self) -> None:
        self.catalog = Catalog.load()
        self._providers: dict[str, Provider] = {}

    def provider(self, name: str) -> Provider:
        """Construct the adapter for one native authority, once per command."""
        if name not in self._providers:
            self._providers[name] = PROVIDERS[name](Path(self.catalog.connection_root))
        return self._providers[name]

    def resolve(self, cluster_id: str) -> tuple[Provider, Identity]:
        """Route an immutable ID, or resolve one unambiguous name from native state."""
        if cluster_id.startswith("clu_"):
            identity = Identity.decode(cluster_id)
        else:
            validate_name(cluster_id)
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
        return self.provider(identity.provider), identity

    def resources(self) -> dict[str, Any]:
        """Describe configured policy, without inventing live free capacity."""
        return {
            "units": {
                "cpus": "logical CPUs per node", "memory": "GiB per node",
                "accelerators": "type and count per node",
            },
            "offers": [
                {
                    "name": offer.name,
                    "resources": offer.resources.as_dict(),
                    "max_nodes": offer.max_nodes,
                    "time": {
                        "default_seconds": offer.time.default_seconds,
                        "max_seconds": offer.time.max_seconds,
                        "idle_seconds": offer.time.idle_seconds,
                    },
                    "startup": offer.startup.class_,
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
            try:
                plan = self._plan_offer(offer, request, name)
                if plan is not None:
                    return plan
            except UnavailableOfferError as exc:
                unavailable.append(f"{offer.name}: {exc}")
        raise ComputeError(
            "no configured offer matches this resource request; see lc compute resources"
            + ("; " + "; ".join(unavailable) if unavailable else "")
        )

    def _plan_offer(
        self, offer: Offer, request: Request, name: str | None,
    ) -> LaunchPlan | None:
        """Match one shape and validate its provider without allocating anything."""
        if request.num_nodes > offer.max_nodes:
            return None
        if request.startup is not None and request.startup != offer.startup.class_:
            return None
        limit = offer.time.max_seconds
        if request.seconds is not None and limit is not None and request.seconds > limit:
            return None
        if (
            offer.resources.cpus < request.cpus
            if request.min_cpus else offer.resources.cpus != request.cpus
        ):
            return None
        if (
            offer.resources.memory_bytes < request.memory_bytes
            if request.min_memory else offer.resources.memory_bytes != request.memory_bytes
        ):
            return None
        if request.accelerators is None:
            matches = offer.resources.accelerators is None
        else:
            matches = request.accelerators.matches(offer.resources.accelerators)
        if not matches:
            return None
        return self.provider(offer.provider).plan(offer, request).replace(name=name)

    def plan_local(
        self, *, name: str | None = None, time: str | None = None,
        gpus: str | None = None, num_nodes: int = 1, startup: str | None = None,
    ) -> LaunchPlan:
        """Plan the first usable local offer, without considering remote backends.

        Without ``gpus``, each offer is taken whole, GPUs included; ``"0"`` takes
        it without them, since a local allocation never reserves its GPUs.
        """
        if reason := local_disabled_reason(self.catalog.allow_local):
            raise ComputeError(reason)
        name = "local" if name is None else name
        validate_name(name)
        unavailable: list[str] = []
        for offer in self.catalog.offers:
            if offer.provider != "local":
                continue
            if gpus == "0":
                offer = offer.replace(resources=offer.resources.replace(accelerators=None))
            request = Request.parse(
                str(offer.resources.cpus), f"{offer.resources.memory_bytes}B",
                gpus=gpus, num_nodes=num_nodes, time=time, startup=startup,
            )
            if gpus is None:
                request = request.replace(accelerators=offer.resources.accelerators)
            try:
                plan = self._plan_offer(offer, request, name)
                if plan is not None:
                    return plan
            except UnavailableOfferError as exc:
                unavailable.append(f"{offer.name}: {exc}")
        raise ComputeError(
            "no local offer matches this request; see lc compute resources for local shapes "
            "and time limits, or supply --cpus and --memory for a remote allocation"
            + ("; " + "; ".join(unavailable) if unavailable else "")
        )

    def launch(self, plan: LaunchPlan) -> Identity:
        """Choose an unused name from native observations, then submit exactly once."""
        if plan.offer.provider == "local" and (
            reason := local_disabled_reason(self.catalog.allow_local)
        ):
            raise ComputeError(reason)
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
        return self.provider(plan.offer.provider).launch(plan.replace(name=name))

    def discover(self) -> tuple[list[Snapshot], dict[str, str]]:
        """Query each native authority once, retaining partial discovery failures."""
        snapshots: list[Snapshot] = []
        errors: dict[str, str] = {}
        for name in self.catalog.providers:
            try:
                snapshots.extend(self.provider(name).discover())
            except ComputeError as exc:
                errors[name] = str(exc)
        return snapshots, errors

    def status(self, cluster_id: str, *, wait: bool = False, timeout: float = 300) -> Snapshot:
        """Observe native state and live Dask readiness within a finite deadline."""
        if not math.isfinite(timeout) or timeout <= 0:
            raise ComputeError("timeout must be finite and positive")
        provider, identity = self.resolve(cluster_id)
        deadline = time.monotonic() + timeout
        # Native schedulers ask users not to poll in a tight loop: back off
        # from one second, so a long queue wait costs a few dozen queries.
        delay = 1.0
        unreachable = ""
        while True:
            snapshot = provider.inspect(identity)
            remaining = deadline - time.monotonic()
            if snapshot.phase == "active" and remaining > 0:
                try:
                    with provider.connect(identity, timeout=min(10, remaining)) as client:
                        info = client.scheduler_info()
                    snapshot.workers = len(info["workers"])
                    snapshot.observation = "reachable"
                    snapshot.ready = (
                        snapshot.num_nodes is not None and snapshot.workers >= snapshot.num_nodes
                    )
                    unreachable = ""
                except ComputeError as exc:
                    snapshot.observation = "unreachable"
                    snapshot.ready = False
                    snapshot.reason = unreachable = str(exc)
                remaining = deadline - time.monotonic()
            if not wait or snapshot.ready or snapshot.phase in ("ended", "stopping"):
                return snapshot
            if remaining <= 0:
                raise ComputeError(
                    f"cluster did not become ready within {timeout:g}s; allocation is unchanged"
                    + (f"; last connection attempt: {unreachable}" if unreachable else ""),
                    cluster_id=identity.encode(),
                )
            time.sleep(min(delay, remaining))
            delay = min(2 * delay, 30.0)

    def down(self, cluster_id: str) -> Identity:
        """Ask the native provider to end the allocation, independently of Dask health."""
        provider, identity = self.resolve(cluster_id)
        provider.terminate(identity)
        return identity


@contextmanager
def connect(cluster_id: str, *, timeout: float = 10) -> Iterator[Any]:
    """Borrow a validated standard Dask client; detach without closing its cluster."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ComputeError("timeout must be finite and positive")
    service = Compute()
    provider, identity = service.resolve(cluster_id)
    if identity.provider == "local" and (
        reason := local_disabled_reason(service.catalog.allow_local)
    ):
        raise ComputeError(reason, cluster_id=identity.encode())
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
        # A caller prepares (fetch, build, sync) before its first task, and Dask's
        # idle test counts only tasks: a no-op task restarts the countdown for it.
        client.submit(int, pure=False)
        yield client
