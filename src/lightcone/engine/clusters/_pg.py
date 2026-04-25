"""Bundled Postgres for cluster-scoped Dagster storage.

SQLite + shared HPC filesystems (NFS/Lustre/GPFS) is a known broken
combination from compute nodes — POSIX locking semantics aren't reliable
across the network, and Dagster's official guidance for non-trivial
deployments is Postgres.  Lightcone bundles a Postgres daemon (via the
``pixeltable-pgserver`` wheel — a maintained fork that ships pip-
installable manylinux/macos/windows binaries) and runs it as part of
the cluster lifecycle.

Lifecycle:

* :func:`start_pg` is called from ``lc cluster start`` / ``attach``.  It
  invokes ``pg_ctl start`` directly (rather than ``pgserver.get_server``)
  so we control the socket location.  This matters: HPC home
  filesystems (GPFS, Lustre) often allow ``socket()`` bind but reject
  ``chmod`` on socket files, so postgres' default of putting its unix
  socket inside the data dir fails on NERSC home with
  ``could not set permissions of file ".s.PGSQL.NNNN": Invalid argument``.
  We force the socket dir to ``/tmp`` (always tmpfs / always supports
  chmod on socket files), keeping the actual data dir on home where
  the user wants their persistent history.
* :func:`stop_pg` invokes ``pg_ctl stop -m fast`` against the same data
  dir.  Idempotent — a no-op if no daemon is running.
* The data dir is project-local at ``<project>/.lightcone/pg/`` so
  history persists across cluster sessions and survives scratch purges.

If ``pixeltable-pgserver`` isn't available at runtime (older install,
locked-down env), :func:`start_pg` returns ``None`` and the cluster
runs without persistent Dagster storage — the cluster itself still
works; you just lose the run history.
"""
from __future__ import annotations

import hashlib
import logging
import os
import socket
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def pg_data_dir(project_root: Path) -> Path:
    """Per-project Postgres data directory (``<project>/.lightcone/pg/``)."""
    return project_root / ".lightcone" / "pg"


def _socket_dir_for_data(data_dir: Path) -> Path:
    """Per-data-dir tmpfs socket dir (always supports chmod on socket files).

    Hashed by the data dir path to avoid collisions if multiple projects
    coexist on one node.  Per-uid prefix keeps shared compute nodes
    (multiple users on one node) from stepping on each other.
    """
    h = hashlib.sha256(str(data_dir).encode()).hexdigest()[:8]
    return Path("/tmp") / f"lightcone-pg-{os.getuid()}-{h}"


def _bin_path() -> Path | None:
    """Locate the bundled postgres binaries, or ``None`` if unavailable."""
    try:
        from pixeltable_pgserver.utils import POSTGRES_BIN_PATH
    except ImportError:
        return None
    return Path(POSTGRES_BIN_PATH)


def _free_port() -> int:
    """Pick a currently-free TCP port (race-prone but good enough at startup)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def start_pg(project_root: Path) -> str | None:
    """Start (or re-attach to) a Postgres daemon for *project_root*.

    Returns the connection URI (``postgresql://…``) or ``None`` if
    ``pixeltable-pgserver`` isn't installed.  Idempotent: if a daemon is
    already running against this data dir, returns the URI from the
    previously written marker file.
    """
    bin_path = _bin_path()
    if bin_path is None:
        logger.warning(
            "pixeltable-pgserver not installed; cluster will run without "
            "persistent Dagster storage. `pip install pixeltable-pgserver`."
        )
        return None

    pg_ctl = str(bin_path / "pg_ctl")
    initdb = str(bin_path / "initdb")

    data_dir = pg_data_dir(project_root)
    data_dir.parent.mkdir(parents=True, exist_ok=True)
    uri_marker = data_dir / "lightcone-uri"

    # Already running for this data dir → return its URI.
    if (data_dir / "PG_VERSION").exists():
        status = subprocess.run(
            [pg_ctl, "status", "-D", str(data_dir)],
            capture_output=True, text=True,
        )
        if status.returncode == 0 and uri_marker.exists():
            uri = uri_marker.read_text().strip()
            logger.info("Re-attaching to running Postgres at %s", uri)
            return uri
        # Stale state: pidfile present but server dead, or no marker.
        # `pg_ctl stop` is a no-op if not actually running; safe to call.
        subprocess.run(
            [pg_ctl, "stop", "-D", str(data_dir), "-m", "immediate"],
            check=False, capture_output=True,
        )
    else:
        data_dir.mkdir(parents=True, exist_ok=True)
        # Postgres requires data dir mode 0700.
        data_dir.chmod(0o700)
        result = subprocess.run(
            [
                initdb, "-D", str(data_dir),
                "-U", "lightcone", "-A", "trust", "--encoding=UTF8",
            ],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"initdb failed (exit {result.returncode}):\n{result.stderr.strip()}"
            )

    socket_dir = _socket_dir_for_data(data_dir)
    socket_dir.mkdir(parents=True, exist_ok=True)
    socket_dir.chmod(0o777)

    port = _free_port()
    log_file = data_dir / "log"

    result = subprocess.run(
        [
            pg_ctl, "-w", "-D", str(data_dir), "-l", str(log_file),
            "-o", f"-h '*' -p {port} -k {socket_dir}",
            "start",
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        log_tail = ""
        if log_file.exists():
            log_tail = "\n--- pg log tail ---\n" + log_file.read_text()[-1500:]
        raise RuntimeError(
            f"pg_ctl start failed (exit {result.returncode}):\n"
            f"{result.stderr.strip() or result.stdout.strip()}{log_tail}"
        )

    hostname = socket.gethostname()
    uri = f"postgresql://lightcone@{hostname}:{port}/postgres"
    uri_marker.write_text(uri + "\n")
    logger.info("Postgres up at %s (data: %s)", uri, data_dir)
    return uri


def stop_pg(project_root: Path) -> None:
    """Shut down the project's Postgres daemon if it's running.

    Idempotent — a no-op if not running, not initialised, or the
    dependency isn't installed.  The data directory is preserved so a
    future :func:`start_pg` re-attaches to the same database.
    """
    bin_path = _bin_path()
    if bin_path is None:
        return

    data_dir = pg_data_dir(project_root)
    if not (data_dir / "PG_VERSION").exists():
        return

    pg_ctl = str(bin_path / "pg_ctl")
    subprocess.run(
        [pg_ctl, "stop", "-D", str(data_dir), "-m", "fast"],
        check=False, capture_output=True,
    )
    (data_dir / "lightcone-uri").unlink(missing_ok=True)
