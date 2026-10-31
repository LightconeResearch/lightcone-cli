"""The Lightcone app: one page over one project's ``astra.yaml``.

Inspired from JupyterLab's ``examples/app`` (BSD-3-Clause, Copyright (c)
Jupyter Development Team): a ``LabServerApp`` serving one page, with its
token, its page config and Jupyter's Contents API. The page reads the
project through that API and resolves the spec itself; the app's own routes
(see ``routes``) answer what that API cannot: an output's recorded run, read
by lc's manifest reader, and the cited papers in astra's cache, outside the
project. It serves no settings or themes: the page has no
JupyterLab interface to configure. The one other extension it loads is
jupyter-live-content, whose WebSocket tells the page when a file it read
changed on disk.

Run as ``python -m lightcone.app``; ``lc view`` is what starts it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from jupyterlab_server import LabServerApp

from lightcone.app import routes

_HERE = Path(__file__).parent
_BUNDLE = _HERE / "static" / "bundle.js"


def _jupyter_server_extension_points() -> list[dict[str, Any]]:
    return [{"module": __name__, "app": LightconeApp}]


class LightconeApp(LabServerApp):
    """Serve the Lightcone front end and nothing else."""

    name = "lightcone"
    app_name = "Lightcone"
    # The page's `?v=`. Jupyter serves static/ as immutable by path, so this
    # is all that makes a browser fetch a rebuilt bundle: its bytes, not the
    # package version, which a development build does not move.
    app_version = hashlib.sha256(
        _BUNDLE.read_bytes() if _BUNDLE.is_file() else b""
    ).hexdigest()[:16]
    default_url = "/lightcone"
    app_url = "/lightcone"
    serverapp_config = {
        "jpserver_extensions": {"jupyter_live_content": True},
        # jupyter_server still serves a handler that declares no auth rule to
        # anyone; refused instead, every route needs the token, ours included.
        "allow_unauthenticated_access": False,
    }
    static_dir = str(_HERE / "static")
    templates_dir = str(_HERE / "templates")
    # The page loads no lab extension, so none is advertised to it.
    labextensions_path: list[str] = []

    def initialize_handlers(self) -> None:
        """Add the app's routes to the page's own."""
        super().initialize_handlers()
        # Ahead of the catch-all jupyterlab_server ends its own list with
        # (`/lightcone/.*`), which would answer for them with the page.
        self.handlers[:0] = routes.HANDLERS
