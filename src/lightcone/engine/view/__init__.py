"""The viewer: a Jupyter Server app that shows the project's ASTRA inventory.

Jupyter Server supplies everything a server on a shared machine needs — a
random token exchanged for a cookie, the ``Host`` check against DNS
rebinding, port retries, a private runtime file. What this module adds is
the restriction: the server reads the project and does nothing else. No
kernels, no terminals, no service that writes; the page resolves the
analysis in the browser, reading files through Jupyter's own ``/files/``
and metadata through two routes of ours.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from string import Template
from typing import Any

from jupyter_server.base.handlers import APIHandler, JupyterHandler
from jupyter_server.extension.application import ExtensionApp
from jupyter_server.extension.handler import ExtensionHandlerMixin
from jupyter_server.serverapp import ServerApp
from jupyter_server.utils import ensure_async, url_path_join
from tornado import web
from traitlets import default

from lightcone.engine.project import ProjectError

_URL = "/lightcone/view"
_HERE = Path(__file__).parent
#: Built from `view/` at the repository root; package data in a wheel.
STATIC = _HERE / "static"


class ViewServer(ServerApp):
    """A Jupyter server that can read the project and nothing else."""

    # Login only. Left out: kernels, sessions and kernelspecs (they run
    # code), contents and config (they write), nbconvert, events, the file
    # views. Jupyter's `/files/` is not a service — the contents manager adds
    # it — and stays: it is read-only and refuses hidden paths, so `.git`
    # and `.venv` are never served. (A subset is how Jupyter documents
    # overriding this; mypy reads the base's literal as a fixed length.)
    default_services = ("auth",)  # type: ignore[assignment]

    @property
    def config_file_paths(self) -> list[str]:
        """Read no Jupyter configuration file, the user's or the environment's.

        Nothing installed on the machine can then turn the token off or
        load another extension into this server.
        """
        return []

    @default("allow_unauthenticated_access")
    def _deny_unauthenticated_access(self) -> bool:
        return False

    @default("answer_yes")
    def _stop_without_asking(self) -> bool:
        # Ctrl-C otherwise asks for confirmation on the terminal.
        return True


class ViewApp(ExtensionApp):
    """The app `lc view` launches: one page and two metadata routes."""

    name = "lightcone_view"
    extension_url = _URL
    load_other_extensions = False
    serverapp_class = ViewServer
    static_paths = [str(STATIC)]

    @classmethod
    def get_extension_package(cls) -> str:
        """Name this module, not the ``lightcone.engine`` Jupyter would infer."""
        return __name__

    @default("open_browser")
    def _no_browser(self) -> bool:
        # On a headless login node `webbrowser` can resolve to a console
        # browser, which would take over the terminal. The URL is logged.
        return False

    def initialize_handlers(self) -> None:
        """Route the page and the metadata the SDK's project reader needs."""
        self.handlers.extend(
            [
                (_URL, _Page),
                (url_path_join(_URL, "api", "stat"), _Stat),
                (url_path_join(_URL, "api", "list"), _List),
            ]
        )


def _jupyter_server_extension_points() -> list[dict[str, Any]]:
    return [{"module": __name__, "app": ViewApp}]


def serve(root: Path) -> None:
    """Serve *root*'s inventory until interrupted.

    Args:
        root: The project directory; the server's root, and all it reads.

    Raises:
        ProjectError: *root* has no ``astra.yaml``, or the page was never
            built into this installation.
    """
    if not (root / "astra.yaml").is_file():
        raise ProjectError(f"{root} has no astra.yaml — there is no analysis here to view.")
    if not (STATIC / "main.js").is_file():
        raise ProjectError(
            f"The viewer's page is not built into this installation ({STATIC}). "
            "In a source checkout: `npm ci && npm run build` in `view/`."
        )
    ViewApp.initialize_server(argv=[], root_dir=str(root)).start()


class _Page(ExtensionHandlerMixin, JupyterHandler):
    @web.authenticated
    def get(self) -> None:
        page = Template((_HERE / "page.html").read_text(encoding="utf-8"))
        values = {
            "title": f"{Path(self.settings['server_root_dir']).name} · lc view",
            "css": self.static_url("main.css"),
            "js": self.static_url("main.js"),
            "api": url_path_join(self.base_url, _URL, "api"),
            "files": url_path_join(self.base_url, "files") + "/",
        }
        self.finish(page.substitute({k: html.escape(v) for k, v in values.items()}))


class _Stat(ExtensionHandlerMixin, APIHandler):
    """What the SDK's ``stat`` asks: a file's size and mtime, "a directory", or null.

    Absence is an answer here, not an error: the SDK stats every declared
    output, and an unmaterialized one would otherwise be a 404 in the
    browser console and the server log for each.
    """

    @web.authenticated
    async def get(self) -> None:
        path = self.get_query_argument("path")
        try:
            model = await ensure_async(self.contents_manager.get(path, content=False))
        except web.HTTPError as error:
            if error.status_code != 404:
                raise
            self.finish("null")
            return
        if model["type"] == "directory":
            self.finish(json.dumps({"type": "directory"}))
            return
        modified = int(model["last_modified"].timestamp() * 1000)
        self.finish(json.dumps({"type": "file", "size": model["size"], "modifiedAtMs": modified}))


class _List(ExtensionHandlerMixin, APIHandler):
    """What the SDK's ``readDirectory`` asks: bare child names, hidden ones omitted."""

    @web.authenticated
    async def get(self) -> None:
        path = self.get_query_argument("path")
        model = await ensure_async(
            self.contents_manager.get(path, content=True, type="directory")
        )
        entries = [
            {"name": child["name"], "type": "directory" if child["type"] == "directory" else "file"}
            for child in sorted(model["content"], key=lambda child: child["name"])
        ]
        self.finish(json.dumps(entries))
