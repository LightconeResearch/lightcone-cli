"""Bundled Postgres for cluster-scoped Dagster storage.

SQLite + shared HPC filesystems (NFS/Lustre/GPFS) is a known broken
combination from compute nodes — POSIX locking semantics aren't reliable
across the network.  Lightcone bundles a Postgres daemon (via the
``pixeltable-pgserver`` wheel — a maintained fork of ``pgserver`` that
ships pip-installable manylinux/macos/windows binaries) and runs it as
part of the cluster lifecycle.

Lifecycle:

* :func:`start_pg` is called from ``lc cluster start``/``attach``.  It
  invokes ``pgserver.get_server(..., cleanup_mode=None)`` so the daemon
  *outlives* the launching script — the cluster's state file then owns
  the PG instance via its data dir + recorded URI.
* :func:`stop_pg` re-attaches with ``cleanup_mode='stop'`` and lets the
  handle close, which sends ``pg_ctl stop`` to the running daemon.
* The data dir is project-local (``<project>/.lightcone/pg/``) so
  history persists across cluster sessions and across scratch purges.

If ``pixeltable-pgserver`` is unavailable (older install, locked-down
environment), :func:`start_pg` returns ``None`` and the caller falls
back to no-PG behaviour.  Lightcone never *requires* the dep at runtime
— the cluster still works without it; you just lose the persistent
Dagster history.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def pg_data_dir(project_root: Path) -> Path:
    """Per-project Postgres data directory (``<project>/.lightcone/pg/``)."""
    return project_root / ".lightcone" / "pg"


def start_pg(project_root: Path) -> str | None:
    """Start (or re-attach to) a Postgres daemon for *project_root*.

    Returns the connection URI (``postgresql://…``) or ``None`` if the
    optional ``pixeltable-pgserver`` dependency isn't available — the
    caller treats ``None`` as "no PG; Dagster falls back to SQLite".

    Idempotent: if a daemon is already running against this data dir,
    ``get_server`` re-attaches and returns its URI.  The daemon outlives
    this call (``cleanup_mode=None``); call :func:`stop_pg` to shut down.
    """
    try:
        import pixeltable_pgserver as pgserver
    except ImportError:
        logger.warning(
            "pixeltable-pgserver not installed; cluster will run without "
            "persistent Dagster storage. `pip install pixeltable-pgserver`."
        )
        return None

    data_dir = pg_data_dir(project_root)
    data_dir.parent.mkdir(parents=True, exist_ok=True)
    server = pgserver.get_server(data_dir, cleanup_mode=None)
    uri = server.get_uri()
    logger.info("Postgres up at %s (data: %s)", uri, data_dir)
    return uri


def stop_pg(project_root: Path) -> None:
    """Shut down the project's Postgres daemon if it's running.

    Idempotent — a no-op if the daemon isn't running or the dependency
    isn't installed.  The data directory is preserved so a future
    :func:`start_pg` re-attaches to the same database.
    """
    try:
        import pixeltable_pgserver as pgserver
    except ImportError:
        return

    data_dir = pg_data_dir(project_root)
    if not data_dir.exists():
        return
    try:
        # Re-attach with cleanup_mode='stop'; closing the handle stops PG.
        server = pgserver.get_server(data_dir, cleanup_mode="stop")
        server.cleanup()
    except Exception as e:  # noqa: BLE001 — surface any pgserver issue, don't crash stop
        logger.warning("Postgres stop for %s raised: %s", data_dir, e)
