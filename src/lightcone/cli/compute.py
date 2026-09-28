"""CLI rendering for explicit resource allocation and cluster lifecycle."""

from __future__ import annotations

import json
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
                    "schema_version": 1,
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

    table = Table(*headers, box=None, padding=(0, 2))
    for row in rows:
        table.add_row(*row)
    Console(markup=False).print(table)


@click.group()
def compute() -> None:
    """Allocate resources, inspect clusters, and end allocations.

    The catalog is LC_COMPUTE_CONFIG, else ~/.lightcone/compute.yaml, else a
    built-in local offer.
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
            ["OFFER", "CPUS", "MEMORY", "MAX NODES", "DEFAULT", "MAX TIME", "STARTUP"],
            [
                [
                    offer["name"],
                    str(offer["resources"]["cpus"]),
                    f"{offer['resources']['memory']:g} GiB",
                    str(offer["max_nodes"]),
                    f"{offer['time']['default_seconds'] // 60}m",
                    f"{offer['time']['max_seconds'] // 60}m",
                    offer["startup"],
                ]
                for offer in data["offers"]
            ],
        )


@compute.command()
@click.option("--name", help="Cluster name; defaults to a generated short name.")
@click.option("--cpus", required=True, help="Logical CPUs per node; suffix + requests a minimum.")
@click.option("--memory", required=True, help="GiB per node; suffix + requests a minimum.")
@click.option("--num-nodes", default=1, type=click.IntRange(min=1), show_default=True)
@click.option(
    "--time", "walltime", help="Requested walltime, e.g. 30m or 2h; defaults to the offer."
)
@click.option(
    "--startup", type=click.Choice(["fast"]), help="Require a fast startup service class."
)
@click.option("--dry-run", is_flag=True, help="Resolve the launch without allocating compute.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured output.")
def launch(
    name: str | None,
    cpus: str,
    memory: str,
    num_nodes: int,
    walltime: str | None,
    startup: str | None,
    dry_run: bool,
    as_json: bool,
) -> None:
    """Create one new cluster from a provider-independent resource request."""
    from lightcone.engine.compute import Compute
    from lightcone.engine.compute.model import Request

    with _errors(as_json):
        service = Compute()
        plan = service.plan(
            Request.parse(
                cpus,
                memory,
                num_nodes=num_nodes,
                time=walltime,
                startup=startup,
            ),
            name=name,
        )
        data: dict[str, Any] = {"schema_version": 1, "plan": plan.as_dict()}
        if dry_run:
            if as_json:
                click.echo(json.dumps(data))
            else:
                click.echo(json.dumps(plan.as_dict(), indent=2))
            return
        identity = service.launch(plan)
        data.update(id=identity.encode(), name=identity.name, accepted=True)
        if as_json:
            click.echo(json.dumps(data))
        else:
            click.echo(identity.name)
            click.echo(
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
                        "schema_version": 1,
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
                    "schema_version": 1,
                    "id": identity.encode(),
                    "name": identity.name,
                    "termination_requested": True,
                })
            )
        else:
            click.echo(f"Termination requested: {identity.name}")
