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

MANIFEST_FILENAME = ".lightcone-manifest.json"
SCHEMA_VERSION = 1

#: Excluded from the content hash: the manifest is written *after* the
#: hash it contains, so hashing it would be circular.
_HASH_EXCLUDE = frozenset({MANIFEST_FILENAME})


def output_dir(root: Path, universe_id: str, output_id: str) -> Path:
    """Where a ``(universe, output)`` pair's bytes live.

    Path-addressed, and the path in a rendered recipe is this path — no
    staging, no scratch, no relocation.
    """
    return root / "results" / universe_id / output_id


# =============================================================================
# Content identity
# =============================================================================


def data_version(path: Path) -> str:
    """The content identity of *path* — its bytes, and nothing else about it.

    A directory hashes each file in sorted relative-path order, with the
    path fed in beside the bytes so a rename moves the digest. A file
    hashes its own bytes. The two are framed differently, so a directory
    holding one file can never collide with that file on its own.

    Never mtime or size. A content hash is what lets a byte-identical
    rebuild stop invalidating everything downstream, and what stops a file
    restored with an old timestamp from passing as unchanged.
    """
    if not path.exists():
        raise FileNotFoundError(path)
    h = hashlib.sha256()
    if path.is_file():
        h.update(b"file:")
        _feed(h, path)
        return f"sha256:{h.hexdigest()}"

    h.update(b"dir:")
    files = [p for p in path.rglob("*") if p.is_file() and p.name not in _HASH_EXCLUDE]
    for p in sorted(files, key=lambda x: x.relative_to(path).as_posix()):
        h.update(b"path:")
        h.update(p.relative_to(path).as_posix().encode())
        h.update(b"\0data:")
        _feed(h, p)
        h.update(b"\0")
    return f"sha256:{h.hexdigest()}"


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
        """The manifest as JSON-ready data, ``schema_version`` first."""
        data = asdict(self)
        return {"schema_version": data.pop("schema_version"), **data}


def read(directory: Path) -> Manifest | None:
    """The manifest in *directory*, or ``None`` if there isn't a usable one.

    Absent or unparseable both come back as ``None``, which the staleness
    rule reads as "make it again" — the safe direction. ``OSError`` is
    deliberately not caught: a permission problem is a real fault and must
    not disguise itself as an output that simply needs rebuilding.
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
    this one, never half of either. It has to be complete before the driver
    saves the directory, which is why the content hash it carries is
    computed before the save rather than from what the save produced.
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

    kind: Literal["missing", "code", "input"]
    #: Which declared input moved, when ``kind`` is ``input``.
    input: str = ""

    def __str__(self) -> str:
        if self.kind == "missing":
            return "no manifest — it has never been materialized"
        if self.kind == "code":
            return "the recipe, its decisions, or the environment changed"
        return f"the input `{self.input}` changed"


def staleness(
    *,
    code_version: str,
    manifest: Manifest | None,
    inputs: Mapping[str, str | None],
) -> Reason | None:
    """Why *manifest* no longer describes a current output, or ``None``.

    Values, not objects, on purpose: this is a pure comparison, and both of
    its callers arrive at those values differently. The worker passes each
    input's live content identity, taken from what its upstream just
    returned. ``--check`` passes ``None`` for any input it has already
    decided will be rebuilt, meaning "this is going to change" — it cannot
    know whether a rebuild will come out byte-identical, and stopping the
    cascade there would under-report.

    That ``None`` is the whole of the difference between the two callers:
    one input value, conservatively chosen, rather than a second body of
    logic that could disagree with this one.
    """
    if manifest is None:
        return Reason("missing")
    if manifest.code_version != code_version:
        return Reason("code")
    for name, current in inputs.items():
        if current is None or manifest.input_versions.get(name) != current:
            return Reason("input", name)
    return None
