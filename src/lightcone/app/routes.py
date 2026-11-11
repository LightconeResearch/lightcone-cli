"""The app's own routes: what the page needs and the Contents API cannot give.

A result's recorded run, read from ASTRA's record and lc's manifest, so
the page never parses either format and the server never has to open
dotfiles to it; every result's state, classified by the engine exactly as
``lc status`` does, so the page holds no copy of that rule; and the cited
papers in astra's cache, which lies outside the project (``astra paper
add`` fills it, and quotes are verified against it). Nothing here writes
the cache. Every route needs the token, like the rest of the server.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from astra.evidence import papers
from astra.records import Record
from jupyter_core.utils import ensure_async
from jupyter_server.base.handlers import APIHandler
from jupyter_server.utils import url_path_join
from tornado import web
from tornado.httputil import url_concat

from lightcone.app.files import open_fd
from lightcone.engine import assets, materialize
from lightcone.engine.project import ProjectError, current_project


class PapersHandler(APIHandler):
    """Which of the cited DOIs the cache holds a PDF of, and where to get it."""

    @web.authenticated
    async def get(self) -> None:
        """Answer the repeated ``doi`` arguments from the cache."""
        found: dict[str, dict[str, str]] = {}
        for doi in self.get_arguments("doi"):
            if await asyncio.to_thread(papers.get, doi, None) is not None:
                url = url_path_join(self.base_url, "lightcone/api/paper")
                found[doi] = {"pdfUrl": url_concat(url, {"doi": doi})}
        self.set_header("Cache-Control", "private, no-store")
        self.finish({"papers": found})


class PaperHandler(APIHandler):
    """The PDF the cache holds for one DOI."""

    @web.authenticated
    async def get(self) -> None:
        """Send the paper the ``doi`` argument names, or a 404."""
        pdf = await asyncio.to_thread(papers.get, self.get_query_argument("doi"), None)
        if pdf is None:
            raise web.HTTPError(404)
        self.set_header("Cache-Control", "private, no-store")
        self.finish(pdf, set_content_type="application/pdf")


class RunHandler(APIHandler):
    """The run recorded for one result, or ``null`` when there is none."""

    @web.authenticated
    async def get(self) -> None:
        """Read the record and manifest of the result the ``path`` argument names."""
        # Whether the path is a file in the project is the contents manager's
        # to say, as it does for /files/: outside the root, hidden, absent or
        # a directory is refused before anything is read.
        cm: Any = self.contents_manager  # its methods are untyped
        path = self.get_query_argument("path")
        if not cm.allow_hidden and await ensure_async(cm.is_hidden(path)):
            raise web.HTTPError(404)
        model = await ensure_async(cm.get(path, type="file", content=False))
        root = Path(cm.root_dir).resolve()

        def read(name: str) -> str | None:
            # Both are hidden, but use the same confined opener as artifacts.
            try:
                with open_fd(root, root / name, hidden=True) as fd:
                    with os.fdopen(fd, "r", closefd=False) as file:
                        return file.read()
            except (web.HTTPError, UnicodeDecodeError):
                return None

        run: dict[str, Any] | None = None
        for record_path, manifest_path in assets.sidecars(model["path"]):
            if (text := read(record_path)) is None:
                continue
            try:
                record = Record.from_json(json.loads(text))
            except (ValueError, KeyError, TypeError):
                continue
            manifest = assets.loads(read(manifest_path) or "")
            run = {
                "finishedAt": record.finished or "",
                "gitRevision": record.commit,
                "recipe": manifest.recipe if manifest else "",
                "environment": record.environment,
                "cliVersion": record.runner.version,
                "inputVersions": {item.entry: item.content_hash for item in record.inputs},
            }
            break
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
    ("/lightcone/api/paper", PaperHandler),
    ("/lightcone/api/run", RunHandler),
    ("/lightcone/api/status", StatusHandler),
]
