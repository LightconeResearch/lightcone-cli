"""Confined reads for the viewer, including symlinks in shared projects."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from jupyter_server.base.handlers import AuthenticatedFileHandler
from jupyter_server.services.contents.largefilemanager import AsyncLargeFileManager
from nbformat.sign import NotebookNotary
from tornado import web
from traitlets import Bool, default

from lightcone.engine import assets


def resolved_path(root: Path, path: Path, *, hidden: bool = False) -> Path:
    """Allow project-local links, including annex objects, but no escaping links."""
    root = root.resolve()
    try:
        relative = path.relative_to(root)
        resolved = path.resolve(strict=True)
        target = resolved.relative_to(root)
        if not hidden:
            if any(part.startswith(".") for part in relative.parts):
                raise ValueError("hidden path")
            # Locked git-annex artifacts point into this private object store.
            parts = (
                target.parts[3:]
                if target.parts[:3] == (".git", "annex", "objects")
                else target.parts
            )
            if any(part.startswith(".") for part in parts):
                raise ValueError("hidden target")
        return resolved
    except (ValueError, OSError, RuntimeError) as error:
        raise web.HTTPError(404, "File is outside the readable project") from error


@contextmanager
def open_fd(
    root: Path, path: Path, *, directory: bool = False, hidden: bool = False
) -> Iterator[int]:
    """Open beneath root without following links swapped in after resolution.

    Directory descriptors anchor every step. O_NONBLOCK and fstat reject
    special files without hanging on a FIFO planted by another project writer.
    """
    root = root.resolve()
    path = resolved_path(root, path, hidden=hidden)
    try:
        with ExitStack() as stack:
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY
            parent = os.open(root, flags)
            stack.callback(os.close, parent)
            parts = path.relative_to(root).parts
            for part in parts[:-1]:
                parent = os.open(part, flags, dir_fd=parent)
                stack.callback(os.close, parent)
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if directory:
                flags |= os.O_DIRECTORY
            fd = os.open(parts[-1] if parts else ".", flags, dir_fd=parent)
            stack.callback(os.close, fd)
            mode = os.fstat(fd).st_mode
            if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
                raise web.HTTPError(404, "Not a regular file")
            yield fd
    except OSError as error:
        raise web.HTTPError(404, "File is unavailable") from error


class ProjectFileHandler(AuthenticatedFileHandler):
    """Jupyter's authentication and HTTP ranges over a confined, open file."""

    def validate_absolute_path(self, root: str, absolute_path: str) -> str:
        # The opener checks containment and hidden paths, allowing annex links.
        # Keep the requested alias for MIME detection and subsequent reads:
        # its resolved target may legitimately live under .git/annex/objects.
        with open_fd(Path(root), Path(absolute_path)) as fd:
            self._stat_result = os.fstat(fd)
        return absolute_path

    # Tornado's classmethod reads by path. This instance needs the serving root
    # as well; compute_etag is disabled by Jupyter, so no class-level caller runs.
    def get_content(  # type: ignore[override]
        self, abspath: str, start: int | None = None, end: int | None = None
    ) -> Iterator[bytes]:
        with (
            open_fd(Path(self.root), Path(abspath)) as fd,
            os.fdopen(fd, "rb", closefd=False) as file,
        ):
            file.seek(start or 0)
            remaining = None if end is None else end - (start or 0)
            while remaining is None or remaining > 0:
                chunk = file.read(65_536 if remaining is None else min(65_536, remaining))
                if not chunk:
                    break
                if remaining is not None:
                    remaining -= len(chunk)
                yield chunk


class ProjectContents(AsyncLargeFileManager):
    """Confined project reads, without hashing or persistent notebook trust."""

    # Notebook reads otherwise recover a corrupt file by renaming its atomic
    # intermediate into place, a write that bypasses Jupyter's authorizer.
    use_atomic_writing = Bool(False)

    @default("files_handler_class")
    def _files_handler_class_default(self) -> type[ProjectFileHandler]:
        return ProjectFileHandler

    def _get_os_path(self, path: str) -> str:
        os_path: str = super()._get_os_path(path)  # type: ignore[no-untyped-call]
        resolved_path(Path(self.root_dir), Path(os_path))
        return os_path

    def _base_model(self, path: str) -> dict[str, Any]:
        model: dict[str, Any] = super()._base_model(path)  # type: ignore[no-untyped-call]
        os_path = Path(self._get_os_path(path))
        with open_fd(Path(self.root_dir), os_path, directory=os_path.is_dir()) as fd:
            info = os.fstat(fd)
        # lstat describes the symlink itself. Previews and cache tokens must
        # instead describe the bytes that will be served through that link.
        model.update(size=info.st_size, writable=False)
        for field, timestamp in (("last_modified", info.st_mtime), ("created", info.st_ctime)):
            try:
                model[field] = datetime.fromtimestamp(timestamp, UTC)
            except (ValueError, OverflowError, OSError):
                model[field] = datetime.fromtimestamp(0, UTC)
        return model

    @contextmanager
    def open(self, os_path: str, mode: str = "r", **kwargs: Any) -> Iterator[IO[Any]]:
        with open_fd(Path(self.root_dir), Path(os_path)) as fd:
            with open(fd, mode, closefd=False, **kwargs) as file:
                yield file

    async def get(
        self,
        path: str,
        content: bool = True,
        type: str | None = None,
        format: str | None = None,
        require_hash: bool = False,
    ) -> dict[str, Any]:
        # Live-content asks for a hash even for metadata-only reads. The page
        # never uses it, and Jupyter would read the entire artifact to produce it.
        model: dict[str, Any] = await super().get(  # type: ignore[no-untyped-call]
            path, content, type, format, require_hash=False
        )
        if model["type"] != "directory":
            root = Path(self.root_dir)
            target = resolved_path(root, root / model["path"])
            model["watchPaths"] = [
                target.relative_to(root).as_posix(),
                assets.manifest_path(Path(model["path"])).as_posix(),
            ]
        return model

    async def _dir_model(self, path: str, content: bool = True) -> dict[str, Any]:
        model: dict[str, Any] = await super()._dir_model(  # type: ignore[no-untyped-call]
            path, content=False
        )
        if content:
            with open_fd(Path(self.root_dir), Path(self._get_os_path(path)), directory=True) as fd:
                names = os.listdir(fd)
            children = []
            for name in names:
                if not self.should_list(name):  # type: ignore[no-untyped-call]
                    continue
                try:
                    children.append(await self.get(f"{path}/{name}".strip("/"), content=False))
                except (web.HTTPError, OSError):
                    # A hidden, escaping, absent or unreadable child must not
                    # prevent the rest of the directory from being listed.
                    continue
            model.update(content=children, format="json")
        return model

    @default("notary")
    def _notary_default(self) -> NotebookNotary:
        # Reading a notebook checks its signature, which by default creates
        # Jupyter's signature database and secret in the user's data directory.
        return NotebookNotary(  # type: ignore[no-untyped-call]
            parent=self, db_file=":memory:", secret=os.urandom(32)
        )
