"""One stock Dask worker per Slurm rank, with a scheduler alongside rank zero."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import socket
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import UUID

from lightcone.engine.compute.model import ComputeError, Connection, Identity
from lightcone.engine.compute.runtime import (
    configured_directory,
    create_security,
    load_security,
    private_directory,
    read_private_json,
    write_private_json,
)
from lightcone.engine.compute.slurm import attempt_directory

_STARTUP_TIMEOUT = 120.0


def _allocation(args: argparse.Namespace) -> tuple[Identity, int, int]:
    """Reject missing or conflicting native placement before creating any endpoint."""
    if not re.fullmatch(r"[0-9a-f]{32}", args.submission):
        raise ComputeError("invalid submission token")
    if str(UUID(args.namespace)) != args.namespace:
        raise ComputeError("invalid connection namespace")
    native_id = os.environ.get("SLURM_JOB_ID", "")
    if not re.fullmatch(r"[0-9]+", native_id):
        raise ComputeError("Dask bootstrap requires a native Slurm allocation")
    values: dict[str, int] = {}
    for key in ("SLURM_PROCID", "SLURM_NTASKS", "SLURM_JOB_NUM_NODES", "SLURM_CPUS_PER_TASK"):
        raw = os.environ.get(key, "")
        if not raw.isdigit():
            raise ComputeError(f"Dask bootstrap requires native {key}")
        values[key] = int(raw)
    if (
        args.num_nodes < 1
        or args.cpus < 1
        or args.memory_bytes < 1
        or args.task_slots < 1
        or values["SLURM_NTASKS"] != args.num_nodes
        or values["SLURM_JOB_NUM_NODES"] != args.num_nodes
        or not 0 <= values["SLURM_PROCID"] < args.num_nodes
        or values["SLURM_CPUS_PER_TASK"] < args.cpus
        or args.task_slots > args.cpus
    ):
        raise ComputeError("native Slurm placement does not match the frozen allocation envelope")
    if hasattr(os, "sched_getaffinity") and len(os.sched_getaffinity(0)) < args.cpus:
        raise ComputeError("native CPU affinity is smaller than the requested allocation envelope")
    memory = os.environ.get("SLURM_MEM_PER_NODE", "")
    if not memory.isdigit() or int(memory) * 1024**2 < args.memory_bytes:
        raise ComputeError("native per-node memory does not match the allocation envelope")
    restarts = os.environ.get("SLURM_RESTART_COUNT", "0")
    if not restarts.isdigit():
        raise ComputeError("invalid native Slurm restart count")
    return (
        Identity(namespace=args.namespace, native_id=native_id, token=args.submission),
        int(restarts),
        values["SLURM_PROCID"],
    )


async def run(args: argparse.Namespace) -> None:
    """Run standard asynchronous Scheduler/Worker contexts for this native rank."""
    from distributed import Scheduler, Worker

    identity, restarts, rank = _allocation(args)
    connection = Connection(
        namespace=identity.namespace, provider="slurm",
        launch={"connection_root": args.connection_root},
    )
    directory = attempt_directory(connection, identity, restarts)
    scratch = configured_directory(Path(args.scratch_root or tempfile.gettempdir()))
    scratch = private_directory(
        scratch / identity.token / f"attempt-{restarts}" / str(rank), create=True
    )
    identity_values: dict[str, Any] = {
        "namespace": identity.namespace,
        "native_id": identity.native_id,
        "token": identity.token,
        "uid": os.getuid(),
        "restarts": restarts,
        "num_nodes": args.num_nodes,
        "cpus": args.cpus,
        "memory_bytes": args.memory_bytes,
        "task_slots": args.task_slots,
    }
    address = {"interface": args.interface} if args.interface else {"host": socket.gethostname()}
    worker_options = {
        **address,
        "nthreads": args.task_slots,
        "memory_limit": 0,
        "local_directory": str(scratch),
        "dashboard_address": "127.0.0.1:0",
        "dashboard": False,
        "death_timeout": _STARTUP_TIMEOUT,
        "name": f"lightcone-{rank}",
        "protocol": "tls",
    }
    if rank == 0:
        directory = private_directory(directory, create=True)
        security = create_security(directory)
        scheduler = Scheduler(
            **address,
            protocol="tls",
            port=0,
            dashboard=False,
            dashboard_address="127.0.0.1:0",
            security=security,
            scheduler_file=str(directory / "scheduler.json"),
        )
        try:
            await asyncio.wait_for(scheduler, timeout=_STARTUP_TIMEOUT)
            write_private_json(
                directory / "identity.json", {**identity_values, "scheduler_id": scheduler.id}
            )
            async with Worker(scheduler.address, security=security, **worker_options):
                await scheduler.finished()  # type: ignore[no-untyped-call]
        finally:
            await scheduler.close()
        return
    deadline = time.monotonic() + _STARTUP_TIMEOUT
    while True:
        try:
            metadata = read_private_json(directory / "identity.json")
        except ComputeError:
            if time.monotonic() >= deadline:
                raise ComputeError("timed out waiting for this allocation's scheduler") from None
            await asyncio.sleep(0.2)
            continue
        if any(
            type(metadata.get(key)) is not type(value) or metadata.get(key) != value
            for key, value in identity_values.items()
        ):
            raise ComputeError("scheduler rendezvous belongs to another allocation attempt")
        break
    security = load_security(directory)
    async with Worker(
        scheduler_file=str(directory / "scheduler.json"), security=security, **worker_options
    ) as worker:
        await worker.finished()


def main() -> None:
    """Read frozen launcher arguments; a failure becomes a nonzero native task exit."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("submission", "namespace", "connection-root"):
        parser.add_argument(f"--{name}", required=True)
    for name in ("num-nodes", "cpus", "memory-bytes", "task-slots"):
        parser.add_argument(f"--{name}", required=True, type=int)
    parser.add_argument("--scratch-root")
    parser.add_argument("--interface")
    args = parser.parse_args()
    os.umask(0o077)
    import dask

    try:
        with dask.config.set(
            {
                "distributed.scheduler.http.routes": [],
                "distributed.worker.http.routes": [],
            }
        ):
            asyncio.run(run(args))
    except (ComputeError, ValueError) as exc:
        logging.error("Slurm Dask startup failed: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
