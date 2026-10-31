"""The app's own routes: what the page needs and the Contents API cannot give.

An output's recorded run, read by lc's own manifest reader, so the page
never parses lc's format and the server never has to open dotfiles to it;
and the cited papers in astra's paper cache, which lies outside the
project (``astra paper add`` fills it, and quotes are verified against it).
The paper routes are adapted from jupyterlab-lightcone's ``routes.py``
(BSD-3-Clause, Lightcone Research), without its fetch route: nothing here
writes the cache. Every route needs the token, like the rest of the server.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from astra.papers.cache import CachedPaper, PaperCache
from jupyter_core.utils import ensure_async
from jupyter_server.base.handlers import APIHandler, AuthenticatedFileHandler
from jupyter_server.utils import url_path_join
from tornado import web

from lightcone.engine import assets

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
        # The manager follows symlinks, so where the sidecar really is gets
        # its own check.
        root = Path(cm.root_dir).resolve()
        sidecar = assets.manifest_path(root / model["path"]).resolve()
        if not sidecar.is_relative_to(root):
            raise web.HTTPError(404, "the run record is outside the project")
        manifest = assets.read(sidecar)
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


HANDLERS = [
    ("/lightcone/api/papers", PapersHandler),
    ("/lightcone/api/run", RunHandler),
    # Jupyter's own file handler: ranges for pdf.js, 304s on reopening.
    ("/lightcone/papers/(.*)", AuthenticatedFileHandler, {"path": str(_PAPERS)}),
]
