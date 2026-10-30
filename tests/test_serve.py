"""``lc serve``'s lifecycle, against a real detached jupyter server.

The page itself needs no front-end build, so the lifecycle runs everywhere.
Whether the built front end is actually served is gated like the other
smoke suites: a checkout without ``ui/`` built skips, and
``LC_UI_TESTS_REQUIRED=1`` turns that skip into a failure.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import stat
import sys
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from lightcone.engine import serve as engine
from lightcone.engine.project import ProjectError

REQUIRED_ENV = "LC_UI_TESTS_REQUIRED"
_BUNDLE = Path(engine.__file__).parent.parent / "app" / "static" / "bundle.js"


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Serve state under tmp_path; every server started is stopped."""
    monkeypatch.setattr(engine, "_STATE", tmp_path / "serve")
    yield tmp_path / "serve"
    from jupyter_server.serverapp import list_running_servers

    for runtime in (tmp_path / "serve").glob("*"):
        for info in list_running_servers(str(runtime)):
            os.kill(info["pid"], signal.SIGTERM)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "astra.yaml").write_text("name: demo\n")
    return root


def _get(url: str) -> tuple[int, str]:
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args: object, **kwargs: object) -> None:
            return None

    try:
        with urllib.request.build_opener(_NoRedirect).open(url, timeout=10) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, ""


def test_a_directory_without_a_spec_is_refused(state: Path, tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="no astra.yaml"):
        engine.serve(tmp_path)
    assert not state.exists()


def test_the_first_call_starts_a_server_and_the_next_reuses_it(
    state: Path, project: Path
) -> None:
    first = engine.serve(project)
    assert first.started
    status, page = _get(first.url)
    assert status == 200
    assert "<title>Lightcone</title>" in page

    again = engine.serve(project, "decisions.binning")
    assert not again.started
    assert again.pid == first.pid
    assert again.url.endswith("&record=decisions.binning")


def test_the_page_needs_the_token(state: Path, project: Path) -> None:
    url = engine.serve(project).url
    status, _ = _get(url.split("?")[0])
    assert status != 200


def test_the_token_stays_private(state: Path, project: Path) -> None:
    """jupyter writes the token into files of its own beside the log, so
    the directory is what keeps it private."""
    engine.serve(project)
    (runtime,) = state.iterdir()
    assert stat.S_IMODE(runtime.stat().st_mode) == 0o700
    (log,) = runtime.glob("*.log")
    assert stat.S_IMODE(log.stat().st_mode) == 0o600


def test_a_change_on_disk_is_pushed_to_the_page(state: Path, project: Path) -> None:
    """What keeps an open page current: jupyter-live-content's WebSocket,
    loaded beside the app, tells a subscriber when a file it read changes
    on disk — here, written the way an agent writes it, not through Jupyter."""
    from tornado.websocket import websocket_connect

    url = urlsplit(engine.serve(project).url)
    (token,) = parse_qs(url.query)["token"]

    async def subscribe_then_edit() -> dict[str, object]:
        socket = await websocket_connect(
            f"ws://{url.netloc}/api/live-content/ws?token={token}"
        )
        await socket.write_message(json.dumps({"type": "client_opened", "path": "astra.yaml"}))
        await asyncio.sleep(0.5)  # the watcher starts on subscription
        (project / "astra.yaml").write_text("name: edited\n")
        message = await asyncio.wait_for(socket.read_message(), timeout=10)
        socket.close()
        assert isinstance(message, str)
        return dict(json.loads(message))

    update = asyncio.run(subscribe_then_edit())
    assert update["type"] == "server_update"
    assert update["path"] == "astra.yaml"


def test_a_server_that_dies_at_startup_reports_its_log(
    state: Path, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    popen = engine.subprocess.Popen

    def failing(args: list[str], **kwargs: object) -> object:
        return popen([args[0], "-c", "print('no such app'); raise SystemExit(3)"], **kwargs)

    monkeypatch.setattr(engine.subprocess, "Popen", failing)
    with pytest.raises(ProjectError, match="no such app"):
        engine.serve(project)


def test_the_server_stops_itself_when_idle(
    state: Path, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asserted on the argv: jupyter_server's idle clock checks once a
    minute, which is too slow to wait for here."""
    argv: list[str] = []

    def popen(args: list[str], **kwargs: object) -> None:
        argv.extend(args)
        raise OSError("not started")

    monkeypatch.setattr(engine.subprocess, "Popen", popen)
    with pytest.raises(OSError):
        engine.serve(project)
    assert f"--ServerApp.shutdown_no_activity_timeout={engine.IDLE_TIMEOUT}" in argv
    assert "--no-browser" in argv


# ---- the built front end ---------------------------------------------------


def _gate() -> None:
    if _BUNDLE.is_file():
        return
    if os.environ.get(REQUIRED_ENV):
        pytest.fail(f"{REQUIRED_ENV} is set but the front end is not built (ui/: npm run build).")
    pytest.skip("the front end is not built here")


def test_the_guard_skips_without_a_build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys.modules[__name__], "_BUNDLE", tmp_path / "bundle.js")
    monkeypatch.delenv(REQUIRED_ENV, raising=False)
    with pytest.raises(pytest.skip.Exception):
        _gate()


def test_the_guard_fails_when_skipping_is_forbidden(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys.modules[__name__], "_BUNDLE", tmp_path / "bundle.js")
    monkeypatch.setenv(REQUIRED_ENV, "1")
    with pytest.raises(pytest.fail.Exception):
        _gate()


def test_the_page_loads_the_built_front_end(state: Path, project: Path) -> None:
    _gate()
    served = engine.serve(project)
    base = served.url.split("/lightcone?")[0]
    status, _ = _get(f"{base}/static/lightcone/bundle.js")
    assert status == 200
