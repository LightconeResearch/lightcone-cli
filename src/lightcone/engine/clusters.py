"""Read and attach to the sidebar's clusters; their lifecycle belongs to the sidebar.

The registry is the interface between the two packages. Backend state, rather
than a stale scheduler file, decides which records can serve a run.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import os
import platform
import re
import socket
import subprocess
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import dask.config
import psutil  # type: ignore[import-untyped]

from . import project
from .project import ProjectError

FORMAT = "lightcone.cluster/1"
_ID = re.compile(r"[0-9]{8}-[0-9]{6}-[a-z0-9]{4}")
_WAIT = 120
_CONNECT_TIMEOUT = 30
_COMPUTE = "Lightcone sidebar › Compute"


@dataclass(frozen=True)
class Record:
    """A validated registry record and its last observed backend state."""

    directory: Path
    data: dict[str, Any]
    state: str = "unknown"
    start_estimate: str | None = None

    @property
    def id(self) -> str:
        return str(self.data["id"])

    @property
    def backend(self) -> str:
        return str(self.data["backend"])

    @property
    def label(self) -> str:
        return str(self.data["label"])

    def section(self, name: str) -> dict[str, Any]:
        """Return a validated section of the record."""
        section = self.data.get(name)
        return section if isinstance(section, dict) else {}


def registry_root() -> Path:
    """The per-user registry, shared with the JupyterLab extension."""
    return Path.home() / ".lightcone" / "clusters"


def _records() -> Iterator[Record]:
    try:
        directories = sorted(registry_root().iterdir())
    except OSError:
        return
    for directory in directories:
        if not _ID.fullmatch(directory.name):
            continue
        try:
            data = json.loads((directory / "cluster.json").read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            continue
        backend = data.get("backend")
        if (
            data.get("id") != directory.name
            or backend not in ("local", "slurm", "gateway")
            or not isinstance(data.get("label"), str)
            or not isinstance(data.get(backend), dict)
            or not isinstance(data.get("workers"), dict)
        ):
            continue
        record = Record(directory, data)
        if not all(
            isinstance(data["workers"].get(key), str)
            for key in ("lightcone", "distributed", "python")
        ):
            continue
        section = record.section(backend)
        if backend == "slurm" and not isinstance(section.get("job"), str):
            continue
        if backend == "gateway":
            if not all(
                isinstance(section.get(key), str) and section[key] for key in ("name", "address")
            ):
                continue
        else:
            tls = record.section("tls")
            if not all(
                isinstance(tls.get(key), str)
                and tls[key]
                and not Path(tls[key]).is_absolute()
                and ".." not in Path(tls[key]).parts
                for key in ("ca", "cert", "key")
            ):
                continue
        yield record


def _local_process(record: Record, key: str) -> bool:
    """Reject reused PIDs, including records predating creation timestamps."""
    local = record.section("local")
    pid = local.get(key)
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        process = psutil.Process(pid)
        if process.status() == psutil.STATUS_ZOMBIE or not process.is_running():
            return False
        started = local.get(f"{key}_started")
        if started is not None:
            if process.create_time() != started:
                return False
        else:
            command = process.cmdline()
            module = "dask_scheduler" if key == "pid" else "dask_worker"
            if command[1:3] != ["-m", f"distributed.cli.{module}"]:
                return False
            try:
                path = command[command.index("--scheduler-file") + 1]
            except (ValueError, IndexError):
                return False
            if Path(path) != record.directory / "scheduler.json":
                return False
        return Path(process.cwd()) == record.directory.resolve()
    except psutil.NoSuchProcess:
        return False


def _scheduler_address(record: Record) -> str | None:
    try:
        data = json.loads((record.directory / "scheduler.json").read_text())
    except (OSError, ValueError):
        return None
    address = data.get("address") if isinstance(data, dict) else None
    return address if isinstance(address, str) and address else None


def _slurm_states() -> dict[str, tuple[str, str | None]]:
    """One queue snapshot for all the user's Slurm records."""
    result = subprocess.run(
        ["squeue", "--me", "--noheader", "--format=%i|%T|%S|%L"],
        capture_output=True,
        text=True,
        check=True,
        timeout=_CONNECT_TIMEOUT,
    )
    states = {}
    for line in result.stdout.splitlines():
        fields = line.strip().split("|")
        if len(fields) == 4:
            job, state, start, _remaining = fields
            states[job] = (state, None if start in ("N/A", "Unknown", "") else start)
    return states


def _gateway_address() -> str | None:
    address = dask.config.get("gateway.address", None)
    if not isinstance(address, str) or not address:
        return None
    try:
        return address.format(**os.environ).rstrip("/") or None
    except (KeyError, ValueError):
        return None


def _gateway_type() -> Any:
    try:
        from dask_gateway import Gateway
    except ImportError as error:
        raise ProjectError(
            "This cluster needs dask-gateway. Install lightcone-cli with its gateway extra: "
            "uv tool install 'lightcone-cli[gateway]'."
        ) from error
    return Gateway


def _gateway_states() -> dict[str, str]:
    gateway_type = _gateway_type()

    async def query() -> dict[str, str]:
        async with gateway_type(asynchronous=True) as gateway:
            reports = await asyncio.wait_for(
                gateway.list_clusters(status=["pending", "running", "stopping"]),
                timeout=_CONNECT_TIMEOUT,
            )
            return {report.name: report.status.name for report in reports}

    return asyncio.run(query())


def _gateway_connection(record: Record) -> tuple[str, Any]:
    """Fetch native credentials without creating a cluster lifecycle handle.

    GatewayCluster.get_client() cannot accept a connection timeout. Its address
    and security come from the same public report, and suffice for a Client.
    """
    gateway_type = _gateway_type()

    async def connect() -> tuple[str, Any]:
        async with gateway_type(asynchronous=True) as gateway:
            report = await asyncio.wait_for(
                gateway.get_cluster(record.section("gateway")["name"]), timeout=_CONNECT_TIMEOUT
            )
            if report.status.name != "RUNNING":
                raise ProjectError(
                    f"Cluster {record.label} is {report.status.name.lower()}. "
                    f"Check it in {_COMPUTE}."
                )
            return report.scheduler_address, report.security

    return asyncio.run(connect())


def _states(records: list[Record]) -> list[Record]:
    """Unknown backends are not candidates; discovery never mutates the registry."""
    slurm: dict[str, tuple[str, str | None]] | None = None
    gateway: dict[str, str] | None = None
    if any(record.backend == "slurm" for record in records):
        try:
            slurm = _slurm_states()
        except (OSError, subprocess.SubprocessError):
            pass
    if any(record.backend == "gateway" for record in records):
        try:
            gateway = _gateway_states()
        except ProjectError:
            raise
        except Exception:
            pass
    observed = []
    for record in records:
        state, estimate = "unknown", None
        if record.backend == "local":
            try:
                if _local_process(record, "pid"):
                    state = "running" if _scheduler_address(record) else "starting"
                else:
                    state = "stopping" if _local_process(record, "worker") else "gone"
            except psutil.AccessDenied:
                pass
        elif record.backend == "slurm" and slurm is not None:
            status, estimate = slurm.get(record.section("slurm")["job"], ("GONE", None))
            if status == "PENDING":
                state = "queued"
            elif status in ("RUNNING", "CONFIGURING"):
                state = "running" if _scheduler_address(record) else "starting"
            else:
                state = "gone" if status == "GONE" else "stopping"
        elif record.backend == "gateway" and gateway is not None:
            status = gateway.get(record.section("gateway")["name"], "GONE")
            state = {"PENDING": "starting", "RUNNING": "running", "GONE": "gone"}.get(
                status, "stopping"
            )
        observed.append(replace(record, state=state, start_estimate=estimate))
    return observed


def attached_cluster(root: Path) -> Record | None:
    """Select the one compatible live cluster, refusing ambiguous choices."""
    records = []
    for record in _records():
        image = record.section("workers").get("image")
        if record.backend == "gateway":
            server_image = os.environ.get("JUPYTER_IMAGE_SPEC") or os.environ.get("JUPYTER_IMAGE")
            if (
                not server_image
                or image != server_image
                or record.section("gateway").get("address") != _gateway_address()
                or project.mode(root) != "direct"
            ):
                continue
        elif image is not None:
            continue
        elif (
            record.backend == "local"
            and record.section("local").get("host") != socket.gethostname()
        ):
            continue
        records.append(record)
    candidates = [
        record for record in _states(records) if record.state in ("queued", "starting", "running")
    ]
    if len(candidates) > 1:
        names = ", ".join(f"{record.label} ({record.id})" for record in candidates)
        raise ProjectError(
            f"Several clusters could run this project: {names}. Stop all but one in {_COMPUTE}."
        )
    return candidates[0] if candidates else None


def _probe(root: str) -> dict[str, str | bool]:
    """Run on each worker before any recipe is submitted."""
    versions: dict[str, str | bool] = {"python": platform.python_version()}
    for name, distribution in (("lightcone", "lightcone-cli"), ("distributed", "distributed")):
        try:
            versions[name] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not installed"
    versions["project"] = Path(root).is_dir() and os.access(root, os.R_OK | os.X_OK)
    return versions


def _verify(client: Any, record: Record, root: Path) -> None:
    expected = _probe(str(root))
    reports = client.run(_probe, str(root))
    if not reports:
        raise ProjectError(f"Cluster {record.label} has no workers. Check it in {_COMPUTE}.")
    for address, actual in reports.items():
        for key in ("lightcone", "distributed", "python"):
            if actual[key] != expected[key]:
                raise ProjectError(
                    f"Cluster {record.label}, worker {address}, runs {key} {actual[key]}; "
                    f"this process runs {expected[key]}. Replace the cluster in {_COMPUTE}."
                )
        if not actual["project"]:
            raise ProjectError(
                f"Cluster {record.label}, worker {address}, cannot read project {root}. "
                "Move the project to a filesystem the workers see, such as $SCRATCH or home."
            )


@contextmanager
def client(record: Record, root: Path) -> Iterator[Any]:
    """Attach, verify and close only this run's client, never shared workers."""
    from distributed import Client, Security

    expected = _probe(str(root))
    for key in ("lightcone", "distributed", "python"):
        actual = record.section("workers").get(key)
        if actual != expected[key]:
            raise ProjectError(
                f"Cluster {record.label} records {key} {actual}; "
                f"this process runs {expected[key]}. "
                f"Replace the cluster in {_COMPUTE}."
            )
    deadline = time.monotonic() + _WAIT
    while record.state != "running":
        if record.state == "queued":
            estimate = (
                f" (estimated start {record.start_estimate})" if record.start_estimate else ""
            )
            raise ProjectError(
                f"Cluster {record.label} is queued{estimate}. Run again once it starts."
            )
        if record.state != "starting":
            raise ProjectError(f"Cluster {record.label} is {record.state}. Check it in {_COMPUTE}.")
        if time.monotonic() >= deadline:
            raise ProjectError(
                f"Cluster {record.label} did not start in {_WAIT} seconds. "
                f"Check its logs in {record.directory} and {_COMPUTE}."
            )
        time.sleep(0.2)
        record = _states([record])[0]

    with ExitStack() as stack:
        try:
            if record.backend == "gateway":
                address, security = _gateway_connection(record)
            else:
                # Read the address ourselves: Client(scheduler_file=...) can wait
                # forever if the scheduler removes the file between check and open.
                scheduler_address = _scheduler_address(record)
                if not scheduler_address:
                    raise OSError("the scheduler file is missing or incomplete")
                address = scheduler_address
                tls = record.section("tls")
                security = Security(  # type: ignore[no-untyped-call]
                    tls_ca_file=str(record.directory / tls["ca"]),
                    tls_client_cert=str(record.directory / tls["cert"]),
                    tls_client_key=str(record.directory / tls["key"]),
                    require_encryption=True,
                )
            connected = Client(  # type: ignore[no-untyped-call]
                address, security=security, timeout=_CONNECT_TIMEOUT, set_as_default=False
            )
            stack.callback(connected.close)
            connected.wait_for_workers(1, timeout=_WAIT)
            _verify(connected, record, root)
        except ProjectError:
            raise
        except Exception as error:
            raise ProjectError(
                f"Cluster {record.label} could not be reached or verified: {error}. "
                f"Check or stop it in {_COMPUTE}."
            ) from error
        yield connected
