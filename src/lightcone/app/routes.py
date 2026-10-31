"""The app's own routes: what the page needs and the Contents API cannot give.

An output's recorded run, read by lc's own manifest reader, so the page
never parses lc's format and the server never has to open dotfiles to it;
every output's state, classified by the engine exactly as ``lc status``
does, so the page holds no copy of that rule; and the cited papers in
astra's paper cache, which lies outside the
project (``astra paper add`` fills it, and quotes are verified against it).
The paper routes are adapted from jupyterlab-lightcone's ``routes.py``
(BSD-3-Clause, Lightcone Research), without its fetch route: nothing here
writes the cache. Every route needs the token, like the rest of the server.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from astra.papers.cache import CachedPaper, PaperCache
from jupyter_core.utils import ensure_async
from jupyter_server.base.handlers import APIHandler
from jupyter_server.utils import url_path_join
from tornado import web

from lightcone.app.files import ProjectFileHandler, open_fd
from lightcone.engine import assets, materialize
from lightcone.engine.project import ProjectError, current_project

_PAPERS = PaperCache().cache_dir


def _cached() -> dict[str, CachedPaper]:
    """Every cached paper by DOI as the SDK normalizes it, unversioned first."""
    papers: dict[str, CachedPaper] = {}
    for paper in PaperCache().list_papers():
        doi = paper.metadata.doi.strip().lower()
        if doi not in papers or paper.metadata.version is None:
            papers[doi] = paper
    return papers


class PapersHandler(APIHandler):
    """Title, authors and PDF of the cited DOIs the cache holds."""

    @web.authenticated
    async def get(self) -> None:
        """Answer the repeated ``doi`` arguments from one scan of the cache."""
        cached = await asyncio.to_thread(_cached)
        papers: dict[str, dict[str, str]] = {}
        for doi in self.get_arguments("doi"):
            if (paper := cached.get(doi)) is None:
                continue
            pdf = paper.pdf_path.relative_to(_PAPERS).as_posix()
            metadata = {
                "title": paper.metadata.title,
                "authors": ", ".join(paper.metadata.authors or []),
                "pdfUrl": url_path_join(self.base_url, "lightcone/papers", pdf),
            }
            papers[doi] = {key: value for key, value in metadata.items() if value}
        self.set_header("Cache-Control", "private, no-store")
        self.finish({"papers": papers})


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
            report = await asyncio.to_thread(lambda: materialize.status(current_project(root)))
        except ProjectError as error:
            raise web.HTTPError(422, str(error)) from error
        outputs = {o.output: {"state": o.status, "detail": o.why} for o in report.outputs}
        self.set_header("Cache-Control", "private, no-store")
        self.finish({"outputs": outputs})


HANDLERS = [
    ("/lightcone/api/papers", PapersHandler),
    ("/lightcone/api/run", RunHandler),
    ("/lightcone/api/status", StatusHandler),
    # Jupyter's own file handler: ranges for pdf.js, 304s on reopening.
    ("/lightcone/papers/(.*)", ProjectFileHandler, {"path": str(_PAPERS)}),
]
