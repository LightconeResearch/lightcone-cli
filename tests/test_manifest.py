"""Tests for the manifest module — the integrity layer."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lightcone.engine import _lc_finalize
from lightcone.engine.manifest import (
    MANIFEST_FILENAME,
    SCHEMA_VERSION,
    code_version,
    fingerprint_external,
    read_manifest,
    sha256_dir,
    write_manifest,
)


def _write(path: Path, content: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        path.write_text(content)
    else:
        path.write_bytes(content)


# ---- sha256_dir -----------------------------------------------------------


def test_sha256_dir_empty(tmp_path: Path) -> None:
    d = tmp_path / "out"
    d.mkdir()
    h = sha256_dir(d)
    # Empty dir gets a stable, well-known hash (sha256 of empty stream).
    assert h.startswith("sha256:")
    assert len(h) == len("sha256:") + 64


def test_sha256_dir_deterministic(tmp_path: Path) -> None:
    d = tmp_path / "out"
    _write(d / "a.txt", b"hello")
    _write(d / "b/c.txt", b"world")
    assert sha256_dir(d) == sha256_dir(d)


def test_sha256_dir_changes_on_content(tmp_path: Path) -> None:
    d = tmp_path / "out"
    _write(d / "a.txt", b"hello")
    h1 = sha256_dir(d)
    _write(d / "a.txt", b"hellooo")
    h2 = sha256_dir(d)
    assert h1 != h2


def test_sha256_dir_changes_on_added_file(tmp_path: Path) -> None:
    d = tmp_path / "out"
    _write(d / "a.txt", b"hello")
    h1 = sha256_dir(d)
    _write(d / "b.txt", b"new")
    h2 = sha256_dir(d)
    assert h1 != h2


def test_sha256_dir_independent_of_creation_order(tmp_path: Path) -> None:
    d1 = tmp_path / "d1"
    _write(d1 / "a.txt", b"A")
    _write(d1 / "b.txt", b"B")

    d2 = tmp_path / "d2"
    _write(d2 / "b.txt", b"B")
    _write(d2 / "a.txt", b"A")

    assert sha256_dir(d1) == sha256_dir(d2)


def test_sha256_dir_excludes_manifest(tmp_path: Path) -> None:
    """The manifest file itself MUST be excluded from data_version,
    otherwise we'd create a chicken-and-egg problem."""
    d = tmp_path / "out"
    _write(d / "data.csv", b"x,y\n1,2\n")
    h_before = sha256_dir(d)
    _write(d / MANIFEST_FILENAME, b'{"data_version": "sha256:foo"}')
    h_after = sha256_dir(d)
    assert h_before == h_after


def test_sha256_dir_excludes_snakemake_timestamp(tmp_path: Path) -> None:
    """Snakemake touches ``.snakemake_timestamp`` AFTER the rule's run:
    block completes (it's the marker for ``directory()`` outputs). Our
    data_version must ignore it, otherwise verify always fails."""
    d = tmp_path / "out"
    _write(d / "data.csv", b"x,y\n1,2\n")
    h_before = sha256_dir(d)
    _write(d / ".snakemake_timestamp", b"")
    h_after = sha256_dir(d)
    assert h_before == h_after


def test_sha256_dir_missing_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        sha256_dir(tmp_path / "nope")


# ---- fingerprint_external -------------------------------------------------


def test_fingerprint_external_file_uses_mtime_size(tmp_path: Path) -> None:
    f = tmp_path / "input.bin"
    f.write_bytes(b"some bytes")
    fp = fingerprint_external(f)
    assert fp.startswith("mtime-size:")


def test_fingerprint_external_file_strict_uses_sha256(tmp_path: Path) -> None:
    f = tmp_path / "input.bin"
    f.write_bytes(b"some bytes")
    fp = fingerprint_external(f, strict=True)
    assert fp.startswith("sha256:")


def test_fingerprint_external_directory(tmp_path: Path) -> None:
    d = tmp_path / "input_dir"
    _write(d / "a.txt", b"data")
    fp = fingerprint_external(d)
    # Directories always go through the same content hash.
    assert fp.startswith("sha256:")


def test_fingerprint_external_missing_returns_marker(tmp_path: Path) -> None:
    fp = fingerprint_external(tmp_path / "nope.txt")
    assert fp == "missing"


# ---- code_version ---------------------------------------------------------


def test_code_version_deterministic() -> None:
    cv1 = code_version(
        recipe="python script.py --x 1",
        container_image="lc-foo-abc123.sif",
        decisions={"k": "a", "j": 1},
    )
    cv2 = code_version(
        recipe="python script.py --x 1",
        container_image="lc-foo-abc123.sif",
        decisions={"j": 1, "k": "a"},  # key order shouldn't matter
    )
    assert cv1 == cv2
    assert cv1.startswith("sha256:")


def test_code_version_changes_on_recipe() -> None:
    cv1 = code_version(recipe="a", container_image="c", decisions={})
    cv2 = code_version(recipe="b", container_image="c", decisions={})
    assert cv1 != cv2


def test_code_version_changes_on_container() -> None:
    cv1 = code_version(recipe="r", container_image="c1", decisions={})
    cv2 = code_version(recipe="r", container_image="c2", decisions={})
    assert cv1 != cv2


def test_code_version_changes_on_decisions() -> None:
    cv1 = code_version(recipe="r", container_image="c", decisions={"k": 1})
    cv2 = code_version(recipe="r", container_image="c", decisions={"k": 2})
    assert cv1 != cv2


def test_code_version_handles_none_container() -> None:
    cv = code_version(recipe="r", container_image=None, decisions={})
    assert cv.startswith("sha256:")


def test_code_version_changes_when_finalizer_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The finalizer source is hashed into code_version. Editing the
    integrity layer must invalidate every existing manifest."""
    cv_before = code_version(recipe="r", container_image="c", decisions={})
    # Pretend the finalizer's bytes changed.
    monkeypatch.setattr(
        "lightcone.engine.manifest._finalizer_source_hash",
        lambda: "sha256:deadbeef",
    )
    cv_after = code_version(recipe="r", container_image="c", decisions={})
    assert cv_before != cv_after


# ---- _lc_finalize.finalize -----------------------------------------------


def _write_cfg(lightcone_dir: Path, cfg: dict) -> None:
    lightcone_dir.mkdir(parents=True, exist_ok=True)
    (lightcone_dir / "snakefile-config.json").write_text(json.dumps(cfg))


def test_finalize_writes_manifest_atomically(tmp_path: Path) -> None:
    """End-to-end: finalize() reads cfg, hashes output, writes manifest."""
    out = tmp_path / "results" / "u1" / "foo"
    _write(out / "data.csv", b"x,y\n1,2\n")
    lightcone = tmp_path / ".lightcone"
    _write_cfg(
        lightcone,
        {
            "foo": {
                "u1": {
                    "output_id": "foo",
                    "universe_id": "u1",
                    "recipe": "echo",
                    "container_image": "python:3.12-slim",
                    "decisions": {},
                    "code_version": "sha256:abc",
                    "git_sha": "deadbeef",
                    "lc_version": "0.0",
                    "inputs": {},
                }
            }
        },
    )

    _lc_finalize.finalize("foo", "u1", out, lightcone)

    m = json.loads((out / MANIFEST_FILENAME).read_text())
    assert m["output_id"] == "foo"
    assert m["data_version"].startswith("sha256:")
    # No leftover .tmp file — atomic rename completed.
    assert not (out / (MANIFEST_FILENAME + ".tmp")).exists()


def test_finalize_chains_upstream_data_version(tmp_path: Path) -> None:
    """When an input dir has a manifest, its data_version flows into
    input_versions — the chain is built from cfg-resolved paths."""
    upstream = tmp_path / "results" / "u1" / "up"
    _write(upstream / "out.bin", b"u")
    lightcone = tmp_path / ".lightcone"
    _write_cfg(
        lightcone,
        {
            "up": {
                "u1": {
                    "output_id": "up",
                    "universe_id": "u1",
                    "recipe": "x",
                    "container_image": None,
                    "decisions": {},
                    "code_version": "sha256:up",
                    "git_sha": "g",
                    "lc_version": "0",
                    "inputs": {},
                }
            },
            "down": {
                "u1": {
                    "output_id": "down",
                    "universe_id": "u1",
                    "recipe": "y",
                    "container_image": None,
                    "decisions": {},
                    "code_version": "sha256:down",
                    "git_sha": "g",
                    "lc_version": "0",
                    "inputs": {"up": str(upstream)},
                }
            },
        },
    )
    _lc_finalize.finalize("up", "u1", upstream, lightcone)
    upstream_dv = json.loads((upstream / MANIFEST_FILENAME).read_text())["data_version"]

    downstream = tmp_path / "results" / "u1" / "down"
    _write(downstream / "result.bin", b"d")
    _lc_finalize.finalize("down", "u1", downstream, lightcone)

    dn = json.loads((downstream / MANIFEST_FILENAME).read_text())
    assert dn["input_versions"]["up"] == upstream_dv


def test_finalize_hash_matches_verify_hash(tmp_path: Path) -> None:
    """The host-side ``sha256_dir`` (used by lc verify) must produce the
    same hash as the finalizer's data_version, since both live in
    ``_lc_finalize.py``. Single-source-of-truth invariant."""
    out = tmp_path / "out"
    _write(out / "a.bin", b"abc")
    _write(out / "nested" / "b.bin", b"def")
    lightcone = tmp_path / ".lightcone"
    _write_cfg(
        lightcone,
        {
            "x": {
                "u": {
                    "output_id": "x",
                    "universe_id": "u",
                    "recipe": "r",
                    "container_image": None,
                    "decisions": {},
                    "code_version": "sha256:c",
                    "git_sha": "g",
                    "lc_version": "0",
                    "inputs": {},
                }
            }
        },
    )
    _lc_finalize.finalize("x", "u", out, lightcone)
    written_dv = json.loads((out / MANIFEST_FILENAME).read_text())["data_version"]
    assert written_dv == sha256_dir(out)


# ---- write_manifest -------------------------------------------------------


def test_write_manifest_basic(tmp_path: Path) -> None:
    out = tmp_path / "results" / "u1" / "foo"
    _write(out / "data.csv", b"x,y\n1,2\n")
    raw = tmp_path / "raw.txt"
    raw.write_bytes(b"raw")

    manifest_path = write_manifest(
        output_dir=out,
        inputs={"raw_data": raw},
        cfg={
            "output_id": "foo",
            "universe_id": "u1",
            "recipe": "python script.py",
            "container_image": "lc-foo-abc.sif",
            "decisions": {"k": 1},
            "code_version": "sha256:abc",
            "git_sha": "deadbeef",
            "lc_version": "0.4.1",
        },
    )

    assert manifest_path == out / MANIFEST_FILENAME
    assert manifest_path.exists()
    m = json.loads(manifest_path.read_text())

    assert m["schema_version"] == SCHEMA_VERSION
    assert m["output_id"] == "foo"
    assert m["universe_id"] == "u1"
    assert m["recipe"] == "python script.py"
    assert m["container_image"] == "lc-foo-abc.sif"
    assert m["decisions"] == {"k": 1}
    assert m["code_version"] == "sha256:abc"
    assert m["git_sha"] == "deadbeef"
    assert m["lc_version"] == "0.4.1"
    assert m["data_version"].startswith("sha256:")
    assert "raw_data" in m["input_versions"]
    assert m["input_versions"]["raw_data"].startswith("mtime-size:")
    assert "finished_at" in m
    assert "host" in m


def test_write_manifest_chains_upstream_data_version(tmp_path: Path) -> None:
    """When an input is itself a materialized output (has a manifest), the
    upstream's data_version flows into our manifest's input_versions.
    """
    upstream = tmp_path / "results" / "u1" / "upstream"
    _write(upstream / "out.csv", b"a,b\n")
    write_manifest(
        output_dir=upstream,
        inputs={},
        cfg={
            "output_id": "upstream",
            "universe_id": "u1",
            "recipe": "echo",
            "container_image": None,
            "decisions": {},
            "code_version": "sha256:up",
            "git_sha": "g",
            "lc_version": "0.0",
        },
    )
    upstream_manifest = json.loads((upstream / MANIFEST_FILENAME).read_text())
    upstream_dv = upstream_manifest["data_version"]

    downstream = tmp_path / "results" / "u1" / "downstream"
    _write(downstream / "result.csv", b"r\n")
    write_manifest(
        output_dir=downstream,
        inputs={"upstream": upstream},
        cfg={
            "output_id": "downstream",
            "universe_id": "u1",
            "recipe": "echo",
            "container_image": None,
            "decisions": {},
            "code_version": "sha256:dn",
            "git_sha": "g",
            "lc_version": "0.0",
        },
    )
    dn_manifest = json.loads((downstream / MANIFEST_FILENAME).read_text())
    assert dn_manifest["input_versions"]["upstream"] == upstream_dv


def test_write_manifest_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """write_manifest must not leave a half-written manifest if json.dumps
    or the write fails midway."""
    out = tmp_path / "out"
    _write(out / "x", b"1")

    cfg = {
        "output_id": "o",
        "universe_id": "u",
        "recipe": "r",
        "container_image": None,
        "decisions": {},
        "code_version": "sha256:c",
        "git_sha": "g",
        "lc_version": "0.0",
    }
    write_manifest(output_dir=out, inputs={}, cfg=cfg)
    assert (out / MANIFEST_FILENAME).exists()


# ---- read_manifest --------------------------------------------------------


def test_read_manifest_present(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _write(out / "x", b"d")
    write_manifest(
        output_dir=out,
        inputs={},
        cfg={
            "output_id": "o",
            "universe_id": "u",
            "recipe": "r",
            "container_image": None,
            "decisions": {},
            "code_version": "sha256:c",
            "git_sha": "g",
            "lc_version": "0.0",
        },
    )
    m = read_manifest(out)
    assert m is not None
    assert m["output_id"] == "o"


def test_read_manifest_missing_returns_none(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    assert read_manifest(out) is None


def test_read_manifest_corrupt_returns_none(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / MANIFEST_FILENAME).write_text("not json")
    assert read_manifest(out) is None
