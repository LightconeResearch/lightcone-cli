"""The viewer's server, run for real and probed over HTTP.

The questions are what a running server answers — whether the token is
required, whether hidden paths and code-running services are absent,
whether a hostile Jupyter configuration changes any of that — and no stub
answers them. The server is the module's own app in a subprocess, so the
page bundle (built with node) is not needed: only `serve`'s refusal of an
installation without one is, and that is tested through the CLI.
"""

from __future__ import annotations

import json
import os
import pty
import queue
import re
import signal
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from click.testing import CliRunner

from lightcone.cli.commands import main
from lightcone.engine import view

_URL = re.compile(r"http://127\.0\.0\.1:(\d+)/lightcone/view(?:\?token=(\w+))?")

_SERVE = (
    "import sys; from lightcone.engine.view import ViewApp; "
    "ViewApp.initialize_server(argv=[], root_dir=sys.argv[1]).start()"
)


@dataclass
class Server:
    process: subprocess.Popen[bytes]
    base: str
    token: str

    def get(
        self, path: str, *, token: bool = True, headers: dict[str, str] | None = None,
        method: str = "GET",
    ) -> tuple[int, bytes]:
        """Request *path*, returning the status and body without following redirects."""
        request = urllib.request.Request(self.base + path, method=method, headers=headers or {})
        if token:
            request.add_header("Authorization", f"token {self.token}")
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(request, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def _project(root: Path) -> Path:
    (root / "universes").mkdir(parents=True)
    (root / "results" / "baseline").mkdir(parents=True)
    (root / "data").mkdir()
    (root / ".git").mkdir()
    (root / "astra.yaml").write_text("name: viewed\n")
    (root / "universes" / "baseline.yaml").write_text("id: baseline\n")
    (root / "results" / "baseline" / "fit.json").write_text('{"h0": 70}\n')
    (root / "results" / "baseline" / ".fit.manifest.json").write_text("{}\n")
    (root / "data" / "catalog.csv").write_text("z,mu\n0.1,38.3\n")
    (root / ".git" / "config").write_text("[core]\n")
    return root


def _hostile_jupyter_config(directory: Path) -> dict[str, str]:
    """A Jupyter configuration that would undo every guarantee, were it read."""
    directory.mkdir()
    (directory / "jupyter_server_config.json").write_text(
        json.dumps(
            {
                "IdentityProvider": {"token": ""},
                "ServerApp": {
                    "token": "",
                    "password": "",
                    "allow_remote_access": True,
                    "allow_unauthenticated_access": True,
                    "jpserver_extensions": {"jupyter_server_terminals": True},
                },
                "ContentsManager": {"allow_hidden": True},
            }
        )
    )
    return {
        "JUPYTER_CONFIG_DIR": str(directory),
        "JUPYTER_CONFIG_PATH": str(directory),
        "JUPYTER_SERVER_ALLOW_UNAUTHENTICATED_ACCESS": "true",
    }


def _start(root: Path, scratch: Path, **popen: object) -> Server:
    env = {
        **os.environ,
        **_hostile_jupyter_config(scratch / "jupyter-config"),
        "JUPYTER_RUNTIME_DIR": str(scratch / "runtime"),
        "JUPYTER_DATA_DIR": str(scratch / "data"),
    }
    process = subprocess.Popen(
        [sys.executable, "-c", _SERVE, str(root)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **popen,  # type: ignore[call-overload]
    )
    lines: queue.Queue[str] = queue.Queue()
    log: list[str] = []

    def pump() -> None:
        assert process.stdout is not None
        for raw in process.stdout:
            lines.put(raw.decode(errors="replace"))

    threading.Thread(target=pump, daemon=True).start()
    while True:
        try:
            line = lines.get(timeout=30)
        except queue.Empty:
            process.kill()
            pytest.fail("the server printed no URL within 30 s:\n" + "".join(log))
        log.append(line)
        if match := _URL.search(line):
            return Server(process, f"http://127.0.0.1:{match[1]}", match[2] or "")


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Server]:
    scratch = tmp_path_factory.mktemp("view")
    running = _start(_project(scratch / "project"), scratch)
    yield running
    running.process.terminate()
    running.process.wait(timeout=10)


# ---- the CLI ---------------------------------------------------------------


def test_view_needs_an_analysis_in_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["view"])
    assert result.exit_code == 1
    assert "no astra.yaml" in result.output


def test_view_refuses_an_installation_without_its_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "astra.yaml").write_text("name: viewed\n")
    monkeypatch.setattr(view, "STATIC", tmp_path / "missing")
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["view"])
    assert result.exit_code == 1
    assert "not built" in result.output


# ---- the server ------------------------------------------------------------


def test_the_token_survives_a_configuration_that_empties_it(server: Server) -> None:
    assert len(server.token) >= 32


def test_nothing_is_served_without_the_token(server: Server) -> None:
    for path in ("/lightcone/view", "/files/astra.yaml"):
        status, _ = server.get(path, token=False)
        assert status == 302  # to the login page
    status, _ = server.get("/lightcone/view/api/stat?path=astra.yaml", token=False)
    assert status == 403


def test_the_page_names_its_bundle_and_its_routes(server: Server) -> None:
    status, body = server.get("/lightcone/view")
    assert status == 200
    page = body.decode()
    assert "/static/lightcone_view/main.js" in page
    assert "/static/lightcone_view/main.css" in page
    assert 'data-api="/lightcone/view/api"' in page
    assert 'data-files="/files/"' in page


def test_the_routes_answer_what_the_sdk_reader_asks(server: Server) -> None:
    status, body = server.get("/lightcone/view/api/stat?path=results/baseline/fit.json")
    entry = json.loads(body)
    assert (status, entry["type"], entry["size"]) == (200, "file", len('{"h0": 70}\n'))
    assert isinstance(entry["modifiedAtMs"], int)
    assert json.loads(server.get("/lightcone/view/api/stat?path=universes")[1]) == {
        "type": "directory"
    }
    assert server.get("/lightcone/view/api/stat?path=results/baseline/absent.png") == (
        200, b"null"
    )
    assert json.loads(server.get("/lightcone/view/api/list?path=universes")[1]) == [
        {"name": "baseline.yaml", "type": "file"}
    ]
    assert server.get("/files/astra.yaml") == (200, b"name: viewed\n")


def test_the_project_is_readable_whole(server: Server) -> None:
    """Declared inputs included: with the token required, the person who
    holds it may see the data the analysis reads."""
    assert server.get("/files/data/catalog.csv") == (200, b"z,mu\n0.1,38.3\n")


def test_hidden_paths_are_never_served(server: Server) -> None:
    assert server.get("/files/.git/config")[0] == 404
    assert server.get("/files/results/baseline/.fit.manifest.json")[0] == 404
    assert server.get("/lightcone/view/api/stat?path=.git") == (200, b"null")
    names = {e["name"] for e in json.loads(server.get("/lightcone/view/api/list?path=")[1])}
    assert names == {"astra.yaml", "data", "results", "universes"}


def test_nothing_leaves_the_project_root(server: Server) -> None:
    for path in ("/files/..%2F..%2Fetc%2Fpasswd", "/lightcone/view/api/stat?path=../.."):
        status, body = server.get(path)
        assert status in (403, 404) or body == b"null"


@pytest.mark.parametrize(
    "path",
    [
        "/api/contents", "/api/kernels", "/api/sessions", "/api/kernelspecs",
        "/api/terminals", "/terminals/websocket/1", "/api/config/notebook",
        "/nbconvert/html/astra.yaml", "/view/astra.yaml", "/edit/astra.yaml", "/lab", "/tree",
    ],
)
def test_no_service_runs_code_or_writes(server: Server, path: str) -> None:
    assert server.get(path)[0] == 404


def test_files_are_read_only(server: Server) -> None:
    status, _ = server.get("/files/new.txt", method="PUT")
    assert status == 405
    assert server.get("/api/contents", method="POST")[0] == 404  # would create `untitled`


def test_a_foreign_host_is_refused(server: Server) -> None:
    """A page on another origin cannot reach the server by rebinding DNS."""
    status, _ = server.get("/lightcone/view", headers={"Host": "attacker.example:8888"})
    assert status == 403


def test_an_interrupt_stops_the_server_without_asking(tmp_path: Path) -> None:
    """Jupyter asks for confirmation on a terminal's Ctrl-C, and resumes
    when nobody answers — a hang for an agent. A pty makes it ask."""
    primary, secondary = pty.openpty()
    try:
        running = _start(_project(tmp_path / "project"), tmp_path, stdin=secondary)
        running.process.send_signal(signal.SIGINT)
        try:
            running.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            running.process.kill()
            pytest.fail("the server is still running after Ctrl-C")
    finally:
        os.close(primary)
        os.close(secondary)
