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

The token reads and nothing more. The page only reads, so the server keeps
two of Jupyter's services, the Contents API and ``/files/``, and Jupyter's
authorizer refuses every write; there are no kernels, sessions or terminals
for the token to run code with, and no config to write. Two writes Jupyter
makes outside its authorizer are switched off as well: the login form's
password change, and the notebook trust store a read would create.

Run as ``python -m lightcone.app``; ``lc view`` is what starts it.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from jupyter_server.auth.authorizer import Authorizer
from jupyter_server.auth.identity import PasswordIdentityProvider, User
from jupyter_server.base.handlers import JupyterHandler
from jupyter_server.serverapp import ServerApp
from jupyter_server.services.contents.largefilemanager import AsyncLargeFileManager
from jupyterlab_server import LabServerApp
from nbformat.sign import NotebookNotary
from traitlets import Bool, default

from lightcone.app import routes
from lightcone.engine.serve import BUNDLE

_HERE = Path(__file__).parent


def _jupyter_server_extension_points() -> list[dict[str, Any]]:
    return [{"module": __name__, "app": LightconeApp}]


class ReadOnly(Authorizer):
    """Let the token read and nothing else."""

    def is_authorized(
        self, handler: JupyterHandler, user: User, action: str, resource: str
    ) -> bool:
        """Allow ``read``; refuse ``write`` and ``execute`` with a 403."""
        return action == "read"


class TokenLogin(PasswordIdentityProvider):
    """Jupyter's token login, without the form's offer to set a password."""

    # Accepting one writes a Jupyter config file and lets that password in
    # beside the token. Not configurable, so no config can turn it back on.
    allow_password_change = Bool(False)


class TrustInMemory(AsyncLargeFileManager):
    """Jupyter's contents manager, with notebook trust kept in memory."""

    @default("notary")
    def _notary_default(self) -> NotebookNotary:
        # Reading a notebook checks its signature, which by default creates
        # Jupyter's signature database and secret in the user's data directory.
        return NotebookNotary(  # type: ignore[no-untyped-call]
            parent=self, db_file=":memory:", secret=os.urandom(32)
        )


class LightconeServer(ServerApp):
    """Jupyter's server with only what the page reads through."""

    # jupyter_server's documented way to expose a subset of its services; its
    # own tuple's inferred type is what mypy objects to.
    default_services = ("contents", "files")  # type: ignore[assignment]


class LightconeApp(LabServerApp):
    """Serve the Lightcone front end and nothing else."""

    serverapp_class = LightconeServer
    name = "lightcone"
    app_name = "Lightcone"
    # The page's `?v=`. Jupyter serves static/ as immutable by path, so this
    # is all that makes a browser fetch a rebuilt bundle: its bytes, not the
    # package version, which a development build does not move.
    app_version = hashlib.sha256(
        BUNDLE.read_bytes() if BUNDLE.is_file() else b""
    ).hexdigest()[:16]
    default_url = "/lightcone"
    app_url = "/lightcone"
    serverapp_config = {
        "jpserver_extensions": {"jupyter_live_content": True},
        # jupyter_server still serves a handler that declares no auth rule to
        # anyone; refused instead, every route needs the token, ours included.
        "allow_unauthenticated_access": False,
        "authorizer_class": ReadOnly,
        "identity_provider_class": TokenLogin,
        "contents_manager_class": TrustInMemory,
    }
    static_dir = str(BUNDLE.parent)
    templates_dir = str(_HERE / "templates")
    # The page loads no lab extension, so none is advertised to it.
    labextensions_path: list[str] = []

    def initialize_handlers(self) -> None:
        """Add the app's routes to the page's own."""
        super().initialize_handlers()
        # Ahead of the catch-all jupyterlab_server ends its own list with
        # (`/lightcone/.*`), which would answer for them with the page.
        self.handlers[:0] = routes.HANDLERS
