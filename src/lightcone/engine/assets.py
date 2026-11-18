"""Whether a materialized result is still current.

A result is a single file at the place ASTRA gives it —
``results/<universe>/<name>.<format>``, or ``results/<name>.<format>``
for an output built once — and its **record**, ASTRA's own account of
how it was made, is at ``.astra/records/<universe>/<name>.json``. The
record is all lc writes beside a result, and it stays out of the annex,
so it is readable on a clone that has fetched no content at all.

The rule that classifies a result — ``current``, ``behind`` or ``stale``
— lives here. Whether a record still describes the analysis is ASTRA's
checker's to say, and its findings are taken as they are; lc adds the
three things only a runner knows: the bytes the results it reads have
once a run has remade them, the environment a result ran under, and
whether its files were last written by its own run.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from lightcone.engine.project import ProjectError

if TYPE_CHECKING:
    from astra.api import Status as Checked

    # A type only: history is git's, and this module runs no git.
    from lightcone.engine.dataset import LastWrite

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


# =============================================================================
# Content
# =============================================================================


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
        unfetched = is_pointer(path)
    if unfetched:
        raise ContentNotFetchedError(
            f"{path}: the content is not in this clone — git-annex holds a "
            "reference to it, not the data."
        )
    if lfs_pointer(path):
        raise ContentNotFetchedError(
            f"{path}: the content is not in this clone — Git LFS holds a "
            "reference to it, not the data. Fetch it with `git lfs pull`."
        )


def is_pointer(path: Path) -> bool:
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


def lfs_pointer(path: Path) -> bool:
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


# =============================================================================
# Classification — one rule, and it is the only place the rule lives
# =============================================================================
#
# Three states, and the line between them is what the whole model turns on.
#
# A result is **stale** when it contradicts the project as it now stands:
# ASTRA's checker finds no valid record for it, finds that something it
# is made from has changed since the commit its record names, or finds
# other bytes at its place than its record gives; or its files were last
# committed by something other than its own run. Either way what is on
# disk is mislabelled, so it is remade.
#
# A result is **behind** when its record still describes the analysis, but
# names an earlier environment. Nothing about it is wrong — the record's
# commit reconstructs that environment — so it is reported and left alone.
# A caller that wants it remade asks for that.
#
# Whether the bytes are in this clone takes part in neither. A clone that
# has fetched no annex content holds stand-ins for results that are
# perfectly up to date. Bytes at a result's place that are not its
# record's, and stand for nothing, are another matter: what is there is
# not what the record describes, so it is stale.


#: What a result is, relative to the project as it now stands.
Status = Literal["current", "behind", "stale"]


@dataclass(frozen=True)
class Verdict:
    """One result's state, and the sentence explaining it."""

    status: Status
    #: Why, for ``stale`` and ``behind``. Empty for ``current``.
    why: str = ""

    def calls_for_a_remake(self, *, refresh: bool) -> bool:
        """Whether a run would make this result again.

        ``stale`` always, because the artifact contradicts the project.
        ``behind`` only when asked, because it does not.

        Args:
            refresh: Whether the caller asked for behind results too.

        Returns:
            Whether to run the recipe.
        """
        return self.status == "stale" or (refresh and self.status == "behind")


def classify(
    *,
    found: Checked,
    environment: str,
    read: Mapping[str, Digest] | None = None,
    foreign: LastWrite | None = None,
) -> Verdict:
    """Decide what a result is, relative to the project as it now stands.

    The only place this rule lives. Values rather than objects, because
    the driver and the worker arrive at them differently and must not
    diverge in anything else — history included: whether the result's
    files were last written by its own run is git's to answer, so it
    arrives here as a value computed by whoever has git (the driver), and
    the rule stays pure.

    ASTRA's checker says what is wrong with a result in itself, and
    separately which of the results it reads were not unchanged when it
    looked. The second is not taken from the checker: by the time this is
    asked, a run may have remade those results, so it is decided here from
    the bytes they have *now* against the bytes the record says were read.
    That is what lets a byte-identical rebuild stop a cascade.

    Args:
        found: What ASTRA's checker found of the result before the run.
        environment: The IRI of the project's environment as it now is.
        read: The bytes of each result this one reads, by entry of its
            output's ``inputs``, as they now stand: a task's answer once
            its upstreams have landed, or the driver's from their records
            while none of them is to be remade. ``None`` asks what the
            result is in itself, while one of them may still be remade.
        foreign: The commit that last wrote the result's files, when it
            was not the result's own run; ``None`` when clean. A hit is a
            contradiction — the record no longer describes the bytes — so
            it is ``stale``, though a result ASTRA already finds stale
            keeps that more actionable reason. It is said in place of
            ASTRA's finding that the bytes are not the record's, since it
            also says who wrote them.

    Returns:
        ``stale``, ``behind`` or ``current``, with the reason for the
        first two.
    """
    record = found.record
    why = [broken.message for broken in found.why if broken.level == "error"]
    if record is not None and read is not None:
        recorded = {
            item.entry: (item.content_hash, item.size) for item in record.generated_by.inputs
        }
        why += [
            f"`{entry}` was remade with other bytes than this result read"
            for entry, digest in read.items()
            if recorded.get(entry) != digest
        ]
    if why:
        return Verdict("stale", "; ".join(dict.fromkeys(why)))
    if foreign is not None:
        return Verdict(
            "stale",
            f'last changed by {foreign.sha[:7]} ("{foreign.subject}", {foreign.author}, '
            f"{foreign.date}) rather than its own run, so the record no longer "
            f"describes these bytes — the next run remakes it; inspect first with "
            f"`git show {foreign.sha[:7]}`",
        )
    # Of ASTRA's warnings one says the result is not what its record
    # describes: bytes at its place that are neither the record's nor a
    # stand-in for them. After a foreign write, which says who wrote them;
    # content this clone has not fetched is stood in for, and no
    # diagnostic at all.
    if other := [broken.message for broken in found.why if broken.code == "ABSENT_RESULT"]:
        return Verdict("stale", "; ".join(dict.fromkeys(other)))
    assert record is not None  # a result with no record of its own breaks a rule
    if record.generated_by.environment.iri != environment:
        return Verdict("behind", "made under an earlier environment")
    return Verdict("current")
