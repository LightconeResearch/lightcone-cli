"""Own one standard LocalCluster for a finite allocation lifetime."""

from __future__ import annotations

import atexit
import os
import signal
import sys
import threading
import time
from pathlib import Path
from types import FrameType

from lightcone.engine.compute.runtime import (
    SCHEDULER_CONFIG,
    create_security,
    private_directory,
    read_private_json,
    write_private_json,
)


def main() -> None:
    """Run the detached allocation owner until shutdown or its walltime expires."""
    os.umask(0o077)
    directory = private_directory(Path(sys.argv[1]))
    launch = read_private_json(directory / "launch.json")
    if os.getsid(0) != os.getpid() or os.getpgrp() != os.getpid():
        raise RuntimeError("the local allocation owner must lead its own process session")
    stopped = threading.Event()

    def stop(_signum: int, _frame: FrameType | None) -> None:
        stopped.set()

    def expire(_signum: int, _frame: FrameType | None) -> None:
        # This bound does not depend on the scheduler loop or graceful Dask close.
        os.killpg(os.getpgrp(), signal.SIGKILL)

    # Register before importing Dask so its multiprocessing finalizers run first.
    # A recipe can ignore SIGTERM and outlive its worker: keep custody of the
    # session and walltime timer until every member has been sent SIGKILL.
    atexit.register(os.killpg, os.getpgrp(), signal.SIGKILL)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGALRM, expire)
    remaining = float(launch["deadline"]) - time.monotonic()
    signal.setitimer(signal.ITIMER_REAL, max(0.001, remaining))
    try:
        while not launch["identity"]:
            if stopped.wait(0.01):
                return
            launch = read_private_json(directory / "launch.json")
        import dask
        from distributed import LocalCluster

        security = create_security(directory)
        allocation = read_private_json(directory / "identity.json")
        gpus = int(allocation["gpus"])
        if not gpus:
            os.environ["CUDA_VISIBLE_DEVICES"] = ""
        with dask.config.set(SCHEDULER_CONFIG), LocalCluster(  # type: ignore[no-untyped-call]
            n_workers=1,
            threads_per_worker=int(launch["task_slots"]),
            processes=True,
            host="127.0.0.1",
            scheduler_port=0,
            dashboard_address=None,
            worker_dashboard_address=None,
            security=security,
            protocol="tls",
            scheduler_kwargs={
                "scheduler_file": str(directory / "scheduler.json"),
                "dashboard": False,
                "dashboard_address": "127.0.0.1:0",
            },
            local_directory=str(private_directory(Path(launch["scratch"]))),
            # Recipes use subprocesses: Dask's Python-process RSS cannot enforce
            # their RAM envelope. Local resource limits are explicitly cooperative.
            memory_limit=0,
            resources={
                "CPU": int(allocation["cpus"]), "MEMORY": int(allocation["memory"]), "GPU": gpus,
            },
            silence_logs=50,
        ) as cluster:
            write_private_json(
                directory / "connection.json",
                {"identity": launch["identity"], "scheduler_id": cluster.scheduler.id},
            )
            stopped.wait()
    except Exception as exc:
        write_private_json(directory / "error.json", {"error": str(exc)[:4096]})
        raise


if __name__ == "__main__":
    main()
