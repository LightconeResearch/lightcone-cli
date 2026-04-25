"""Reconstructable entrypoint for ``dagster-dask`` workers.

``dagster-dask`` does not pickle the asset definitions across workers; it
ships a :class:`dagster.ReconstructableJob` pointer and each worker
re-imports :func:`build_pilot_job` below to rebuild the same Definitions
in-process.  Workers then call :func:`dagster.execute_plan` and the asset
body shells out to the configured container runtime.

State is passed through environment variables that ``lc run`` sets before
calling :func:`dagster.execute_job`:

* ``LIGHTCONE_PROJECT_PATH`` — the orchestrator's working directory; the
  shared filesystem assumption means workers see the same path.
* ``LIGHTCONE_PILOT`` — the pilot config name to load.
* ``LIGHTCONE_UNIVERSE`` — the universe id (defaults to ``"baseline"``).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dagster import reconstructable


def build_pilot_job() -> Any:
    """Module-scope callable. Workers re-import and call this."""
    from dagster_dask import dask_executor

    from lightcone.engine.assets import build_definitions
    from lightcone.engine.pilots import load_pilot_config

    project_path = Path(os.environ["LIGHTCONE_PROJECT_PATH"])
    pilot_name = os.environ["LIGHTCONE_PILOT"]
    universe_id = os.environ.get("LIGHTCONE_UNIVERSE", "baseline")

    pilot_config = load_pilot_config(pilot_name)
    if pilot_config is None:
        raise RuntimeError(
            f"LIGHTCONE_PILOT={pilot_name!r} but no pilot config exists at "
            f"~/.lightcone/pilots/{pilot_name}.yaml"
        )

    defs = build_definitions(
        project_path,
        pilot_config=pilot_config,
        universe_id=universe_id,
        no_build=True,                  # workers never rebuild containers
        executor_def=dask_executor,
    )
    return defs.get_implicit_global_asset_job_def()


def get_pilot_job() -> Any:
    """Build the :class:`ReconstructableJob`. Caller must set env vars first.

    ``reconstructable()`` validates the target by calling it once at
    construction time, so the ``LIGHTCONE_*`` env vars must already be in
    place when this is invoked.  ``lc run`` does that immediately before
    the call.
    """
    return reconstructable(build_pilot_job)
