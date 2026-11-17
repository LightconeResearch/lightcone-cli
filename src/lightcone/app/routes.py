"""The app's own routes: what the page needs and the Contents API cannot give.

An output's recorded run, read by lc's own manifest reader, so the page
never parses lc's format and the server never has to open dotfiles to it;
every output's state, classified by the engine exactly as ``lc status``
does, so the page holds no copy of that rule; and the cited papers in
astra's paper cache, which lies outside the
project (``astra paper add`` fills it, and quotes are verified against it).
A paper is found under the DOI the page asks for, exactly as astra keeps
it and at no version; any other the page reads from arXiv, or links to.
The paper routes are adapted from jupyterlab-lightcone's ``routes.py``
(BSD-3-Clause, Lightcone Research), without its fetch route: nothing here
writes the cache. Every route needs the token, like the rest of the server.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

from astra import api
from jupyter_core.utils import ensure_async
from jupyter_server.base.handlers import APIHandler
from jupyter_server.utils import url_path_join
from tornado import web

from lightcone.app.files import ProjectFileHandler, open_fd
from lightcone.engine import assets, materialize
from lightcone.engine.project import ProjectError, current_project


class PapersHandler(APIHandler):
    """Title, authors and PDF of the cited DOIs the cache holds."""

    @web.authenticated
    async def get(self) -> None:
        """Answer the repeated ``doi`` arguments, each by astra's own lookup."""
        papers: dict[str, dict[str, str]] = {}
        for doi in self.get_arguments("doi"):
            paper = await asyncio.to_thread(api.paper, doi)
            if paper is None:
                continue
            metadata = {
                "title": paper.title,
                "authors": ", ".join(paper.authors),
                "pdfUrl": url_path_join(self.base_url, "lightcone/papers", quote(doi, safe="/")),
            }
            papers[doi] = {key: value for key, value in metadata.items() if value}
        self.set_header("Cache-Control", "private, no-store")
        self.finish({"papers": papers})


class PaperFileHandler(ProjectFileHandler):
    """A cached paper's PDF, by its DOI: ranges for pdf.js, 304s on reopening."""

    # Checked here as well as in Jupyter's handler, so that the cache is not
    # even looked in for a request without the token.
    @web.authenticated
    async def get(  # type: ignore[override]  # the signature of Jupyter's own get
        self, path: str, **kwargs: Any
    ) -> None:
        """Serve the PDF of the paper whose DOI the path is."""
        paper = await asyncio.to_thread(api.paper, path)
        if paper is None:
            raise web.HTTPError(404)
        # Confined to the paper's own folder, which is known only now.
        pdf = Path(paper.pdf_path)
        self.root = str(pdf.parent)
        served = super().get(pdf.name, **kwargs)
        assert served is not None  # none only for a request without the token
        await served


class RunHandler(APIHandler):
    """The run lc recorded for one output, or ``null`` when there is none."""

    @web.authenticated
    async def get(self) -> None:
        """Read the manifest beside the output the ``path`` argument names."""
        # Whether the path is a file in the project is the contents manager's
        # to say, as it does for /files/: outside the root, hidden, absent or
        # a directory is refused before anything is read.
        cm: Any = self.contents_manager  # its methods are untyped
        path = self.get_query_argument("path")
        if not cm.allow_hidden and await ensure_async(cm.is_hidden(path)):
            raise web.HTTPError(404)
        model = await ensure_async(cm.get(path, type="file", content=False))
        # The sidecar is hidden, but uses the same confined opener as artifacts.
        root = Path(cm.root_dir).resolve()
        sidecar = assets.manifest_path(root / model["path"])
        try:
            with open_fd(root, sidecar, hidden=True) as fd:
                with os.fdopen(fd, "r", closefd=False) as file:
                    manifest = assets.loads(file.read())
        except (web.HTTPError, UnicodeDecodeError):
            manifest = None
        run: dict[str, Any] | None = None
        if manifest is not None:
            run = {
                "finishedAt": manifest.finished_at,
                "gitRevision": manifest.git_sha,
                "recipe": manifest.recipe,
                "environment": manifest.env_version,
                "cliVersion": manifest.lc_version,
                "inputVersions": manifest.input_versions,
            }
        self.set_header("Cache-Control", "private, no-store")
        self.finish({"run": run})


class StatusHandler(APIHandler):
    """What state every output is in: the engine's answer, as `lc status` gives it."""

    @web.authenticated
    async def get(self) -> None:
        """Classify every output; a project the engine cannot read is a 422."""
        root = Path(self.contents_manager.root_dir)
        try:
            # Classification hashes the declared inputs, so it runs off the loop.
            report = await asyncio.to_thread(materialize.status, current_project(root))
        except ProjectError as error:
            raise web.HTTPError(422, str(error)) from error
        outputs = {o.output: {"state": o.status, "detail": o.why} for o in report.outputs}
        self.set_header("Cache-Control", "private, no-store")
        # The warnings qualify the verdicts — an unfetched input reads as
        # "changed" without the line saying it is merely not in this clone.
        self.finish({"outputs": outputs, "warnings": report.warnings})


HANDLERS = [
    ("/lightcone/api/papers", PapersHandler),
    ("/lightcone/api/run", RunHandler),
    ("/lightcone/api/status", StatusHandler),
    ("/lightcone/papers/(.*)", PaperFileHandler, {"path": ""}),
]
