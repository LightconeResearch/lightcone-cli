"""Site placement checks for local allocation and actual execution workers."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass

from lightcone.engine.project import ProjectError


@dataclass(frozen=True)
class _Site:
    """An HPC center whose login nodes must not host execution workers."""

    name: str
    marker: str


_SITES = (_Site(name="NERSC", marker="NERSC_HOST"),)


def require_compute_node(command: str = "cluster execution") -> None:
    """Refuse worker execution on a recognized site's non-compute host.

    A submission shell can inherit a job ID while remaining on a login node.
    Require the current host to match the node named by the Slurm daemon too.

    Args:
        command: The operation to name in a placement refusal.

    Raises:
        ProjectError: If a recognized site's host lacks a matching compute-node
            context. Workstations outside known sites are permitted.
    """
    site = next((site for site in _SITES if site.marker in os.environ), None)
    if site is None:
        return
    node = os.environ.get("SLURMD_NODENAME", "").split(".", 1)[0]
    host = socket.gethostname().split(".", 1)[0]
    if os.environ.get("SLURM_JOB_ID") and node and node == host:
        return
    raise ProjectError(
        f"{command} cannot execute on a {site.name} login node or an unverified "
        f"compute host ({site.marker} is set). A SLURM_JOB_ID alone does not "
        "prove compute-node placement.\n"
        "Use `lc compute resources` and `lc compute launch` to select a compute "
        "allocation, then pass its cluster ID to `lc run` or `lc materialize`.\n"
        "Project inspection with `lc materialize --check` and `lc status` "
        "does not require a compute node."
    )
