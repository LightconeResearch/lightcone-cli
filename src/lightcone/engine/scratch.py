"""Resolve and prepare lightcone's scratch root.

A single concept: where lightcone keeps its operational state — snakemake
metadata, dask worker spill, cross-node stdout locks. Resolved at the
start of every ``lc run``. Resolution precedence (first hit wins):

1. ``LIGHTCONE_SCRATCH`` env var (escape hatch / CI override).
2. ``scratch_root`` in ``<project>/.lightcone/lightcone.yaml`` (per-project pin).
3. ``scratch_root`` from the detected site in
   :mod:`lightcone.engine.site_registry`. Stored as a shell expression
   (e.g. ``$SCRATCH``) and expanded with :func:`os.path.expandvars`.
4. :func:`tempfile.gettempdir` fallback (single-node only).

The resolved path is then used as the parent of ``.lightcone/`` —
multiple projects can share one scratch root without colliding because
snakemake state is keyed by a hash of the project's absolute path.

Why this matters on NERSC: ``$HOME`` and ``/global/cfs`` are mounted on
compute nodes via DVS, which `does not support file locking
<https://docs.nersc.gov/performance/io/dvs/>`_. Snakemake's workflow
lock, our cross-node stdout lock, and any future coordination primitive
silently fail there. ``$SCRATCH`` is Lustre, which works correctly.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import os
import shutil
import socket
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import yaml

from lightcone.engine.site_registry import detect_site, get_site_defaults

LIGHTCONE_SCRATCH_ENV = "LIGHTCONE_SCRATCH"


@dataclass(frozen=True)
class RunDirs:
    """Per-run scratch directories. All paths are guaranteed to exist."""

    root: Path  # ``<scratch>/.lightcone``
    snakemake_state: Path  # ``<scratch>/.lightcone/snakemake/<project-hash>/.snakemake``
    dask_local: Path  # ``<scratch>/.lightcone/dask/<run-id>``
    lock_path: Path  # ``<scratch>/.lightcone/locks/<run-id>.lock``
    # Project-level sentinel for the run-exclusion flock. Held for the
    # duration of one ``lc run`` to prevent concurrent invocations on
    # the same project from interleaving Snakemake state updates.
    run_lock_path: Path  # ``<scratch>/.lightcone/locks/<project-hash>.run-lock``


def resolve_scratch_root(project_path: Path) -> Path:
    """Resolve the scratch root directory for *project_path*.

    See module docstring for the precedence chain. Always returns a
    ``Path``; never raises. The returned path is not created — callers
    that need sub-directories should use :func:`prepare_run_dirs`.
    """
    if env := os.environ.get(LIGHTCONE_SCRATCH_ENV):
        return Path(os.path.expandvars(env)).expanduser()

    project_cfg = project_path / ".lightcone" / "lightcone.yaml"
    if project_cfg.is_file():
        try:
            data = yaml.safe_load(project_cfg.read_text()) or {}
        except yaml.YAMLError:
            data = {}
        if val := data.get("scratch_root"):
            return Path(os.path.expandvars(str(val))).expanduser()

    site_key = detect_site(socket.gethostname())
    if site_key:
        site = get_site_defaults(site_key) or {}
        if val := site.get("scratch_root"):
            expanded = os.path.expandvars(str(val))
            # ``$VAR`` left intact means the env wasn't set — don't write
            # to a literal path called ``$SCRATCH``. Fall through.
            if not expanded.startswith("$") and "$" not in expanded:
                return Path(expanded).expanduser()

    return Path(tempfile.gettempdir())


def project_hash(project_path: Path) -> str:
    """Stable short hash keyed on the absolute project path.

    Used to namespace snakemake state under the scratch root: two
    different projects sharing one ``$SCRATCH`` get separate
    ``.snakemake/`` dirs; the same project moved to a different machine
    gets a fresh state (since absolute path differs).
    """
    return hashlib.sha256(str(project_path.resolve()).encode("utf-8")).hexdigest()[:12]


def prepare_run_dirs(project_path: Path, *, run_id: str | None = None) -> RunDirs:
    """Create and return per-run scratch sub-directories.

    *run_id* defaults to the current PID — unique per ``lc run``
    invocation, easily mappable to a process for debugging. Lock and
    dask-local dirs are run-scoped (cleaned per invocation); snakemake
    state is project-scoped (persistent across invocations).
    """
    scratch = resolve_scratch_root(project_path)
    root = scratch / ".lightcone"
    rid = run_id or str(os.getpid())
    pkey = project_hash(project_path)
    snakemake_state = root / "snakemake" / pkey / ".snakemake"
    dask_local = root / "dask" / rid
    lock_path = root / "locks" / f"{rid}.lock"
    run_lock_path = root / "locks" / f"{pkey}.run-lock"
    for d in (root, snakemake_state.parent, dask_local, lock_path.parent):
        d.mkdir(parents=True, exist_ok=True)
    # Touch lockfiles so workers can ``flock`` them without racing on
    # ``O_CREAT``. Empty file is fine — flock is independent of contents.
    lock_path.touch(exist_ok=True)
    run_lock_path.touch(exist_ok=True)
    return RunDirs(
        root=root,
        snakemake_state=snakemake_state,
        dask_local=dask_local,
        lock_path=lock_path,
        run_lock_path=run_lock_path,
    )


def ensure_snakemake_symlink(project_path: Path, snakemake_state: Path) -> None:
    """Repoint ``<project>/.snakemake`` to *snakemake_state*.

    Snakemake stores its workflow lock and per-job metadata under
    ``.snakemake/`` in the working directory. On NERSC-like sites where
    the project lives on a DVS-mounted filesystem, that directory's
    ``flock``s are silent no-ops and small-file I/O is slow. Redirecting
    via symlink lets snakemake find its state at the canonical path
    while the bytes actually live on Lustre.

    If a real (non-symlink) ``.snakemake/`` already exists from a prior
    direct ``snakemake`` invocation, we move it aside with a
    ``.snakemake.legacy`` suffix rather than deleting — losing somebody
    else's job metadata silently is a worse failure than leaving a
    backup.
    """
    link = project_path / ".snakemake"
    snakemake_state.mkdir(parents=True, exist_ok=True)
    if link.is_symlink():
        try:
            if link.resolve() == snakemake_state.resolve():
                return
        except OSError:
            pass
        link.unlink()
    elif link.exists():
        backup = link.with_name(".snakemake.legacy")
        # If a backup already exists, keep the existing one — that's
        # likely from an even older run; don't mask it.
        if not backup.exists():
            link.rename(backup)
        else:
            import shutil

            shutil.rmtree(link)
    link.symlink_to(snakemake_state, target_is_directory=True)


class RunLockBusyError(RuntimeError):
    """Raised when another ``lc run`` already holds the project's run-lock."""


@contextlib.contextmanager
def acquire_run_lock(rundirs: RunDirs) -> Iterator[None]:
    """Hold an exclusive flock on the project's run-lock for the duration.

    The lock is at ``<scratch>/.lightcone/locks/<project-hash>.run-lock``
    so each project gets its own. Acquired non-blocking — concurrent
    ``lc run`` invocations on the same project hit :class:`RunLockBusyError`
    rather than queueing silently.

    The kernel releases the lock automatically when the holding process
    exits (clean shutdown, crash, or SIGKILL), so a previous run that
    died ungracefully cannot leave us deadlocked. Any ``.snakemake/``
    workflow lock that survived a prior crash gets cleared inside this
    context — safe to do because we hold the project-wide lock.
    """
    fd = os.open(rundirs.run_lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise RunLockBusyError(
                f"Another ``lc run`` holds the lock at "
                f"{rundirs.run_lock_path}. Wait for it to finish, or "
                f"if you're certain it's gone, delete the lockfile."
            ) from e
        # We're alone. Clear any leftover snakemake lock from a prior
        # crashed run — snakemake stores zero-byte sentinel files in
        # ``.snakemake/locks/`` that aren't tied to a process and would
        # otherwise refuse the next workflow start.
        snake_locks = rundirs.snakemake_state / "locks"
        if snake_locks.is_dir():
            for entry in snake_locks.iterdir():
                with contextlib.suppress(OSError):
                    if entry.is_dir():
                        shutil.rmtree(entry)
                    else:
                        entry.unlink()
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
