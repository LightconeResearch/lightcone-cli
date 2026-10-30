"""The Lightcone app: one page over one project's ``astra.yaml``.

Inspired from JupyterLab's ``examples/app`` (BSD-3-Clause, Copyright (c)
Jupyter Development Team): a ``LabServerApp`` serving one page, with its
token, its page config and Jupyter's Contents API. The page reads the
project through that API and resolves the spec itself, so this app adds no
routes of its own, and it serves no settings or themes: the page has no
JupyterLab interface to configure. The one other extension it loads is
jupyter-live-content, whose WebSocket tells the page when a file it read
changed on disk.

Run as ``python -m lightcone.app``; ``lc serve`` is what starts it.
"""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path
from typing import Any

from jupyterlab_server import LabServerApp

_HERE = Path(__file__).parent


def _jupyter_server_extension_points() -> list[dict[str, Any]]:
    return [{"module": __name__, "app": LightconeApp}]


class LightconeApp(LabServerApp):
    """Serve the Lightcone front end and nothing else."""

    name = "lightcone"
    app_name = "Lightcone"
    app_version = version("lightcone-cli")
    default_url = "/lightcone"
    app_url = "/lightcone"
    serverapp_config = {"jpserver_extensions": {"jupyter_live_content": True}}
    static_dir = str(_HERE / "static")
    templates_dir = str(_HERE / "templates")
    # The page loads no lab extension, so none is advertised to it.
    labextensions_path: list[str] = []
