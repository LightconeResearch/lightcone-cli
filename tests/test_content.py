"""Tests for `lightcone.engine.content` — what a file holds, and its hash.

A stand-in reads perfectly well, so every shape one takes is pinned here
as refused, and real content that merely looks like one as passed.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from lightcone.engine import content

# ---- content that is not there ---------------------------------------------


def test_an_unfetched_annexed_file_is_refused(tmp_path: Path) -> None:
    """`filter=annex` leaves a pointer file where the content would be, so
    the path *exists* and is readable — using it would be a well-formed
    answer to the wrong question."""
    pointer = tmp_path / "catalog.fits"
    pointer.write_text("/annex/objects/SHA256E-s300000--4367c4a63392fa9b887bbcf046033d89.fits\n")

    with pytest.raises(content.ContentNotFetchedError, match="not in this clone"):
        content.require_fetched(pointer)


def test_an_unfetched_locked_file_is_refused_like_an_unfetched_pointer(tmp_path: Path) -> None:
    """The other shape an annexed file takes. A researcher may run `git
    annex lock`, or set `annex.thin`, whenever they like — so detection
    cannot depend on which one lc's own writes happen to produce."""
    locked = tmp_path / "catalog.fits"
    locked.symlink_to("../.git/annex/objects/2K/9P/SHA256E-s300000--4367c4a6.fits")

    assert locked.is_symlink() and not locked.exists()
    with pytest.raises(content.ContentNotFetchedError, match="not in this clone"):
        content.require_fetched(locked)


def test_a_fetched_file_passes_in_either_shape(tmp_path: Path) -> None:
    fetched = tmp_path / "object.fits"
    fetched.write_text("x,y\n1,2\n")
    locked = tmp_path / "catalog.fits"
    locked.symlink_to(fetched)

    content.require_fetched(fetched)
    content.require_fetched(locked)


def test_a_broken_symlink_that_is_not_annexed_is_not_called_unfetched(tmp_path: Path) -> None:
    """Not git-annex's doing, so not `git annex get`'s to fix — whatever
    opens the file reports it."""
    stray = tmp_path / "scratch.dat"
    stray.symlink_to(tmp_path / "gone-with-the-scratch-dir")

    content.require_fetched(stray)


def test_a_git_lfs_pointer_is_refused_too(tmp_path: Path) -> None:
    """lc fetches nothing from LFS, and a recipe handed the pointer would
    read three lines of text for its data."""
    pointer = tmp_path / "catalog.fits"
    pointer.write_text(
        "version https://git-lfs.github.com/spec/v1\n"
        "oid sha256:4d7a214614ab2935c943f9e0ff69d22eadbb8f32b1258daaa5e2ca24d17e2393\n"
        "size 12345\n"
    )

    with pytest.raises(content.ContentNotFetchedError, match="git lfs pull"):
        content.require_fetched(pointer)


def test_a_file_that_merely_mentions_the_prefix_is_content(tmp_path: Path) -> None:
    """The test is the prefix at the very start, as git-annex's own is —
    a script that talks about annex paths is not a pointer."""
    script = tmp_path / "fit.py"
    script.write_text("# reads /annex/objects/ sometimes\nprint(1)\n")

    content.require_fetched(script)


def test_a_large_file_is_never_a_pointer(tmp_path: Path) -> None:
    """git-annex's own bound: a pointer is small, so real content that
    happens to begin like one is still content."""
    big = tmp_path / "big.bin"
    big.write_bytes(b"/annex/objects/" + b"0" * (32 * 1024))

    content.require_fetched(big)


# ---- content, as a record names it -----------------------------------------


@pytest.mark.parametrize("data", [b"", b"x,y\n1,2\n", b"\x00" * (3 << 20)])
def test_a_file_is_hashed_the_way_a_record_names_content(tmp_path: Path, data: bytes) -> None:
    """What a record says of bytes is the runner's to find out: the
    SHA-256 under its algorithm's name, and the size, of the bytes
    themselves."""
    path = tmp_path / "result.bin"
    path.write_bytes(data)

    assert content.digest_file(path) == (f"sha256:{hashlib.sha256(data).hexdigest()}", len(data))


def test_hashing_a_file_that_is_not_there_raises(tmp_path: Path) -> None:
    """The caller words the failure: an input that cannot be read and a
    result that was never written are different sentences."""
    with pytest.raises(OSError):
        content.digest_file(tmp_path / "gone.csv")
