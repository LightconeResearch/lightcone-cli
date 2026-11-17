"""What a materialized output *is*: where it lives, what it records, and
whether it is still current.

An asset is a single file at the place ASTRA gives its result —
``results/<universe>/<name>.<format>``, or ``results/<name>.<format>``
for an output built once — with a manifest sidecar beside it,
``.<name>.manifest.json``. The place is ASTRA's, so the path is derived
rather than chosen by the recipe, and one result can only ever be one
file. Beside the bytes lc writes ASTRA's record of the result, at
``.astra/records/<universe>/<name>.json``, and its own manifest, which is
what it decides from. Both are kept out of the annex, so they stay
readable on a clone that has fetched no content at all.

The rule that classifies an output — ``current``, ``behind`` or
``stale`` — lives here too, next to the manifest it reads and the hashes
it compares. It is the one place in the layer where a bug is quiet rather
than loud: a rule that under-reports leaves an output silently describing
bytes that no longer follow from its inputs. It is a **content-hash**
rule, so a byte-identical rebuild stops the cascade and a restored file
with an old mtime cannot hide.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from lightcone.engine.project import ProjectError

if TYPE_CHECKING:
    # A type only: history is git's, and this module runs no git.
    from lightcone.engine.dataset import LastWrite

MANIFEST_SUFFIX = ".manifest.json"
SCHEMA_VERSION = 1

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


class ContentNotFetchedError(ProjectError):
    """An annexed file whose content is not in this clone."""


def manifest_path(output: Path) -> Path:
    """The manifest sidecar beside *output*.

    ``.<name>.manifest.json``, named from the result's name alone and
    never its format — so the manifest keeps its path, and therefore its
    history, when a spec re-declares the output in another serialization.

    A name is an output's id, which ASTRA's grammar keeps free of dots,
    while a format may hold one (``tar.gz``), so the name is recovered by
    partitioning on the **first** dot. ``Path.stem`` would answer ``x.tar``
    for ``x.tar.gz``.

    Args:
        output: The output's own path.

    Returns:
        The sidecar's path.
    """
    return output.parent / f".{output.name.partition('.')[0]}{MANIFEST_SUFFIX}"


# =============================================================================
# Content identity
# =============================================================================


def digest(path: Path) -> Digest:
    """Hash a file's content — its bytes, and nothing else about it.

    Unframed, so the hash is a plain sha256 an outsider reproduces with
    ``sha256sum``, and the manifest, the record and the crate agree on it
    for the same file. Never mtime: a content hash is what lets a
    byte-identical rebuild stop cascading, and what stops a file restored
    with an old timestamp passing as unchanged. The size is the one a
    record gives beside the hash, read from the very bytes hashed.

    Args:
        path: A file: an input, or a result, each of which ASTRA makes one
            file.

    Returns:
        The hash, as ``sha256:<hex>``, and the size in bytes.

    Raises:
        FileNotFoundError: If *path* does not exist. Never a constant
            digest, which would silently disable the staleness chain.
        ContentNotFetchedError: If the file is annexed without its content
            being in this clone, in either of the shapes that takes.
    """
    if path.is_symlink() and not path.exists():
        require_fetched(path)
    if not path.exists():
        raise FileNotFoundError(path)
    require_fetched(path)
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
        return f"sha256:{h.hexdigest()}", os.fstat(f.fileno()).st_size


def stamp(path: Path) -> tuple[int, int, int]:
    """What changes when a file is written or replaced: its inode, its
    size and its modification time."""
    found = path.stat()
    return found.st_ino, found.st_size, found.st_mtime_ns


class Versions:
    """Content digests, computed once per run.

    A declared input is hashed once per result that reads it, which for a
    multiverse spec is the same bytes over and over: eight universes times
    four outputs sharing one catalog reads it thirty-two times. A digest
    is kept with the stamp of the file it was computed from, and given
    again only for that very file: one that was written or replaced since
    is hashed again, so what a task records reading is what it read.

    The driver fills the memo before submitting tasks. Its populated dict
    travels with each task, so separate worker processes reuse the same hashes.
    Failed reads are left to the worker to report for the affected task.
    """

    def __init__(self) -> None:
        self._known: dict[Path, tuple[tuple[int, int, int], Digest]] = {}

    def of(self, path: Path) -> Digest:
        """Return *path*'s digest, hashing each version of it at most once.

        Args:
            path: A declared input.

        Returns:
            The hash, as ``sha256:<hex>``, and the size.
        """
        resolved = path.resolve()
        now = stamp(path) if path.exists() else None
        known = self._known.get(resolved)
        if known is None or known[0] != now:
            found = digest(path)
            assert now is not None  # `digest` raises for a path that is not there
            known = self._known[resolved] = now, found
        return known[1]


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
            content. Loud, because the alternative is silent: hashing a
            pointer yields a well-formed digest of the wrong bytes.
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
    """What one materialization recorded about itself.

    Every field is filled by the run that wrote it; there are no optional
    halves. ``input_versions`` is the chain — each declared input's content
    identity at the moment this output was made — and it is what lets a
    change to anything upstream reach here without a timestamp.
    """

    output_id: str
    #: Empty for a result built once.
    universe_id: str
    recipe: str
    #: What the spec says this output is: its recipe and decisions. The
    #: rebuild trigger.
    definition_version: str
    #: The environment it was made under. Recorded, never a rebuild
    #: trigger — a difference here makes the output *behind*, not stale.
    env_version: str
    data_version: str
    #: The size of the result's bytes, which a record of a result that
    #: reads this one gives beside ``data_version``.
    size: int
    decisions: dict[str, str]
    input_versions: dict[str, str]
    #: The commit the working tree was at when the run started. Recorded,
    #: never hashed into `definition_version` — a commit must not stale
    #: every output in the repository, and this is what makes the code that
    #: produced a result recoverable anyway.
    git_sha: str
    #: The `origin` URL, or empty when the repository has no remote.
    git_remote: str
    #: The engine that made it. Attestation, not identity: outside both
    #: hashes, so an lc upgrade neither stales an output nor puts it behind.
    lc_version: str
    #: What the sandbox actually enforced, as the boundary attested it.
    hermeticity: dict[str, Any]
    #: When the recipe entered and left the boundary, ISO 8601 UTC with
    #: millisecond precision. Attestation, like ``lc_version``: outside
    #: both hashes, never a rebuild signal — and defaulted empty because
    #: that is the true value for a manifest written before the fields
    #: existed, not back-compat machinery.
    started_at: str = ""
    finished_at: str = ""
    #: The uv that resolved and installed the environment the recipe ran
    #: in — the one tool between the lock and the installed artifacts.
    #: Attestation, like ``lc_version``: outside both hashes, never a
    #: rebuild signal, defaulted empty because that is the true value for
    #: a manifest written before the field existed.
    uv_version: str = ""
    #: The image the recipe ran in — ``{tag, id, archive, arch}`` — or
    #: ``None`` on the host. Defaulted, and that is not back-compat
    #: machinery: ``None`` is the *true* value for every manifest a
    #: container-less engine wrote, and without the default those
    #: manifests would read as absent and the whole project would go
    #: stale over a field that changes nothing about the bytes.
    image: dict[str, Any] | None = None
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

    Takes the sidecar's own path rather than the output's, so every caller
    holding an output path has to say :func:`manifest_path` out loud. Both
    are ``Path``, and a missing file answers ``None`` rather than raising,
    so a caller that passed the wrong one would be wrong in silence.

    Args:
        manifest: The sidecar's path.

    Returns:
        The manifest, or ``None`` when it is absent or unparseable — which
        the staleness rule reads as "make it again", the safe direction.

    Raises:
        OSError: Deliberately not caught. A permission problem is a real
            fault and must not look like an output needing a rebuild.
    """
    path = manifest
    if not path.is_file():
        return None
    try:
        return loads(path.read_text())
    except UnicodeDecodeError:
        return None


def loads(text: str) -> Manifest | None:
    """Parse a run record already read by a caller, or return None if invalid."""
    try:
        data = json.loads(text)
        return Manifest(**{k: v for k, v in data.items() if k in _FIELDS})
    except (json.JSONDecodeError, TypeError, AttributeError, UnicodeDecodeError, ValueError):
        # Anything that is not a manifest object reads as "no manifest":
        # a top-level array, bytes that are not text, a mapping whose
        # fields do not fit. The staleness rule takes that as "make it
        # again", which is the safe direction for all of them.
        return None


def write(path: Path, manifest: Manifest) -> Path:
    """Write *manifest* to *path*, atomically.

    The rename is the commit point: a reader sees the previous manifest or
    this one, never half of either. The temporary lands beside real outputs
    now, so it is named off the sidecar — unique per output, and swept by
    the same pathspec.

    Args:
        path: The sidecar's path.
        manifest: The record to write.

    Returns:
        The path written.
    """
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(manifest.as_dict(), indent=2, sort_keys=False) + "\n")
    temporary.replace(path)
    return path


_FIELDS = frozenset(Manifest.__dataclass_fields__)


# =============================================================================
# Classification — one rule, and it is the only place the rule lives
# =============================================================================
#
# Three states, and the line between them is what the whole model turns on.
#
# An output is **stale** when it contradicts the project as it now stands:
# the spec defines it differently than the artifact was made, or it records
# deriving from bytes the project no longer holds. Either way what is on
# disk is mislabelled, so it is remade.
#
# An output is **behind** when it is still exactly what the spec asks for,
# but was made under an earlier environment. Nothing about it is wrong —
# the environment it ran under is in its manifest and the commit beside it
# reconstructs that environment — so it is reported and left alone. A
# caller that wants it remade asks for that.
#
# The git commit takes part in neither. It is tree-wide, so a README edit
# moves it for every output at once; using it as a signal would mean
# everything is always behind, and a signal that is always on is not one.
# It is recorded, and shown as the context of a `behind` line.


#: What an output is, relative to the project as it now stands.
Status = Literal["current", "behind", "stale"]


@dataclass(frozen=True)
class Reason:
    """Why an output no longer describes what it was made from."""

    kind: Literal["missing", "definition", "declaration", "input"]
    #: Which declared input it was about, for the two input kinds.
    input: str = ""

    def __str__(self) -> str:
        if self.kind == "missing":
            return "no manifest — it has never been materialized"
        if self.kind == "definition":
            return "the recipe or its decisions changed"
        if self.kind == "declaration":
            return f"the output no longer declares the same inputs (`{self.input}`)"
        return f"the input `{self.input}` changed"


@dataclass(frozen=True)
class Verdict:
    """One output's state, and the sentence explaining it."""

    status: Status
    #: Why, for ``stale`` and ``behind``. Empty for ``current``.
    why: str = ""

    def calls_for_a_remake(self, *, refresh: bool) -> bool:
        """Whether a run would make this output again.

        ``stale`` always, because the artifact contradicts the project.
        ``behind`` only when asked, because it does not.

        Args:
            refresh: Whether the caller asked for behind outputs too.

        Returns:
            Whether to run the recipe.
        """
        return self.status == "stale" or (refresh and self.status == "behind")


def classify(
    *,
    definition_version: str,
    env_version: str,
    manifest: Manifest | None,
    inputs: Mapping[str, str | None],
    foreign: LastWrite | None = None,
) -> Verdict:
    """Decide what an output is, relative to the project as it now stands.

    The only place this rule lives. Values rather than objects, because
    the callers arrive at them differently and must not diverge in
    anything else — history included: whether the output's directory was
    last written by its own run record is git's to answer, so it arrives
    here as a value computed by whoever has git (the driver), and the
    rule stays pure. The *prose* for every verdict lives here too, which
    is why this takes the offending commit rather than a finished
    sentence.

    Args:
        definition_version: What the spec currently says this output is.
        env_version: The run's environment identity.
        manifest: The output's recorded manifest, or ``None``.
        inputs: Each declared input's current content identity. ``None``
            for an input the caller has already decided will be remade —
            check mode's sentinel, meaning "this is going to change",
            since it cannot know whether a rebuild is byte-identical.
        foreign: The commit that last wrote the output's directory, when
            it was not the output's own run record; ``None`` when clean.
            A hit is a contradiction — the manifest no longer describes
            the bytes — so it is ``stale``, though an output stale on
            its definition or inputs keeps that more actionable reason.

    Returns:
        ``stale``, ``behind`` or ``current``, with the reason for the
        first two.
    """
    if (reason := _stale(definition_version, manifest, inputs)) is not None:
        return Verdict("stale", str(reason))
    if foreign is not None:
        return Verdict(
            "stale",
            f'last changed by {foreign.sha[:7]} ("{foreign.subject}", {foreign.author}, '
            f"{foreign.date}) rather than its run record, so the manifest no longer "
            f"describes these bytes — the next run remakes it; inspect first with "
            f"`git show {foreign.sha[:7]}`",
        )
    assert manifest is not None  # `_stale` returns a reason when it is None
    if manifest.env_version != env_version:
        # The sentence says what happened; *where* it happened is the
        # manifest's `git_sha`, which a caller with a column for it reads
        # from the record rather than from prose.
        return Verdict("behind", "made under an earlier environment")
    return Verdict("current")


def _stale(
    definition_version: str,
    manifest: Manifest | None,
    inputs: Mapping[str, str | None],
) -> Reason | None:
    """Why the artifact contradicts the project, or ``None`` if it does not."""
    if manifest is None:
        return Reason("missing")
    if manifest.definition_version != definition_version:
        return Reason("definition")
    # The *set* first, and separately: `definition_version` hashes the recipe
    # and the decisions, neither of which an input the spec no longer declares
    # moves — so without this the loop below would never look at it. Adding an
    # input is caught either way; dropping one is only caught here.
    if changed := set(inputs) ^ set(manifest.input_versions):
        return Reason("declaration", sorted(changed)[0])
    for name, current in inputs.items():
        if current is None or manifest.input_versions[name] != current:
            return Reason("input", name)
    return None
