"""Whether a materialized result is still current.

A result is a single file at the place ASTRA gives it —
``results/<universe>/<name>.<format>`` — and its **record**, ASTRA's own
account of how it was made, is at ``.astra/records/<universe>/<name>.json``.
The record is all lc writes beside a result, and it stays out of the
annex, so it is readable on a clone that has fetched no content at all.

The rule that classifies a result — ``current``, ``behind`` or ``stale`` —
lives here. Whether a record still describes the analysis is ASTRA's
checker's to say, and its findings are taken as they are; lc adds the two
things only a runner knows: the environment a result ran under, and
whether its files were last written by its own run.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from lightcone.engine.project import ProjectError

if TYPE_CHECKING:
    from astra.api import Digest
    from astra.api import Status as Checked

    # A type only: history is git's, and this module runs no git.
    from lightcone.engine.dataset import LastWrite

#: The marker every annexed path carries, in both of the shapes an annexed
#: file takes on disk — the pointer file's first bytes, and the locked
#: symlink's target. Detecting the first is the same test git-annex's own
#: ``isPointerFile`` makes, and both have to be made: an unfetched file is
#: readable, or absent, but never obviously wrong.
_ANNEX_OBJECTS = "/annex/objects/"
_POINTER_PREFIX = _ANNEX_OBJECTS.encode()
_POINTER_MAX_BYTES = 32 * 1024


class ContentNotFetchedError(ProjectError):
    """An annexed file whose content is not in this clone."""


def record_places(path: str) -> list[str]:
    """Where the record of a result file at *path* would be.

    For a caller holding only a file's path: a result's name is ids
    joined by dots and a format may hold dots too, so the file's name
    alone does not say where one ends — each split is a candidate, and
    the one whose record exists is the answer.

    Args:
        path: A file's POSIX path from the project root.

    Returns:
        One record path per split; none for a file that is at no
        result's place.
    """
    *folder, file = path.split("/")
    universe = folder[-1:] if folder[-2:-1] == ["results"] else []
    top = folder[: -1 - len(universe)]
    if folder[len(top) : len(top) + 1] != ["results"]:
        return []
    return [
        "/".join([*top, ".astra", "records", *universe, f"{file[:dot]}.json"])
        for dot, char in enumerate(file)
        if char == "." and dot
    ]


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

    Args:
        path: A file whose bytes are about to be used.

    Raises:
        ContentNotFetchedError: If *path* is either shape without its
            content. Loud, because both alternatives are silent: hashing a
            pointer yields a well-formed digest of the wrong bytes, and a
            dangling symlink drops out of a directory's digest entirely.
    """
    if path.is_symlink() and not path.exists():
        unfetched = _ANNEX_OBJECTS in path.readlink().as_posix()
    else:
        unfetched = is_pointer(path)
    if unfetched:
        raise ContentNotFetchedError(
            f"{path}: the content is not in this clone — git-annex holds a "
            f"reference to it, not the data. Fetch it with `git annex get {path}`."
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


# =============================================================================
# Classification — one rule, and it is the only place the rule lives
# =============================================================================
#
# Three states, and the line between them is what the whole model turns on.
#
# A result is **stale** when it contradicts the project as it now stands:
# ASTRA's checker finds no valid record for it, or finds that something it
# is made from has changed since the commit its record names; or its files
# were last committed by something other than its own run. Either way what
# is on disk is mislabelled, so it is remade.
#
# A result is **behind** when its record still describes the analysis, but
# names an earlier environment. Nothing about it is wrong — the record's
# commit reconstructs that environment — so it is reported and left alone.
# A caller that wants it remade asks for that.
#
# Whether the bytes are in this clone takes part in neither. A clone that
# has fetched no annex content holds results that are perfectly up to
# date, and a result that was really replaced shows in history.


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
    found: Sequence[Checked],
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
    the rule stays pure. So does ASTRA's part: what its checker found
    travels with the task, and the one question left once upstream tasks
    have landed — does this result still read the bytes its record gives
    — is ASTRA's own rule over those findings, asked without git.

    Args:
        found: What ASTRA's checker found at each of the result's places,
            before the run.
        environment: The IRI of the project's environment as it now is.
        read: The bytes of each result this one reads, by entry of its
            output's ``inputs``, as they now stand: a task's answer once
            its upstreams have landed, and what makes a byte-identical
            rebuild stop a cascade. ``None`` asks what the result is in
            itself, taking each result it reads to come out as recorded —
            the question while one of them may still be remade.
        foreign: The commit that last wrote the result's files, when it
            was not the result's own run; ``None`` when clean. A hit is a
            contradiction — the record no longer describes the bytes — so
            it is ``stale``, though a result ASTRA already finds stale
            keeps that more actionable reason.

    Returns:
        ``stale``, ``behind`` or ``current``, with the reason for the
        first two.
    """
    why: list[str] = []
    for status in found:
        recorded = (
            {item.entry: item.digest for item in status.record.inputs} if status.record else {}
        )
        given = recorded if read is None else read
        if status.unchanged_given(given):
            continue
        # A finding about a result this one reads is settled by the bytes
        # that result has now, so it is worded from them, not from before.
        why += [
            reason.message
            for reason in status.reasons
            if reason.code != "ABSENT_RESULT"
            and not (reason.change == "result" and reason.entry in given)
        ]
        why += [
            f"`{entry}` was remade with other bytes than this result read"
            for entry, digest in given.items()
            if status.record is not None and recorded.get(entry) != digest
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
    if any(status.record is None or status.record.environment != environment for status in found):
        return Verdict("behind", "made under an earlier environment")
    return Verdict("current")
