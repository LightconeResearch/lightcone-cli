"""Serving a project's analysis in the browser: one detached app per project.

The server is jupyter_server's, and so is its bookkeeping: each server
writes a runtime file naming its URL, token and root, and
``list_running_servers`` reads them back. What lc adds is where those files
live — a directory private to the user, because the token is in them and
in the log, and kept per host, because a shared ``$HOME`` spans many login
nodes while jupyter can only tell whether a process is alive on its own.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import psutil

from lightcone.engine.project import SPEC_FILENAME, ProjectError

# Stops a server nobody is using; jupyter_server's own activity clock.
IDLE_TIMEOUT = 30 * 60
_STARTUP_TIMEOUT = 60.0
_STATE = Path("~/.lightcone/serve")
# The module the server runs as, and what tells a live server from a reused pid.
_APP = "lightcone.app"
# The page the app serves; defined here, where `lc view` must find it without
# importing the app (which imports jupyterlab_server).
BUNDLE = Path(__file__).parents[1] / "app" / "static" / "bundle.js"


@dataclass(frozen=True)
class Served:
    """A server for one project, and how to reach it."""

    url: str
    host: str
    pid: int
    started: bool

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON shape of ``lc view --json``."""
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
        ProjectError: If ``root`` holds no ``astra.yaml``, this installation
            has no built page, or the server does not come up.
    """
    if not (root / SPEC_FILENAME).is_file():
        raise ProjectError(f"no {SPEC_FILENAME} in {root} — run `lc view` from the project root")
    # A wheel always carries the page; an editable install built without Node
    # does not, and would otherwise serve a blank one.
    if not BUNDLE.is_file():
        raise ProjectError(
            f"this installation has no built page ({BUNDLE} is missing) — "
            "build it in the lightcone-cli checkout: `cd ui && npm ci && npm run build`"
        )
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
    # jupyter_server's `list_running_servers`, without the second its import costs.
    for file in runtime.glob("jpserver-*.json"):
        try:
            info = json.loads(file.read_text())
        except (OSError, ValueError):
            continue  # being written, or being removed
        if not (isinstance(info, dict) and _serving(info.get("pid"))):
            file.unlink(missing_ok=True)  # malformed, or its server is gone
        elif info.get("root_dir") == os.path.abspath(root):
            return info
    return None


def _serving(pid: object) -> bool:
    # A dead server's pid is reused — after a reboot, or a node crash — so the
    # process must still be the app, not merely exist.
    if not isinstance(pid, int):
        return False
    try:
        return _APP in psutil.Process(pid).cmdline()
    except psutil.Error:
        return False


def _start(runtime: Path, root: Path) -> dict[str, Any]:
    project = runtime / hashlib.sha256(os.fsencode(os.path.abspath(root))).hexdigest()[:16]
    log = project.with_suffix(".log")
    env = {**os.environ, "JUPYTER_RUNTIME_DIR": str(runtime), "JUPYTER_NO_CONFIG": "1"}
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        # The server outlives this command, so it is not a `project._run` call.
        process = subprocess.Popen(
            [
                sys.executable, "-m", _APP,
                "--no-browser",
                f"--ServerApp.root_dir={root}",
                f"--ServerApp.shutdown_no_activity_timeout={IDLE_TIMEOUT}",
                # A page's login cookie is signed with this secret, and jupyter
                # keeps one per runtime directory. One per project instead, so a
                # page left open reconnects to a later server for its project,
                # never to another project's on the port its own one freed.
                f"--ServerApp.cookie_secret_file={project.with_suffix('.secret')}",
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
            # jupyter writes its file before its event loop binds the port.
            url = urlsplit(info["url"])
            with contextlib.suppress(OSError), socket.create_connection(
                (url.hostname, url.port), timeout=1
            ):
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
