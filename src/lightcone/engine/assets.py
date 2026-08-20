"""What a materialized output *is*: where it lives, what it records, and
whether it is still current.

An asset is a directory — ``results/<universe>/<output_id>/`` — holding
whatever the recipe wrote, plus a manifest beside it. The manifest is the
only part lc writes itself, and it is kept out of the annex so it stays
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
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from lightcone.engine.project import ProjectError

MANIFEST_FILENAME = ".lightcone-manifest.json"
SCHEMA_VERSION = 1

#: Excluded from the content hash: the manifest is written *after* the
#: hash it contains, so hashing it would be circular.
_HASH_EXCLUDE = frozenset({MANIFEST_FILENAME})

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


def output_dir(root: Path, universe_id: str, output_id: str) -> Path:
    """Locate a ``(universe, output)`` pair's directory.

    Path-addressed: the path in a rendered recipe is this path, with no
    staging, scratch or relocation in between.

    Args:
        root: The project root.
        universe_id: The universe the output was made under.
        output_id: The output's id, qualified for a sub-analysis.

    Returns:
        ``<root>/results/<universe_id>/<output_id>``.

    Raises:
        ProjectError: If either id is not a single path component. The
            path is *composed* from them, so an empty one collapses it
            onto a parent — ``results/`` itself, for two — and a worker
            empties this directory before running a recipe in it.
    """
    for label, value in (("universe", universe_id), ("output", output_id)):
        if not value or "/" in value or "\\" in value or value in {".", ".."}:
            raise ProjectError(
                f"{label} id {value!r} is not a single path component, so it "
                f"cannot name a directory under results/."
            )
    return root / "results" / universe_id / output_id


# =============================================================================
# Content identity
# =============================================================================


def data_version(path: Path) -> str:
    """Hash *path*'s content — its bytes, and nothing else about it.

    A directory hashes each file in sorted relative-path order with the
    relative path fed in beside the bytes, so a rename moves the digest. A
    file hashes its own bytes. The two are framed apart, so a directory
    holding one file cannot collide with that file alone.

    Never mtime or size: a content hash is what lets a byte-identical
    rebuild stop cascading, and what stops a file restored with an old
    timestamp passing as unchanged.

    Args:
        path: A file or directory. The manifest is excluded from a
            directory's digest, since it carries the result.

    Returns:
        The digest, as ``sha256:<hex>``.

    Raises:
        FileNotFoundError: If *path* does not exist. Never a constant
            digest, which would silently disable the staleness chain.
        ContentNotFetchedError: If any file is annexed without its content
            being in this clone, in either of the shapes that takes.
    """
    if path.is_symlink() and not path.exists():
        require_fetched(path)
    if not path.exists():
        raise FileNotFoundError(path)
    h = hashlib.sha256()
    if path.is_file():
        require_fetched(path)
        h.update(b"file:")
        _feed(h, path)
        return f"sha256:{h.hexdigest()}"

    h.update(b"dir:")
    # A dangling symlink is an unfetched *locked* file, and ``is_file()``
    # answers False for one — so filtering on that alone would drop it from
    # the digest without a word, reporting a hash of the subset that happens
    # to be present. Only dangling ones are added back: a symlink that
    # resolves to a file is already a file, and one that resolves to a
    # directory is not content.
    files = [
        p
        for p in path.rglob("*")
        if p.name not in _HASH_EXCLUDE and (p.is_file() or (p.is_symlink() and not p.exists()))
    ]
    for p in sorted(files, key=lambda x: x.relative_to(path).as_posix()):
        require_fetched(p)
        h.update(b"path:")
        h.update(p.relative_to(path).as_posix().encode())
        h.update(b"\0data:")
        _feed(h, p)
        h.update(b"\0")
    return f"sha256:{h.hexdigest()}"


class Versions:
    """Content identities, computed once per run.

    A declared input is hashed once per ``(universe, output)`` that names
    it, which for a multiverse spec is the same bytes over and over: eight
    universes times four outputs sharing one catalog reads it thirty-two
    times. Memoizing is sound for exactly as long as a run lasts — a run
    refuses to start on a dirty tree, and the only in-tree path a recipe
    may write is its own output directory, so a declared input's bytes
    cannot change underneath it.

    A class rather than a closure, so what it keeps alive is one dict and
    not whatever scope built it. Deliberately unlocked: concurrent workers
    can race to compute the same digest, which wastes one hash rather than
    serialising every hash behind a lock — and a lock would not survive
    being handed to a worker in another process.
    """

    def __init__(self) -> None:
        self._known: dict[Path, str] = {}

    def of(self, path: Path) -> str:
        """Return *path*'s content identity, hashing it at most once.

        Args:
            path: A declared input, file or directory.

        Returns:
            The digest, as ``sha256:<hex>``.
        """
        resolved = path.resolve()
        if (known := self._known.get(resolved)) is None:
            known = self._known[resolved] = data_version(path)
        return known


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
    elif path.stat().st_size > _POINTER_MAX_BYTES:
        unfetched = False
    else:
        with path.open("rb") as f:
            unfetched = f.read(len(_POINTER_PREFIX)) == _POINTER_PREFIX
    if unfetched:
        raise ContentNotFetchedError(
            f"{path}: the content is not in this clone — git-annex holds a "
            f"reference to it, not the data. Fetch it with `git annex get {path}`."
        )


def _feed(h: hashlib._Hash, path: Path) -> None:
    """Stream *path* into *h* — outputs are not assumed to fit in memory."""
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)


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
    universe_id: str
    recipe: str
    #: What the spec says this output is: its recipe and decisions. The
    #: rebuild trigger.
    definition_version: str
    #: The environment it was made under. Recorded, never a rebuild
    #: trigger — a difference here makes the output *behind*, not stale.
    env_version: str
    data_version: str
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


def read(directory: Path) -> Manifest | None:
    """Read the manifest in *directory*.

    Args:
        directory: An output directory.

    Returns:
        The manifest, or ``None`` when it is absent or unparseable — which
        the staleness rule reads as "make it again", the safe direction.

    Raises:
        OSError: Deliberately not caught. A permission problem is a real
            fault and must not look like an output needing a rebuild.
    """
    path = directory / MANIFEST_FILENAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
        return Manifest(**{k: v for k, v in data.items() if k in _FIELDS})
    except (json.JSONDecodeError, TypeError):
        return None


def write(directory: Path, manifest: Manifest) -> Path:
    """Write *manifest* into *directory*, atomically.

    The rename is the commit point: a reader sees the previous manifest or
    this one, never half of either.

    Args:
        directory: The output directory to write into.
        manifest: The record to write.

    Returns:
        The path written.
    """
    path = directory / MANIFEST_FILENAME
    temporary = directory / f"{MANIFEST_FILENAME}.tmp"
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
    foreign: str = "",
) -> Verdict:
    """Decide what an output is, relative to the project as it now stands.

    The only place this rule lives. Values rather than objects, because
    the callers arrive at them differently and must not diverge in
    anything else — history included: whether the output's directory was
    last written by its own run record is git's to answer, so it arrives
    here as a value computed by whoever has git (the driver), and the
    rule stays pure.

    Args:
        definition_version: What the spec currently says this output is.
        env_version: The run's environment identity.
        manifest: The output's recorded manifest, or ``None``.
        inputs: Each declared input's current content identity. ``None``
            for an input the caller has already decided will be remade —
            check mode's sentinel, meaning "this is going to change",
            since it cannot know whether a rebuild is byte-identical.
        foreign: Empty when the directory's last commit is the output's
            own run record; otherwise the foreign commit, named. A hit is
            a contradiction — the manifest no longer describes the bytes
            — so it is ``stale``, though an output stale on its
            definition or inputs keeps that more actionable reason.

    Returns:
        ``stale``, ``behind`` or ``current``, with the reason for the
        first two.
    """
    if (reason := _stale(definition_version, manifest, inputs)) is not None:
        return Verdict("stale", str(reason))
    if foreign:
        return Verdict("stale", foreign)
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
