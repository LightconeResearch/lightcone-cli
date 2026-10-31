"""``lc view``'s lifecycle, against a real detached jupyter server.

The page itself needs no front-end build, so the lifecycle runs everywhere.
Whether the built front end is actually served is gated like the other
smoke suites: a checkout without ``ui/`` built skips, and
``LC_UI_TESTS_REQUIRED=1`` turns that skip into a failure.
"""

from __future__ import annotations

import asyncio
import contextlib
import http.cookiejar
import json
import os
import signal
import stat
import sys
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import psutil
import pytest
from astra.papers.cache import PaperCache

from lightcone.engine import assets
from lightcone.engine import serve as engine
from lightcone.engine.project import ProjectError

REQUIRED_ENV = "LC_UI_TESTS_REQUIRED"
_BUNDLE = engine.BUNDLE


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Serve state under tmp_path; every server started is stopped. The
    lifecycle needs no front-end build, so the check for one is satisfied."""
    monkeypatch.setattr(engine, "_STATE", tmp_path / "serve")
    page = tmp_path / "bundle.js"
    page.touch()
    monkeypatch.setattr(engine, "BUNDLE", page)
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


def _request(
    url: str,
    method: str = "GET",
    token: str = "",
    body: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, str]:
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args: object, **kwargs: object) -> None:
            return None

    request = urllib.request.Request(
        url,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={**({"Authorization": f"token {token}"} if token else {}), **(headers or {})},
    )
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=10) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, ""


def test_a_directory_without_a_spec_is_refused(state: Path, tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="no astra.yaml"):
        engine.serve(tmp_path)
    assert not state.exists()


def test_an_installation_without_a_built_page_is_refused(
    state: Path, project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rather than a working URL to a blank page."""
    monkeypatch.setattr(engine, "BUNDLE", tmp_path / "unbuilt" / "bundle.js")
    with pytest.raises(ProjectError, match="npm run build"):
        engine.serve(project)
    assert not state.exists()


def test_a_runtime_file_counts_only_while_its_process_is_the_server(
    tmp_path: Path, project: Path
) -> None:
    """A pid outlives its server after a reboot or a crash, and a reused one
    is alive; and one bad file must not break every later call."""
    reused = json.dumps({"pid": os.getpid(), "root_dir": os.path.abspath(project)})
    for n, text in enumerate((reused, "{}", "[]", '{"pid": "1"}')):
        (tmp_path / f"jpserver-{n}.json").write_text(text)
    assert engine._running(tmp_path, project) is None
    assert not list(tmp_path.glob("jpserver-*.json"))


def test_the_first_call_starts_a_server_and_the_next_reuses_it(
    state: Path, project: Path
) -> None:
    first = engine.serve(project)
    assert first.started
    status, page = _request(first.url)
    assert status == 200
    assert "<title>Lightcone</title>" in page

    again = engine.serve(project, "decisions.binning")
    assert not again.started
    assert again.pid == first.pid
    assert again.url.endswith("&record=decisions.binning")


def test_a_page_reconnects_only_to_a_server_for_its_own_project(
    state: Path, project: Path, tmp_path: Path
) -> None:
    """A page outlives its server, and the next server may take the port the
    first one freed: a later server for the same project accepts the page's
    login cookie, another project's refuses it. Each request names the first
    server's host, as the open page's would."""
    other = tmp_path / "other"
    other.mkdir()
    (other / "astra.yaml").write_text("name: other\n")
    first = engine.serve(project)
    with urllib.request.urlopen(first.url) as page:  # the token logs the page in
        cookie = next(
            value.split(";", 1)[0]
            for value in page.headers.get_all("Set-Cookie")
            if value.startswith("username-")
        )
    page_host = urlsplit(first.url).netloc
    os.kill(first.pid, signal.SIGTERM)
    psutil.Process(first.pid).wait(timeout=10)

    def accepts(served: engine.Served) -> bool:
        url = urlsplit(served.url)
        status, _ = _request(
            f"{url.scheme}://{url.netloc}/api/contents/astra.yaml",
            headers={"Cookie": cookie, "Host": page_host},
        )
        return status == 200

    assert accepts(engine.serve(project))
    assert not accepts(engine.serve(other))


@pytest.fixture
def papers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PaperCache:
    """astra's paper cache, where the server's $HOME puts it. The project is
    under that $HOME too, as a project usually is — which is where jupyter
    starts abbreviating paths to `~`."""
    monkeypatch.setenv("HOME", str(tmp_path))
    return PaperCache(tmp_path / ".cache" / "astra" / "papers")


@pytest.fixture
def served(state: Path, project: Path, papers: PaperCache) -> tuple[str, str]:
    """The server's root URL and its token."""
    url = urlsplit(engine.serve(project).url)
    (token,) = parse_qs(url.query)["token"]
    return f"{url.scheme}://{url.netloc}", token


def test_an_outputs_run_comes_from_its_manifest(served: tuple[str, str], project: Path) -> None:
    """Read by lc's own manifest reader, so the page parses no lc format and
    the Contents API never has to serve a dotfile."""
    base, token = served
    output = project / "results" / "baseline" / "best_fit.json"
    output.parent.mkdir(parents=True)
    output.write_text("{}")
    manifest = assets.Manifest(
        output_id="best_fit",
        universe_id="baseline",
        recipe="python fit.py",
        definition_version="sha256:d",
        env_version="sha256:e",
        data_version="sha256:x",
        decisions={},
        input_versions={"catalog": "sha256:c"},
        git_sha="abc123",
        git_remote="",
        lc_version="0.5.0",
        hermeticity={},
        finished_at="2026-10-31T09:00:00.000Z",
    )
    assets.write(assets.manifest_path(output), manifest)
    api = f"{base}/lightcone/api/run?token={token}&path="
    status, body = _request(f"{api}results/baseline/best_fit.json")
    assert status == 200
    assert json.loads(body)["run"] == {
        "finishedAt": "2026-10-31T09:00:00.000Z",
        "gitRevision": "abc123",
        "recipe": "python fit.py",
        "environment": "sha256:e",
        "cliVersion": "0.5.0",
        "inputVersions": {"catalog": "sha256:c"},
    }
    (output.parent / "unrecorded.json").write_text("{}")
    assert json.loads(_request(f"{api}results/baseline/unrecorded.json")[1]) == {"run": None}
    # Not a file in the project: what the Contents API would refuse is refused.
    for path in ("results/baseline/absent.json", "../outside.json", "", "results/baseline"):
        assert _request(f"{api}{path}")[0] in (400, 404), path
    # The manifest stays a dotfile to everything else.
    hidden = f"{base}/api/contents/results/baseline/.best_fit.manifest.json?token={token}"
    assert _request(hidden)[0] == 404


def test_a_run_record_beyond_a_symlink_is_not_read(
    served: tuple[str, str], project: Path, tmp_path: Path
) -> None:
    """The contents manager follows symlinks, so the sidecar the route would
    read is checked where it really is."""
    base, token = served
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "fit.json").write_text("{}")
    (elsewhere / ".fit.manifest.json").write_text("{}")
    (project / "results").symlink_to(elsewhere)
    status, _ = _request(f"{base}/lightcone/api/run?token={token}&path=results/fit.json")
    assert status == 404


def test_the_token_reads_and_runs_nothing(served: tuple[str, str], project: Path) -> None:
    """The page only reads: no kernel, session or terminal to run code with,
    no config to write, and the Contents API refuses every write."""
    base, token = served
    spec = project / "astra.yaml"
    before = spec.read_text()
    for method, route in (
        ("POST", "/api/kernels"),
        ("POST", "/api/sessions"),
        ("POST", "/api/terminals"),
        ("PUT", "/api/config/notebook"),
    ):
        assert _request(f"{base}{route}", method, token)[0] == 404, route
    overwrite = {"type": "file", "format": "text", "content": "name: overwritten\n"}
    for method, route, body in (
        ("PUT", "/api/contents/astra.yaml", overwrite),
        ("PUT", "/api/contents/new.yaml", overwrite),
        ("PATCH", "/api/contents/astra.yaml", {"path": "renamed.yaml"}),
        ("DELETE", "/api/contents/astra.yaml", None),
        ("POST", "/api/contents", {"type": "directory"}),
        ("POST", "/api/contents", {"copy_from": "astra.yaml"}),
        ("POST", "/api/contents/astra.yaml/checkpoints", None),
    ):
        assert _request(f"{base}{route}", method, token, body)[0] == 403, (method, route, body)
    assert spec.read_text() == before
    assert sorted(path.name for path in project.iterdir()) == ["astra.yaml"]
    # Reading is what it is for.
    assert _request(f"{base}/api/contents/astra.yaml", token=token)[0] == 200


def test_the_login_form_sets_no_password(served: tuple[str, str]) -> None:
    """Jupyter's login form offers to trade the token for a password, which
    it writes to a config file and then accepts; the token stays the only
    way in."""
    base, token = served

    def login(**form: str) -> bool:
        jar = http.cookiejar.CookieJar()
        browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        browser.open(f"{base}/login").read()
        xsrf = next(cookie.value for cookie in jar if cookie.name == "_xsrf")
        with contextlib.suppress(urllib.error.HTTPError):
            browser.open(f"{base}/login", data=urlencode({**form, "_xsrf": xsrf}).encode())
        try:
            return bool(browser.open(f"{base}/api/contents/astra.yaml").status == 200)
        except urllib.error.HTTPError:
            return False

    assert login(password=token, new_password="chosen")
    assert not login(password="chosen")


def test_reading_a_notebook_leaves_jupyters_data_directory_alone(
    state: Path, project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A notebook read through the Contents API has its trust signature
    checked, which would create Jupyter's signature database and secret."""
    data = tmp_path / "jupyter-data"
    monkeypatch.setenv("JUPYTER_DATA_DIR", str(data))
    notebook = {"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
    (project / "notes.ipynb").write_text(json.dumps(notebook))
    url = urlsplit(engine.serve(project).url)
    (token,) = parse_qs(url.query)["token"]
    status, _ = _request(f"{url.scheme}://{url.netloc}/api/contents/notes.ipynb", token=token)
    assert status == 200
    assert not list(data.glob("*"))


def test_a_cited_paper_comes_from_astras_cache(
    served: tuple[str, str], papers: PaperCache
) -> None:
    base, token = served
    papers.add("10.1093/MNRAS/stv1198", b"%PDF-1.4 gd1", title="Gaia", authors=["A. B.", "C. D."])
    # The page sends DOIs as the SDK normalizes them; astra stored this one as written.
    query = f"doi=10.1093/mnras/stv1198&doi=10.1/absent&token={token}"
    status, body = _request(f"{base}/lightcone/api/papers?{query}")
    assert status == 200
    found = json.loads(body)["papers"]
    assert found.keys() == {"10.1093/mnras/stv1198"}
    paper = found["10.1093/mnras/stv1198"]
    assert (paper["title"], paper["authors"]) == ("Gaia", "A. B., C. D.")
    # Served by Jupyter's own file handler: ranges for pdf.js.
    request = urllib.request.Request(
        f"{base}{paper['pdfUrl']}?token={token}", headers={"Range": "bytes=0-3"}
    )
    with urllib.request.urlopen(request) as pdf:
        assert (pdf.status, pdf.headers["Content-Type"]) == (206, "application/pdf")
        assert pdf.read() == b"%PDF"


def test_every_route_needs_the_token(served: tuple[str, str]) -> None:
    """The app's routes reach past the project, to astra's cache, so they
    are held to the token like the page and the Contents API are."""
    base, _ = served
    routes = (
        "/lightcone",
        "/lightcone/api/papers?doi=10.1/x",
        "/lightcone/api/run?path=x",
        "/lightcone/papers/x/paper.pdf",
    )
    for route in routes:
        assert _request(f"{base}{route}")[0] in (302, 403), route


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
    _, page = _request(served.url)
    for asset in ("bundle.js", "favicon.svg"):
        assert f"/static/lightcone/{asset}?v=" in page
        status, _ = _request(f"{base}/static/lightcone/{asset}")
        assert status == 200
