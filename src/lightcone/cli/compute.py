"""CLI rendering for explicit resource allocation and cluster lifecycle."""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import click


@contextmanager
def _errors(as_json: bool) -> Iterator[None]:
    from lightcone.engine.compute.model import ComputeError

    try:
        yield
    except ComputeError as exc:
        if not as_json:
            detail = str(exc)
            if exc.cluster_id:
                detail += f"\nCluster: {exc.cluster_id}"
            if exc.submission_token:
                detail += (
                    f"\nSubmission token: {exc.submission_token}; inspect status before retrying"
                )
            raise click.ClickException(detail) from exc
        click.echo(
            json.dumps(
                {
                    "error": str(exc),
                    "id": exc.cluster_id,
                    "submission_token": exc.submission_token,
                }
            )
        )
        raise click.exceptions.Exit(1) from exc


def _table(headers: list[str], rows: list[list[str]]) -> None:
    from rich.console import Console
    from rich.table import Table

    table = Table(*headers, box=None, padding=(0, 1))
    for row in rows:
        table.add_row(*row)
    Console(markup=False).print(table)


def _duration(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    minutes, remainder = divmod(seconds, 60)
    return (f"{minutes}m" if minutes else "") + (f"{remainder}s" if remainder else "")


@click.group()
def compute() -> None:
    """Allocate resources, inspect clusters, and end allocations.

    Read LC_COMPUTE_CONFIG or ~/.lightcone/compute.yaml. A built-in local
    offer follows configured offers unless local compute is disabled or
    the catalog supplies its own local offers. It offers this host's CPUs and
    memory, plus the GPUs CUDA_VISIBLE_DEVICES lists; by default it ends
    after 30 minutes without task activity rather than at a fixed age. Local
    compute is automatically disabled on recognized NERSC login nodes.
    """


@compute.command()
@click.option("--json", "as_json", is_flag=True, help="Emit structured output.")
def resources(as_json: bool) -> None:
    """Show available resource offers in preference order."""
    from lightcone.engine.compute import Compute

    with _errors(as_json):
        data = Compute().resources()
        if as_json:
            click.echo(json.dumps(data))
            return
        _table(
            [
                "OFFER", "CPUS", "MEMORY", "GPUS", "MAX NODES",
                "DEFAULT", "MAX TIME", "IDLE", "STARTUP",
            ],
            [
                [
                    offer["name"],
                    str(offer["resources"]["cpus"]),
                    f"{offer['resources']['memory']:g} GiB",
                    ", ".join(
                        f"{name}:{count}"
                        for name, count in (offer["resources"]["accelerators"] or {}).items()
                    ) or "-",
                    str(offer["max_nodes"]),
                    _duration(offer["time"]["default_seconds"]),
                    _duration(offer["time"]["max_seconds"]),
                    _duration(offer["time"]["idle_seconds"]),
                    offer["startup"],
                ]
                for offer in data["offers"]
            ],
        )


@compute.command()
@click.option("--name", help="Cluster name; defaults to local for the shortcut, else a short name.")
@click.option("--cpus", help="Logical CPUs per node; suffix + requests a minimum.")
@click.option("--memory",
              help="Memory per node, e.g. 16 or 16GB; suffix + requests a minimum.")
@click.option("--gpus",
              help="Accelerator NAME[:COUNT] per node, e.g. A100:4 or GPU:1; 0 requests CPU "
                   "only, the default except for local compute, which takes the offer's GPUs.")
@click.option("--num-nodes", default=1, type=click.IntRange(min=1), show_default=True)
@click.option(
    "--time", "walltime",
    help="Hard walltime, e.g. 30m or 1h30m, even during active work; defaults to the offer's.",
)
@click.option(
    "--startup", type=click.Choice(["fast"]), help="Require a fast startup service class."
)
@click.option("--dry-run", is_flag=True, help="Resolve the launch without allocating compute.")
@click.option("--wait", is_flag=True, help="Wait until the new cluster is ready for execution.")
@click.option("--timeout", type=click.FloatRange(min=0, min_open=True),
              help="Readiness deadline in seconds (default: 300); requires --wait.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured output.")
def launch(
    name: str | None,
    cpus: str | None,
    memory: str | None,
    gpus: str | None,
    num_nodes: int,
    walltime: str | None,
    startup: str | None,
    dry_run: bool,
    wait: bool,
    timeout: float | None,
    as_json: bool,
) -> None:
    """Create a cluster; omit CPU and memory to use the default local offer."""
    from lightcone.engine.compute import Compute
    from lightcone.engine.compute.model import ComputeError, Request

    with _errors(as_json):
        if timeout is not None and not wait:
            raise ComputeError("--timeout requires --wait")
        if timeout is not None and not math.isfinite(timeout):
            raise ComputeError("timeout must be finite and positive")
        if dry_run and wait:
            raise ComputeError("--wait cannot be combined with --dry-run")
        if (cpus is None) != (memory is None):
            raise ComputeError("supply both --cpus and --memory, or omit both for local compute")
        service = Compute()
        if cpus is None or memory is None:
            plan = service.plan_local(
                name=name, time=walltime, gpus=gpus, num_nodes=num_nodes, startup=startup,
            )
        else:
            plan = service.plan(
                Request.parse(
                    cpus, memory, gpus="0" if gpus is None else gpus, num_nodes=num_nodes,
                    time=walltime, startup=startup,
                ),
                name=name,
            )
        data: dict[str, Any] = {"plan": plan.as_dict()}
        if dry_run:
            if as_json:
                click.echo(json.dumps(data))
            else:
                click.echo(json.dumps(plan.as_dict(), indent=2))
            return
        identity = service.launch(plan)
        data.update(id=identity.encode(), name=identity.name, accepted=True)
        if wait:
            if not as_json:
                click.echo(
                    f"Allocation accepted: {identity.name}. Waiting for readiness.", err=True,
                )
            try:
                snapshot = service.status(identity.encode(), wait=True, timeout=timeout or 300)
                if not snapshot.ready:
                    raise ComputeError(
                        f"cluster {identity.name} is {snapshot.phase}: {snapshot.reason}",
                    )
            except ComputeError as exc:
                raise ComputeError(str(exc), cluster_id=identity.encode()) from exc
            data["ready"] = True
        if as_json:
            click.echo(json.dumps(data))
        else:
            click.echo(identity.name)
            click.echo(
                f"Cluster {identity.name} is ready." if wait else
                f"Allocation accepted. Use lc compute status {identity.name} --wait for readiness.",
                err=True,
            )


@compute.command()
@click.argument("cluster_id", metavar="[CLUSTER]", required=False)
@click.option("--wait", is_flag=True, help="Wait for the selected cluster to become ready.")
@click.option(
    "--timeout",
    type=click.FloatRange(min=0, min_open=True),
    help="Readiness deadline in seconds (default: 300); requires --wait.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured output.")
def status(
    cluster_id: str | None,
    wait: bool,
    timeout: float | None,
    as_json: bool,
) -> None:
    """Inspect a cluster by name or ID, or discover allocations from each authority."""
    from lightcone.engine.compute import Compute
    from lightcone.engine.compute.model import ComputeError

    with _errors(as_json):
        if wait and cluster_id is None:
            raise ComputeError("--wait requires a cluster name or ID")
        if timeout is not None and not wait:
            raise ComputeError("--timeout requires --wait")
        service = Compute()
        if cluster_id is not None:
            snapshot = service.status(cluster_id, wait=wait, timeout=timeout or 300)
            data = snapshot.as_dict()
            if as_json:
                click.echo(json.dumps(data))
            else:
                click.echo(
                    f"{snapshot.identity.name}\nAllocation: {snapshot.phase}; "
                    f"Dask: {snapshot.observation}; ready: {snapshot.ready}"
                )
                if snapshot.resources:
                    click.echo(
                        f"{snapshot.num_nodes} node(s), {snapshot.resources.cpus} CPUs and "
                        f"{snapshot.resources.memory_gib:g} GiB per node "
                        f"({snapshot.evidence})"
                    )
                if snapshot.reason:
                    click.echo(snapshot.reason)
            if wait and not snapshot.ready:
                raise click.exceptions.Exit(1)
            return
        snapshots, errors = service.discover()
        if as_json:
            click.echo(
                json.dumps(
                    {
                        "clusters": [item.as_dict() for item in snapshots],
                        "errors": errors,
                    }
                )
            )
        else:
            for item in snapshots:
                click.echo(f"{item.identity.name}: {item.phase}")
            if not snapshots and not errors:
                click.echo("No allocations found.")
            for name, error in errors.items():
                click.echo(f"{name}: {error}", err=True)
        if errors:
            raise click.exceptions.Exit(1)


@compute.command()
@click.argument("cluster_id", metavar="CLUSTER")
@click.option("--json", "as_json", is_flag=True, help="Emit structured output.")
def down(cluster_id: str, as_json: bool) -> None:
    """End a cluster by name or ID; scheduler reachability is not required."""
    from lightcone.engine.compute import Compute

    with _errors(as_json):
        identity = Compute().down(cluster_id)
        if as_json:
            click.echo(
                json.dumps({
                    "id": identity.encode(),
                    "name": identity.name,
                    "termination_requested": True,
                })
            )
        else:
            click.echo(f"Termination requested: {identity.name}")
