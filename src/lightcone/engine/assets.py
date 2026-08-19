"""What a materialized output *is*: where it lives, what it records, and
whether it is still current.

An asset is a directory — ``results/<universe>/<output_id>/`` — holding
whatever the recipe wrote, plus a manifest beside it. The manifest is the
only part lc writes itself, and it is kept out of the annex so it stays
readable on a clone that has fetched no content at all.

The staleness rule lives here too, next to the two hashes it compares,
because it is the one place in the layer where a bug is quiet rather than
loud: a rule that under-reports leaves an output silently describing bytes
that no longer follow from its inputs. It is a **content-hash** rule, so a
byte-identical rebuild stops the cascade and a restored file with an old
mtime cannot hide.
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
        _refuse_unfetched(path)
    if not path.exists():
        raise FileNotFoundError(path)
    h = hashlib.sha256()
    if path.is_file():
        _refuse_unfetched(path)
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
        _refuse_unfetched(p)
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


def _refuse_unfetched(path: Path) -> None:
    """Refuse a file whose content this clone does not hold.

    An annexed file takes one of two shapes, and a researcher can convert
    between them whenever they like — so both are checked rather than
    whichever one lc's own writes produce. An *unlocked* file is a small
    regular file holding the object's path, hard-linked to the object or
    copied from it depending on ``annex.thin``; both look identical without
    the content. A *locked* file is a symlink into the object store, which
    without the content simply dangles.

    Args:
        path: A file about to be hashed.

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
    code_version: str
    env_version: str
    data_version: str
    decisions: dict[str, str]
    input_versions: dict[str, str]
    #: The commit the working tree was at. Recorded, never hashed into
    #: `code_version` — a commit must not stale every output in the
    #: repository, and this is what makes the code that produced a result
    #: recoverable anyway.
    git_sha: str
    #: The `origin` URL, or empty when the repository has no remote.
    git_remote: str
    lc_version: str
    #: What the sandbox actually enforced, as the boundary attested it.
    hermeticity: dict[str, Any]
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
# Staleness — one predicate, and it is the only place the rule lives
# =============================================================================


@dataclass(frozen=True)
class Reason:
    """Why an output would be made again."""

    kind: Literal["missing", "code", "declaration", "input"]
    #: Which declared input it was about, for the two input kinds.
    input: str = ""

    def __str__(self) -> str:
        if self.kind == "missing":
            return "no manifest — it has never been materialized"
        if self.kind == "code":
            return "the recipe, its decisions, or the environment changed"
        if self.kind == "declaration":
            return f"the output no longer declares the same inputs (`{self.input}`)"
        return f"the input `{self.input}` changed"


def staleness(
    *,
    code_version: str,
    manifest: Manifest | None,
    inputs: Mapping[str, str | None],
) -> Reason | None:
    """Decide whether an output still describes what it was made from.

    The only place this rule lives. Values rather than objects, because the
    two callers arrive at them differently and must not diverge in
    anything else.

    Args:
        code_version: The task's current identity.
        manifest: The output's recorded manifest, or ``None``.
        inputs: Each declared input's current content identity. ``None``
            for an input the caller has already decided will be rebuilt —
            ``--check``'s sentinel, meaning "this is going to change",
            since it cannot know whether a rebuild is byte-identical.

    Returns:
        Why the output would be made again, or ``None`` if it is current.
    """
    if manifest is None:
        return Reason("missing")
    if manifest.code_version != code_version:
        return Reason("code")
    # The *set* first, and separately: `code_version` hashes the recipe, the
    # decisions and the environment, so an input the spec no longer declares
    # moves none of them and the loop below would never look at it. Adding
    # one is caught either way; dropping one is only caught here.
    if changed := set(inputs) ^ set(manifest.input_versions):
        return Reason("declaration", sorted(changed)[0])
    for name, current in inputs.items():
        if current is None or manifest.input_versions[name] != current:
            return Reason("input", name)
    return None
