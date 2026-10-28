"""Serving a project's analysis in the browser: one detached app per project.

The server is jupyter_server's, and so is its bookkeeping: each server
writes a runtime file naming its URL, token and root, and
``list_running_servers`` reads them back. What lc adds is where those files
live — a directory private to the user, because the token is in them and
in the log, and kept per host, because a shared ``$HOME`` spans many login
nodes while jupyter can only tell whether a process is alive on its own.
"""

from __future__ import annotations

import hashlib
import os
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from lightcone.engine.project import ProjectError

# Stops a server nobody is using; jupyter_server's own activity clock.
IDLE_TIMEOUT = 30 * 60
_STARTUP_TIMEOUT = 60.0
_STATE = Path("~/.lightcone/serve")
# The command after the interpreter; tests replace it to stage a failing child.
_APP_ARGS = ("-m", "lightcone.app")


@dataclass(frozen=True)
class Served:
    """A server for one project, and how to reach it."""

    url: str
    host: str
    pid: int
    started: bool

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON shape of ``lc serve --json``."""
        return asdict(self)


def serve(root: Path, record: str | None = None) -> Served:
    """Return the URL of this project's server, starting one if none runs here.

    Args:
        root: The project root; it must hold ``astra.yaml``.
        record: A canonical record path for the page to open, passed through
            unchecked — the browser is what resolves the spec.

    Returns:
        The server's URL (token included), host and pid, and whether this
        call started it.

    Raises:
        ProjectError: If ``root`` holds no ``astra.yaml``, or the server
            does not come up.
    """
    if not (root / "astra.yaml").is_file():
        raise ProjectError(f"no astra.yaml in {root} — run `lc serve` from the project root")
    host = socket.gethostname().split(".", 1)[0]
    runtime = _STATE.expanduser() / host
    runtime.mkdir(parents=True, exist_ok=True)
    runtime.chmod(0o700)

    info = _running(runtime, root)
    started = info is None
    if info is None:
        info = _start(runtime, root)
    query = {"token": info["token"], **({"record": record} if record else {})}
    url = f"{info['url'].rstrip('/')}/lightcone?{urlencode(query)}"
    return Served(url=url, host=host, pid=info["pid"], started=started)


def _running(runtime: Path, root: Path) -> dict[str, Any] | None:
    from jupyter_server.serverapp import list_running_servers

    for info in list_running_servers(str(runtime)):
        if info.get("root_dir") == os.path.abspath(root):
            return dict(info)
    return None


def _start(runtime: Path, root: Path) -> dict[str, Any]:
    log = runtime / f"{hashlib.sha256(os.fsencode(os.path.abspath(root))).hexdigest()[:16]}.log"
    env = {**os.environ, "JUPYTER_RUNTIME_DIR": str(runtime), "JUPYTER_NO_CONFIG": "1"}
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        # The server outlives this command, so it is not a `project._run` call.
        process = subprocess.Popen(
            [
                sys.executable, *_APP_ARGS,
                "--no-browser",
                f"--ServerApp.root_dir={root}",
                f"--ServerApp.shutdown_no_activity_timeout={IDLE_TIMEOUT}",
            ],
            cwd=root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=fd,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        os.close(fd)
    deadline = time.monotonic() + _STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        info = _running(runtime, root)
        if info is not None and info.get("pid") == process.pid:
            return info
        if process.poll() is not None:
            raise ProjectError(f"the server exited during startup:\n{_tail(log)}")
        time.sleep(0.1)
    process.kill()
    raise ProjectError(
        f"the server did not start within {_STARTUP_TIMEOUT:.0f}s:\n{_tail(log)}"
    )


def _tail(log: Path, lines: int = 20) -> str:
    return "\n".join(log.read_text(errors="replace").splitlines()[-lines:])
