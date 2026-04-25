"""Reconstructable entrypoint for ``dagster-dask`` workers.

``dagster-dask`` does not pickle the asset definitions across workers; it
ships a :class:`dagster.ReconstructableJob` pointer and each worker
re-imports :func:`build_cluster_job` below to rebuild the same Definitions
in-process.  Workers then call :func:`dagster.execute_plan` and the asset
body shells out to the configured container runtime.

State is passed through environment variables that ``lc run`` sets before
calling :func:`dagster.execute_job`:

* ``LIGHTCONE_PROJECT_PATH`` — the orchestrator's working directory; the
  shared filesystem assumption means workers see the same path.
* ``LIGHTCONE_CLUSTER`` — the cluster config name to load.
* ``LIGHTCONE_UNIVERSE`` — the universe id (defaults to ``"baseline"``).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dagster import reconstructable


def build_cluster_job() -> Any:
    """Module-scope callable. Workers re-import and call this.

    ``LIGHTCONE_CLUSTER`` is the cluster config name to load.  If unset
    or empty, this is the local-execution case: no config is loaded, and
    the orchestrator will pass ``cluster: {local: {}}`` in the run_config
    so dagster-dask spins up a :class:`distributed.LocalCluster` for us.
    """
    from dagster_dask import dask_executor

    from lightcone.engine.assets import build_definitions
    from lightcone.engine.clusters import load_cluster_config

    project_path = Path(os.environ["LIGHTCONE_PROJECT_PATH"])
    cluster_name = os.environ.get("LIGHTCONE_CLUSTER") or None
    universe_id = os.environ.get("LIGHTCONE_UNIVERSE", "baseline")

    cluster_config: dict | None = None
    if cluster_name:
        cluster_config = load_cluster_config(cluster_name)
        if cluster_config is None:
            raise RuntimeError(
                f"LIGHTCONE_CLUSTER={cluster_name!r} but no cluster config exists at "
                f"~/.lightcone/clusters/{cluster_name}.yaml"
            )

    defs = build_definitions(
        project_path,
        cluster_config=cluster_config,
        universe_id=universe_id,
        no_build=True,                  # workers never rebuild containers
        executor_def=dask_executor,
    )
    return defs.get_implicit_global_asset_job_def()


def get_cluster_job() -> Any:
    """Build the :class:`ReconstructableJob`. Caller must set env vars first.

    ``reconstructable()`` validates the target by calling it once at
    construction time, so the ``LIGHTCONE_*`` env vars must already be in
    place when this is invoked.  ``lc run`` does that immediately before
    the call.
    """
    return reconstructable(build_cluster_job)
