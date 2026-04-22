"""Pytest fixtures for SLURM emulator integration tests (ADR-0001 §5.3).

The ``slurm_emulator`` fixture brings up the Docker-compose SLURM cluster
defined in ``docker-compose.yml``.  Tests are skipped automatically when
Docker is unavailable or the emulator fails to start within the timeout.

Credentials match the upstream image defaults:
  SSH host: localhost, port: 2223, user: submitter, password: submitter
"""
from __future__ import annotations

import subprocess
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

_COMPOSE_FILE = Path(__file__).parent / "docker-compose.yml"
_SSH_PORT = 2223
_SSH_USER = "submitter"
_SSH_PASSWORD = "submitter"
_REMOTE_BASE = f"/home/{_SSH_USER}/lightcone-runs"
_STARTUP_TIMEOUT = 120


def _docker_available() -> bool:
    """Return True if the Docker daemon is reachable."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _wait_for_ssh(port: int, timeout: int = _STARTUP_TIMEOUT) -> bool:
    """Return True when sshd on localhost:port accepts connections."""
    import socket

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("localhost", port), timeout=2):
                return True
        except OSError:
            time.sleep(2)
    return False


@pytest.fixture(scope="session")
def slurm_emulator() -> Generator[None, None, None]:
    """Session-scoped fixture: start the SLURM Docker cluster and tear it down.

    Skipped when Docker is unavailable.  Tests in this session that depend on
    this fixture are automatically skipped via ``pytest.mark.needs_slurm_docker``.
    """
    if not _docker_available():
        pytest.skip("Docker daemon not reachable — skipping SLURM emulator tests")

    compose_cmd = ["docker", "compose", "-f", str(_COMPOSE_FILE)]

    # Bring up the cluster
    up = subprocess.run(
        [*compose_cmd, "up", "-d", "--wait", f"--wait-timeout={_STARTUP_TIMEOUT}"],
        capture_output=True,
        text=True,
        timeout=_STARTUP_TIMEOUT + 30,
    )
    if up.returncode != 0:
        pytest.skip(
            f"docker compose up failed (rc={up.returncode}): {up.stderr[:500]}"
        )

    # Wait for SSH to be ready on slurmctld (port 2223)
    if not _wait_for_ssh(_SSH_PORT):
        subprocess.run([*compose_cmd, "down", "-v"], capture_output=True)
        pytest.skip("SLURM emulator SSH did not become reachable within timeout")

    try:
        yield
    finally:
        subprocess.run(
            [*compose_cmd, "down", "-v"],
            capture_output=True,
            timeout=60,
        )


@pytest.fixture()
def emulator_target() -> dict[str, Any]:
    """Normalized target config pointing at the local SLURM Docker emulator."""
    return {
        "mode": "slurm",
        "ssh": {
            "host": "localhost",
            "port": _SSH_PORT,
            "user": _SSH_USER,
            "password": _SSH_PASSWORD,
        },
        "queue": {
            "partition": "debug",
            "time_limit": "00:05:00",
        },
        "remote_base": _REMOTE_BASE,
    }
