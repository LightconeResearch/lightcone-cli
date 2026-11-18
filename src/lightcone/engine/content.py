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
import re
from pathlib import Path

from lightcone.engine.project import ProjectError

#: The hash and size of a file's bytes, as a record gives them.
Digest = tuple[str, int]

#: No stand-in file is longer, as ASTRA's checker reads them: Git LFS's
#: pointer is under 1024 bytes by its specification, and git-annex's is one
#: line naming a key. A longer file is content, and is not read to see.
_POINTER_MAX = 1024

#: What Git LFS's pointer opens with, and the two fields it then gives.
_LFS_VERSION = "version https://git-lfs.github.com/spec/v1"
_LFS_OID = re.compile(r"sha256:[0-9a-f]{64}")
_LFS_SIZE = re.compile(r"[0-9]+")

#: Where git-annex keeps content: each of its stand-ins names a path here.
_ANNEX_OBJECTS = "/annex/objects/"


class ContentNotFetchedError(ProjectError):
    """A file whose content is not in this clone, only a stand-in for it."""


def require_fetched(path: Path) -> None:
    """Refuse a file whose content this clone does not hold.

    An annexed file takes one of two shapes, and a researcher can convert
    between them whenever they like — so both are checked rather than
    whichever one lc's own writes produce. An *unlocked* file without its
    content is a pointer file (:func:`standin`). A *locked* file is a
    symlink into the object store, which without the content dangles.

    Git LFS leaves a pointer file of its own, which is refused the same
    way: a recipe handed that file would read a few lines of text for its
    data. lc fetches nothing from LFS, so that refusal names the command
    that does; annexed content a run fetches itself, before any task looks.

    Args:
        path: A file whose bytes are about to be used.

    Raises:
        ContentNotFetchedError: If *path* is one of these shapes without
            its content. Loud, because the alternative is silent:
            hashing a stand-in yields a well-formed digest of the wrong
            bytes.
        OSError: If *path* cannot be read.
    """
    if path.is_symlink():
        # A link git-annex makes is relative; ASTRA reads no other as its.
        target = os.readlink(path)
        annexed = not os.path.isabs(target) and _ANNEX_OBJECTS in f"/{target}"
        tool = "git-annex" if annexed and not path.exists() else None
    else:
        tool = standin(path)
    if tool == "git-annex":
        raise ContentNotFetchedError(
            f"{path}: the content is not in this clone — git-annex holds a "
            "reference to it, not the data."
        )
    if tool == "Git LFS":
        raise ContentNotFetchedError(
            f"{path}: the content is not in this clone — Git LFS holds a "
            "reference to it, not the data. Fetch it with `git lfs pull`."
        )


def standin(path: Path) -> str | None:
    """Name the tool whose stand-in the file at *path* is, if it is one.

    ASTRA's checker's reading of a pointer file, to the letter: Git LFS's
    is its version line, then an ``oid`` that is a SHA-256 and a
    ``size``, each line ended; git-annex's is one line, a path into its
    object store. A runner that read them otherwise would refuse an input
    ASTRA calls content, or record as a result bytes ASTRA then reads as a
    stand-in for other bytes, and plans again on every check.

    Args:
        path: A path, which need not exist. A symlink is no pointer file.

    Returns:
        ``Git LFS`` or ``git-annex``, or None for content.

    Raises:
        OSError: If *path* cannot be read.
    """
    if path.is_symlink() or not path.is_file() or path.stat().st_size > _POINTER_MAX:
        return None
    data = path.read_bytes()
    try:
        lines = data.decode("utf-8").split("\n")
    except UnicodeDecodeError:
        lines = []
    if lines and lines[0] == _LFS_VERSION and lines[-1] == "":
        fields = {key: value for key, _, value in (line.partition(" ") for line in lines[1:-1])}
        if _LFS_OID.fullmatch(fields.get("oid", "")) and _LFS_SIZE.fullmatch(
            fields.get("size", "")
        ):
            return "Git LFS"
    line = data.removesuffix(b"\n")
    if line.startswith(_ANNEX_OBJECTS.encode()) and b"\n" not in line:
        return "git-annex"
    return None


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
