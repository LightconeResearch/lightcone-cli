"""What a materialized result *is*: what lc records beside it, and whether
it is still current.

A result is a single file at the place ASTRA gives it —
``results/<universe>/<name>.<format>`` — and its **record**, ASTRA's own
account of how it was made, is at ``.astra/records/<universe>/<name>.json``.
The record is closed: it holds the commit, the inputs' hashes, the
environment and the runner, and nothing else. What lc alone can say about
a run — what the sandbox actually enforced, the image, the uv that built
the environment — goes in a manifest sidecar beside the bytes,
``.<name>.manifest.json``. Both stay out of the annex, so they are
readable on a clone that has fetched no content at all.

The rule that classifies a result — ``current``, ``behind`` or ``stale`` —
lives here too. Whether a record still describes the analysis is ASTRA's
checker's to say, and it is asked rather than re-derived; lc adds the two
things only an executor knows: the environment a result ran under, and
whether its files were last written by its own run.
"""

from __future__ import annotations

import json
import posixpath
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from lightcone.engine.project import ProjectError

if TYPE_CHECKING:
    from astra.check import Status as Checked
    from astra.universes import Place

    # A type only: history is git's, and this module runs no git.
    from lightcone.engine.dataset import LastWrite

MANIFEST_SUFFIX = ".manifest.json"
SCHEMA_VERSION = 2

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


def manifest_path(root: Path, place: Place) -> Path:
    """The manifest sidecar beside the bytes at *place*.

    ``.<name>.manifest.json``, named from the result's name alone and
    never its format — so the manifest keeps its path, and therefore its
    history, when a spec re-declares the output in another serialization.

    Args:
        root: The project root.
        place: One of a result's places.

    Returns:
        The sidecar's path.
    """
    return (root / place.bytes).parent / f".{place.name}{MANIFEST_SUFFIX}"


def sidecars(path: str) -> list[tuple[str, str]]:
    """Where the record and manifest of a result file at *path* would be.

    For a caller holding only a file's path: a result's name is ids
    joined by dots and a format may hold dots too, so the file's name
    alone does not say where one ends — each split is a candidate, and
    the one whose record exists is the answer.

    Args:
        path: A file's POSIX path from the project root.

    Returns:
        ``(record, manifest)`` paths, one per split; none for a file that
        is at no result's place.
    """
    *folder, file = path.split("/")
    universe = folder[-1:] if folder[-2:-1] == ["results"] else []
    top = folder[: -1 - len(universe)]
    if folder[len(top) : len(top) + 1] != ["results"]:
        return []
    return [
        (
            "/".join([*top, ".astra", "records", *universe, f"{file[:dot]}.json"]),
            "/".join([*folder, f".{file[:dot]}{MANIFEST_SUFFIX}"]),
        )
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
# The manifest
# =============================================================================


@dataclass(frozen=True)
class Manifest:
    """What lc attests about one run, beyond what its record holds.

    Every field is filled by the run that wrote it. None of it is compared
    by anything: the record is what a result is checked against, and this
    is what makes the run that produced it legible afterwards.
    """

    #: The command as it ran, placeholders filled. The record names the
    #: commit that holds the template; this is what the shell was handed.
    recipe: str
    #: Each of the output's decisions and the option it ran under.
    decisions: dict[str, str]
    #: The `origin` URL, or empty when the repository has no remote.
    git_remote: str
    #: The uv that resolved and installed the environment the recipe ran
    #: in — the one tool between the lock and the installed artifacts.
    uv_version: str
    #: What the sandbox actually enforced, as the boundary attested it.
    hermeticity: dict[str, Any]
    #: The image the recipe ran in — ``{tag, id, archive, arch}`` — or
    #: ``None`` on the host.
    image: dict[str, Any] | None
    schema_version: int = field(default=SCHEMA_VERSION)

    def as_dict(self) -> dict[str, Any]:
        """Return the manifest as JSON-ready data, ``schema_version`` first.

        Returns:
            Every field, in declaration order.
        """
        data = asdict(self)
        return {"schema_version": data.pop("schema_version"), **data}


def read(manifest: Path) -> Manifest | None:
    """Read the manifest at *manifest*.

    Args:
        manifest: The sidecar's path.

    Returns:
        The manifest, or ``None`` when it is absent or unparseable.

    Raises:
        OSError: Deliberately not caught. A permission problem is a real
            fault and must not look like a run that attested nothing.
    """
    if not manifest.is_file():
        return None
    try:
        return loads(manifest.read_text())
    except UnicodeDecodeError:
        return None


def loads(text: str) -> Manifest | None:
    """Parse a manifest already read by a caller, or return None if invalid."""
    try:
        return Manifest(**json.loads(text))
    except (json.JSONDecodeError, TypeError, ValueError):
        # Anything that is not a manifest object reads as "no manifest":
        # a top-level array, a mapping whose fields do not fit.
        return None


def write(path: Path, manifest: Manifest) -> Path:
    """Write *manifest* to *path*, atomically.

    The rename is the commit point: a reader sees the previous manifest or
    this one, never half of either. The temporary lands beside real outputs,
    so it is named off the sidecar — unique per output, and swept by the
    same pathspec.

    Args:
        path: The sidecar's path.
        manifest: The attestation to write.

    Returns:
        The path written.
    """
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(manifest.as_dict(), indent=2, sort_keys=False) + "\n")
    temporary.replace(path)
    return path


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


def check(root: Path) -> dict[str, tuple[Checked, ...]]:
    """Ask ASTRA's checker what every planned result's record says of it.

    Reads records, hashes inputs and reads git; runs nothing and writes
    nothing in the project.

    Args:
        root: The project root.

    Returns:
        Each planned result's statuses, one per place, keyed by the path
        of its first place's record from the project root.

    Raises:
        ProjectError: If the analysis is missing or breaks one of ASTRA's
            rules, or git cannot be read.
    """
    from astra import api

    from lightcone.engine import plan

    with plan.asking_astra(root):
        report = api.check(root)
    if report.errors:
        raise ProjectError(plan.invalid(root, report.errors))
    above = posixpath.dirname(report.root)
    return {
        plan.relative(found.result.places[0].record, above): found.statuses
        for found in report.results
    }


def classify(
    *, found: Sequence[Checked], environment: str, foreign: LastWrite | None = None
) -> Verdict:
    """Decide what a result is, relative to the project as it now stands.

    The only place this rule lives. Values rather than objects, because
    the driver and the worker arrive at them differently and must not
    diverge in anything else — history included: whether the result's
    files were last written by its own run is git's to answer, so it
    arrives here as a value computed by whoever has git (the driver), and
    the rule stays pure.

    Args:
        found: What ASTRA's checker says at each of the result's places.
        environment: The IRI of the project's environment as it now is.
        foreign: The commit that last wrote the result's files, when it
            was not the result's own run; ``None`` when clean. A hit is a
            contradiction — the record no longer describes the bytes — so
            it is ``stale``, though a result ASTRA already finds stale
            keeps that more actionable reason.

    Returns:
        ``stale``, ``behind`` or ``current``, with the reason for the
        first two.
    """
    if why := [
        reason.message
        for status in found
        if status.record_status != "unchanged"
        for reason in status.reasons
        if reason.code != "ABSENT_RESULT"
    ]:
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
