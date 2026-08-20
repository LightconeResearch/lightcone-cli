"""Where a run executes: the venue a materialization finds itself on.

A venue is host state, never project state — nothing here reads the
project or enters any identity. The one venue beyond the local machine is
a SLURM allocation, detected rather than configured: the user already
declared every resource question to SLURM (`salloc -N4 …`), so the
allocation *is* the declaration, and lc's job is to span it — one Dask
worker per allocated node, launched with a single `srun`, all connected
to a scheduler living in the driver process.

Workers run the driver's own interpreter (`sys.executable -m`), which on
an HPC system is the lc tool environment on the shared filesystem — so
driver and workers are the identical installation, which is all a worker
process needs: `lightcone.engine` importable at the driver's version.
Workers need no git and no git-annex; the driver owns git alone.

If the driver dies uncleanly, workers exit on their own (death timeout)
and the allocation's walltime is the backstop; whatever the interrupted
run left behind meets the next run's dirty-tree refusal, which names the
`results/` paths to discard — that is the designed recovery, not a
watchdog.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from lightcone.engine.project import ProjectError

#: How long the allocation's workers get to connect before the run
#: refuses. Generous because the first import of `distributed` from a
#: cold parallel filesystem is seconds, not milliseconds.
_WORKER_WAIT = 120.0

#: Grace given to srun to end on its own once the workers are retired,
#: before the terminate/kill escalation.
_REAP_GRACE = 20.0


def require_compute_node() -> None:
    """Refuse to materialize on a NERSC login node.

    A login node is for editing and submitting, not computing — and every
    node of an allocation becomes a worker, so the remedy is to run the
    same command inside one. NERSC_HOST is set on compute nodes too; an
    active allocation (SLURM_JOB_ID) is what distinguishes them.

    Raises:
        ProjectError: On a NERSC login node, naming the salloc and sbatch
            commands to run instead.
    """
    if "NERSC_HOST" not in os.environ or "SLURM_JOB_ID" in os.environ:
        return
    raise ProjectError(
        "lc materialize executes on compute nodes, and this is a NERSC login "
        "node (NERSC_HOST is set with no SLURM allocation active).\n"
        "\n"
        "Get an allocation and run it there — every node becomes a worker:\n"
        "\n"
        "  interactive:\n"
        "      salloc --nodes=1 --constraint=cpu --qos=interactive --time=02:00:00\n"
        "      lc materialize\n"
        "\n"
        "  batch (from the project root):\n"
        "      sbatch --nodes=1 --constraint=cpu --qos=regular --time=02:00:00 \\\n"
        "          --wrap 'lc materialize'\n"
        "\n"
        "lc materialize --check, lc status and lc run work anywhere."
    )


@contextmanager
def slurm_client() -> Iterator[Any]:
    """Span the SLURM allocation this process is running inside.

    The scheduler lives here, in the driver, bound to this node's
    SLURM-canonical hostname so workers on the allocation's other nodes
    can reach it — the default loopback bind cannot be. One `srun`
    launches one worker per node; the driver's own node hosts a worker
    too, because the driver's footprint is small against a node and
    excluding it would waste one.

    Yields:
        A connected Dask client with every node's worker registered.

    Raises:
        ProjectError: If srun is missing, exits before the workers
            connect, or the workers do not all connect in time.
    """
    from distributed import Client, LocalCluster

    if shutil.which("srun") is None:
        raise ProjectError(
            "SLURM_JOB_ID is set but srun is not on PATH, so lc cannot launch "
            "workers across the allocation. If the variable leaked in from "
            "outside — a container, a copied environment — unset it to run on "
            "this machine alone."
        )
    nodes = int(os.environ.get("SLURM_JOB_NUM_NODES") or os.environ.get("SLURM_NNODES") or 1)
    cpus = int(os.environ.get("SLURM_CPUS_ON_NODE") or os.cpu_count() or 1)
    host = os.environ.get("SLURMD_NODENAME") or socket.gethostname()

    with LocalCluster(  # type: ignore[no-untyped-call]
        n_workers=0,
        host=host,
        dashboard_address=None,
    ) as cluster:
        with Client(cluster) as client:  # type: ignore[no-untyped-call]
            # Not `project._run`, deliberately: that seam is run-to-completion
            # capture, and this child lives as long as the run — and its
            # stderr must reach the terminal live, because srun's own errors
            # (bad step, drained node) are the user's to see as they happen.
            env = dict(os.environ)
            env.setdefault("DASK_LOGGING__DISTRIBUTED", "warning")
            proc = subprocess.Popen(
                _srun_argv(cluster.scheduler_address, nodes, cpus, tempfile.gettempdir()),
                env=env,
            )
            try:
                _await_workers(client, proc, nodes)
                yield client
            finally:
                _wind_down(client, proc)


def _srun_argv(scheduler: str, nodes: int, cpus: int, scratch: str) -> list[str]:
    """Build the one srun invocation that spans the allocation.

    Args:
        scheduler: The driver-side scheduler's address.
        nodes: Allocated node count — one worker task per node.
        cpus: Threads per worker; tasks block in ``subprocess.wait()``
            with the GIL released, so threads carry a whole node.
        scratch: Node-local directory for the worker's own state — never
            the project tree, and explicit so ambient Dask configuration
            cannot point it there.

    Returns:
        The argv, ready for Popen.
    """
    return [
        "srun",
        # Inside salloc's interactive step a plain srun can wait forever
        # for the resources that step already holds.
        "--overlap",
        f"--ntasks={nodes}",
        "--ntasks-per-node=1",
        # Without it the step is entitled to one core and the worker's
        # threads are bound to it.
        f"--cpus-per-task={cpus}",
        # The driver's own interpreter — the tool environment on the
        # shared filesystem — so driver and workers are the identical
        # installation. `-m` cannot resolve to some other install the
        # way a PATH-found `dask` can.
        sys.executable,
        "-m",
        "distributed.cli.dask_worker",
        scheduler,
        "--nthreads",
        str(cpus),
        "--nworkers",
        "1",
        "--no-dashboard",
        # srun will not relaunch the task, so an auto-restart nanny adds
        # nothing and logs a spurious death on every clean retirement.
        "--no-nanny",
        # A worker whose driver is gone exits instead of holding its node
        # to walltime.
        "--death-timeout",
        "60",
        # The real work happens in subprocesses behind the exec boundary,
        # whose memory Dask cannot see — its manager could only ever
        # pause a worker over phantom numbers.
        "--memory-limit",
        "0",
        "--local-directory",
        scratch,
    ]


def _await_workers(client: Any, proc: subprocess.Popen[bytes], nodes: int) -> None:
    """Wait until every node's worker is registered.

    A poll loop rather than ``wait_for_workers`` so that a dead srun is
    reported as srun's own exit code, not as a timeout two minutes later.
    """
    deadline = time.monotonic() + _WORKER_WAIT
    while True:
        connected = len(client.scheduler_info()["workers"])
        if connected >= nodes:
            return
        if (code := proc.poll()) is not None:
            raise ProjectError(
                f"srun exited with code {code} before the allocation's workers "
                f"connected ({connected} of {nodes} had) — its error is above."
            )
        if time.monotonic() >= deadline:
            raise ProjectError(
                f"expected {nodes} dask workers (one per allocated node); "
                f"{connected} connected within {int(_WORKER_WAIT)}s."
            )
        time.sleep(0.5)


def _wind_down(client: Any, proc: subprocess.Popen[bytes]) -> None:
    """Retire the workers, then reap srun — gracefully first.

    Retirement makes each worker exit 0, so srun ends silently; killing
    srun instead prints "srun: forcing job termination" on every clean
    run. The escalation below it is for the runs that were not clean.
    """
    try:
        client.retire_workers(close_workers=True, remove=True)
    except Exception:
        pass
    try:
        proc.wait(timeout=_REAP_GRACE)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=_REAP_GRACE / 2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
