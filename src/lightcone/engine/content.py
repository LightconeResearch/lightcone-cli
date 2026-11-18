"""What a file of the project holds: its content, or only a stand-in for it.

A large-file tool keeps a stand-in in place of content a clone has not
fetched, and a stand-in reads perfectly well: hashing one gives a
well-formed digest of the wrong bytes, and a recipe handed one reads a few
lines of text for its data. So every file whose bytes a record will name is
asked here first, and hashed here the way a record names content.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from lightcone.engine.project import ProjectError

#: The hash and size of a file's bytes, as a record gives them.
Digest = tuple[str, int]

#: The marker every annexed path carries, in both of the shapes an annexed
#: file takes on disk — the pointer file's first bytes, and the locked
#: symlink's target. Detecting the first is the same test git-annex's own
#: ``isPointerFile`` makes, and both have to be made: an unfetched file is
#: readable, or absent, but never obviously wrong.
_ANNEX_OBJECTS = "/annex/objects/"
_POINTER_PREFIX = _ANNEX_OBJECTS.encode()
_POINTER_MAX_BYTES = 32 * 1024

#: The first line of the file Git LFS leaves in place of content it has
#: not fetched. ASTRA reads one as a stand-in too; hashing it would give
#: a well-formed digest of the wrong bytes exactly as an annex pointer's
#: would.
_LFS_POINTER = b"version https://git-lfs.github.com/spec/v1\n"


class ContentNotFetchedError(ProjectError):
    """A file whose content is not in this clone, only a stand-in for it."""


def require_fetched(path: Path) -> None:
    """Refuse a file whose content this clone does not hold.

    An annexed file takes one of two shapes, and a researcher can convert
    between them whenever they like — so both are checked rather than
    whichever one lc's own writes produce. An *unlocked* file is a small
    regular file holding the object's path, hard-linked to the object or
    copied from it depending on ``annex.thin``; both look identical without
    the content. A *locked* file is a symlink into the object store, which
    without the content simply dangles.

    Git LFS leaves a third shape, a small text file naming the content,
    which is refused the same way: a recipe handed that file would read a
    few lines of text for its data. lc fetches nothing from LFS, so that
    refusal names the command that does; annexed content a run fetches
    itself, before any task looks.

    Args:
        path: A file whose bytes are about to be used.

    Raises:
        ContentNotFetchedError: If *path* is one of these shapes without
            its content. Loud, because the alternative is silent:
            hashing a stand-in yields a well-formed digest of the wrong
            bytes.
        OSError: If *path* cannot be read at all.
    """
    if path.is_symlink() and not path.exists():
        unfetched = _ANNEX_OBJECTS in path.readlink().as_posix()
    else:
        unfetched = _is_pointer(path)
    if unfetched:
        raise ContentNotFetchedError(
            f"{path}: the content is not in this clone — git-annex holds a "
            "reference to it, not the data."
        )
    if _lfs_pointer(path):
        raise ContentNotFetchedError(
            f"{path}: the content is not in this clone — Git LFS holds a "
            "reference to it, not the data. Fetch it with `git lfs pull`."
        )


def _is_pointer(path: Path) -> bool:
    """Test whether a regular file holds an annex pointer, not content.

    git-annex's own ``isPointerFile`` rule, spelled once: a file no
    larger than 32 KiB whose bytes begin ``/annex/objects/``. The locked
    shape — a symlink into the object store — is a separate question the
    callers ask themselves, because what a symlink means differs by
    caller.

    Args:
        path: An existing regular file.

    Returns:
        Whether it is a pointer.

    Raises:
        OSError: If the file cannot be read.
    """
    if path.stat().st_size > _POINTER_MAX_BYTES:
        return False
    with path.open("rb") as f:
        return f.read(len(_POINTER_PREFIX)) == _POINTER_PREFIX


def _lfs_pointer(path: Path) -> bool:
    """Test whether a file is what Git LFS leaves in place of content.

    Args:
        path: A path, which need not exist.

    Returns:
        True for a small regular file that opens with the LFS pointer line.
    """
    if not path.is_file() or path.stat().st_size > _POINTER_MAX_BYTES:
        return False
    with path.open("rb") as stream:
        return stream.read(len(_LFS_POINTER)) == _LFS_POINTER


def digest_file(path: Path) -> Digest:
    """Hash a file the way a record names content.

    What a record says of bytes is the runner's to find out, so the
    hashing is here and not ASTRA's: an unframed sha256, which an
    outsider reproduces with ``sha256sum``, and the size of the very
    bytes hashed. Never mtime: a content hash is what lets a
    byte-identical rebuild stop a cascade.

    Args:
        path: A file holding the content itself; ask
            :func:`require_fetched` first where it could be a stand-in.

    Returns:
        The hash, as ``sha256:<hex>``, and the size in bytes.

    Raises:
        OSError: If the file cannot be read.
    """
    with path.open("rb") as stream:
        hashed = hashlib.file_digest(stream, "sha256").hexdigest()
        return f"sha256:{hashed}", os.fstat(stream.fileno()).st_size


def stamp(path: Path) -> tuple[int, int, int]:
    """What changes when a file is written or replaced: its inode, its
    size and its modification time."""
    found = path.stat()
    return found.st_ino, found.st_size, found.st_mtime_ns
