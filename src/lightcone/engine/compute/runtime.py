"""Private connection material for standard Dask schedulers and clients."""

from __future__ import annotations

import json
import math
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from lightcone.engine.compute.model import ComputeError


def private_directory(path: Path, *, create: bool = False) -> Path:
    """Check an owner-only directory without following symlinks.

    Args:
        path: Absolute directory containing allocation credentials.
        create: Create missing directories with private permissions.

    Raises:
        ComputeError: If a component is a symlink or the final directory is unsafe.
    """
    path = path.expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise ComputeError(f"compute connection directory must be absolute: {path}")
    try:
        for component in (*reversed(path.parents), path):
            try:
                info = component.lstat()
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    component.mkdir(mode=0o700)
                except FileExistsError:
                    pass
                info = component.lstat()
            if not stat.S_ISDIR(info.st_mode):
                raise ComputeError(f"compute connection path is not a plain directory: {component}")
            if info.st_uid not in (0, os.getuid()) or (
                info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX)
            ):
                raise ComputeError(f"compute connection path has an unsafe ancestor: {component}")
        info = path.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ComputeError(
                f"compute connection directory must be owned by you with mode 0700: {path}"
            )
    except OSError as exc:
        raise ComputeError(f"cannot access compute connection directory {path}: {exc}") from exc
    return path


def _private_bytes(path: Path) -> bytes:
    private_directory(path.parent)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
            ):
                raise ComputeError(
                    f"compute connection file must be private and owned by you: {path}"
                )
            if info.st_size > 1024 * 1024:
                raise ComputeError(f"compute connection file is too large: {path}")
            return stream.read(1024 * 1024 + 1)
    except OSError as exc:
        raise ComputeError(f"cannot read compute connection file {path}: {exc}") from exc


def read_private_json(path: Path) -> dict[str, Any]:
    """Read a private, owner-controlled JSON object without following symlinks."""
    try:
        value = json.loads(_private_bytes(path))
    except (UnicodeError, ValueError) as exc:
        raise ComputeError(f"compute connection file is incomplete or invalid: {path}") from exc
    if not isinstance(value, dict):
        raise ComputeError(f"compute connection file must contain an object: {path}")
    return value


def _write_private(path: Path, value: bytes) -> None:
    private_directory(path.parent)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_private_json(path: Path, value: dict[str, Any]) -> None:
    """Publish owner-only JSON atomically inside a validated private directory."""
    _write_private(path, json.dumps(value, separators=(",", ":")).encode())


def create_security(directory: Path) -> Any:
    """Persist Dask-generated credentials for one new scheduler attempt.

    Only the scheduler's launcher creates credentials; workers load them.
    Existing credentials are refused so an attempt cannot change identity in place.
    """
    from distributed import Security

    private_directory(directory)
    if any((directory / name).exists() for name in ("tls-cert.pem", "tls-key.pem")):
        raise ComputeError(f"TLS credentials already exist for this scheduler attempt: {directory}")
    temporary = Security.temporary()  # type: ignore[no-untyped-call]
    _write_private(directory / "tls-cert.pem", temporary.tls_ca_file.encode())
    _write_private(directory / "tls-key.pem", temporary.tls_client_key.encode())
    return load_security(directory)


def load_security(directory: Path) -> Any:
    """Load private credentials using Dask's normal mutual-TLS configuration."""
    from distributed import Security

    certificate = directory / "tls-cert.pem"
    key = directory / "tls-key.pem"
    _private_bytes(certificate)
    _private_bytes(key)
    return Security(  # type: ignore[no-untyped-call]
        require_encryption=True,
        tls_ca_file=str(certificate),
        tls_client_cert=str(certificate),
        tls_client_key=str(key),
        tls_scheduler_cert=str(certificate),
        tls_scheduler_key=str(key),
        tls_worker_cert=str(certificate),
        tls_worker_key=str(key),
    )


def open_client(directory: Path, scheduler_id: str, *, timeout: float = 10) -> Any:
    """Connect through a standard scheduler file and verify the live scheduler ID.

    Returns:
        A borrowed client; callers close it without shutting down its scheduler.

    Raises:
        ComputeError: If credentials, the connection, or scheduler identity fail.
    """
    from distributed import Client

    if not scheduler_id or not math.isfinite(timeout) or timeout <= 0:
        raise ComputeError("a scheduler identity and positive connection timeout are required")
    scheduler_file = directory / "scheduler.json"
    read_private_json(scheduler_file)
    security = load_security(directory)
    client = None
    try:
        client = Client(  # type: ignore[no-untyped-call]
            scheduler_file=str(scheduler_file),
            security=security,
            timeout=timeout,
            set_as_default=False,
        )
        if client.scheduler_info().get("id") != scheduler_id:
            raise ComputeError("the scheduler identity differs from this allocation attempt")
        return client
    except Exception as exc:
        if client is not None:
            client.close(timeout=min(timeout, 5))  # type: ignore[no-untyped-call]
        if isinstance(exc, ComputeError):
            raise
        raise ComputeError(f"cannot connect to this allocation's Dask scheduler: {exc}") from exc
