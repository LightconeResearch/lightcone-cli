"""The Lightcone app: a JupyterLab front end over one project's ``astra.yaml``.

Inspired from JupyterLab's ``examples/app`` (BSD-3-Clause, Copyright (c)
Jupyter Development Team): a ``LabServerApp`` serving a page that builds
``JupyterLab`` from a chosen plugin list. The browser reads the project
through Jupyter's Contents API and resolves the spec itself, so this app
adds no routes of its own.

Run as ``python -m lightcone.app``; ``lc serve`` is what starts it.
"""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path
from typing import Any

from jupyter_core.paths import jupyter_config_dir
from jupyterlab_server import LabServerApp

_HERE = Path(__file__).parent


def _jupyter_server_extension_points() -> list[dict[str, Any]]:
    return [{"module": __name__, "app": LightconeApp}]


class LightconeApp(LabServerApp):
    """Serve the Lightcone front end and nothing else."""

    name = "lightcone"
    app_name = "Lightcone"
    app_version = version("lightcone-cli")
    extension_url = "/lightcone"
    default_url = "/lightcone"
    app_url = "/lightcone"
    load_other_extensions = False
    static_dir = str(_HERE / "static")
    templates_dir = str(_HERE / "templates")
    schemas_dir = str(_HERE / "schemas")
    themes_dir = str(_HERE / "themes")
    # The bundle loads no other lab extension, so none is advertised to it.
    labextensions_path: list[str] = []
    # JupyterLab's own: unset, settings would be written into the project.
    user_settings_dir = str(Path(jupyter_config_dir()) / "lab" / "user-settings")
