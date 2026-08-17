"""Tests for the manifest module — the integrity layer."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lightcone.engine.manifest import (
    MANIFEST_FILENAME,
    SCHEMA_VERSION,
    code_version,
    fingerprint_external,
    is_pre_migration,
    read_manifest,
    sha256_dir,
    write_manifest,
)

_ENV = "sha256:" + "ee" * 32


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
    assert fp.startswith("sha256:")


def test_fingerprint_external_missing_returns_marker(tmp_path: Path) -> None:
    fp = fingerprint_external(tmp_path / "nope.txt")
    assert fp == "missing"


# ---- code_version ---------------------------------------------------------


def test_code_version_deterministic() -> None:
    cv1 = code_version(
        recipe="python script.py --x 1",
        decisions={"k": "a", "j": 1},
        env_version=_ENV,
    )
    cv2 = code_version(
        recipe="python script.py --x 1",
        decisions={"j": 1, "k": "a"},
        env_version=_ENV,
    )
    assert cv1 == cv2
    assert cv1.startswith("sha256:")


def test_code_version_changes_on_recipe() -> None:
    cv1 = code_version(recipe="a", decisions={}, env_version=_ENV)
    cv2 = code_version(recipe="b", decisions={}, env_version=_ENV)
    assert cv1 != cv2


def test_code_version_changes_on_env_version() -> None:
    cv1 = code_version(recipe="r", decisions={}, env_version=_ENV)
    cv2 = code_version(recipe="r", decisions={}, env_version="sha256:" + "ff" * 32)
    assert cv1 != cv2


def test_code_version_changes_on_decisions() -> None:
    cv1 = code_version(recipe="r", decisions={"k": 1}, env_version=_ENV)
    cv2 = code_version(recipe="r", decisions={"k": 2}, env_version=_ENV)
    assert cv1 != cv2


def test_code_version_changes_on_writable_project() -> None:
    """The per-output sandbox escalation is materialization-relevant —
    but per-output: it moves only this output's code_version."""
    cv1 = code_version(recipe="r", decisions={}, env_version=_ENV)
    cv2 = code_version(
        recipe="r", decisions={}, env_version=_ENV, writable_project=True
    )
    assert cv1 != cv2


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
            "env_version": _ENV,
            "decisions": {"k": 1},
            "code_version": "sha256:abc",
            "git_sha": "deadbeef",
            "git_remote": "https://github.com/dkn16/test-repo",
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
    assert m["env_version"] == _ENV
    assert m["decisions"] == {"k": 1}
    assert m["code_version"] == "sha256:abc"
    assert m["git_sha"] == "deadbeef"
    assert m["git_remote"] == "https://github.com/dkn16/test-repo"
    assert m["lc_version"] == "0.4.1"
    assert m["data_version"].startswith("sha256:")
    assert "raw_data" in m["input_versions"]
    assert m["input_versions"]["raw_data"].startswith("mtime-size:")
    assert "finished_at" in m
    assert "host" in m
    assert m["worker_runtime"] == "host"
    assert m["image"] is None
    assert m["dpkg_snapshot_sha256"] is None
    assert m["sdist_built"] == []
    # No enforcement ran and none was claimed — the honest default.
    assert m["hermeticity"] == {
        "mechanism": "none", "fs": "open", "network": "allowed",
    }


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
            "decisions": {},
            "code_version": "sha256:up",
            "env_version": _ENV,
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
            "decisions": {},
            "code_version": "sha256:dn",
            "env_version": _ENV,
            "git_sha": "g",
            "lc_version": "0.0",
        },
    )
    dn_manifest = json.loads((downstream / MANIFEST_FILENAME).read_text())
    assert dn_manifest["input_versions"]["upstream"] == upstream_dv


def test_write_manifest_atomic(tmp_path: Path) -> None:
    """No leftover .tmp file after a successful write."""
    out = tmp_path / "out"
    _write(out / "x", b"1")

    cfg = {
        "output_id": "o",
        "universe_id": "u",
        "recipe": "r",
        "decisions": {},
        "code_version": "sha256:c",
        "env_version": _ENV,
        "git_sha": "g",
        "lc_version": "0.0",
    }
    write_manifest(output_dir=out, inputs={}, cfg=cfg)
    assert (out / MANIFEST_FILENAME).exists()
    assert not (out / (MANIFEST_FILENAME + ".tmp")).exists()


def test_write_manifest_data_version_matches_sha256_dir(tmp_path: Path) -> None:
    """The data_version recorded by write_manifest is the same hash that
    ``sha256_dir`` would compute — single source of truth for verify."""
    out = tmp_path / "out"
    _write(out / "a.bin", b"abc")
    _write(out / "nested" / "b.bin", b"def")
    write_manifest(
        output_dir=out,
        inputs={},
        cfg={
            "output_id": "x",
            "universe_id": "u",
            "recipe": "r",
            "decisions": {},
            "code_version": "sha256:c",
        "env_version": _ENV,
            "git_sha": "g",
            "lc_version": "0",
        },
    )
    written_dv = json.loads((out / MANIFEST_FILENAME).read_text())["data_version"]
    assert written_dv == sha256_dir(out)


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
            "decisions": {},
            "code_version": "sha256:c",
        "env_version": _ENV,
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


def test_read_manifest_propagates_oserror(tmp_path: Path) -> None:
    """Permission errors must surface, not be silently confused with a
    missing manifest by ``lc verify`` / ``lc status``."""
    import os
    import sys

    if sys.platform == "win32" or os.geteuid() == 0:
        pytest.skip("permission test requires non-root POSIX")

    out = tmp_path / "out"
    out.mkdir()
    manifest_path = out / MANIFEST_FILENAME
    manifest_path.write_text("{}")
    manifest_path.chmod(0o000)
    try:
        with pytest.raises(PermissionError):
            read_manifest(out)
    finally:
        manifest_path.chmod(0o644)



# ---- schema v2 ------------------------------------------------------------


def test_manifest_v2_field_list_golden(tmp_path: Path) -> None:
    """The normative field enumeration (spec §3) — the single list the
    SCHEMA_VERSION bump implements. A failure here means the schema
    changed without a conscious decision."""
    out = tmp_path / "out"
    _write(out / "x", b"1")
    write_manifest(
        output_dir=out,
        inputs={},
        cfg={
            "output_id": "o", "universe_id": "u", "recipe": "r",
            "decisions": {}, "code_version": "sha256:c", "env_version": _ENV,
        },
        attestation={
            "uv_version": "0.12.3",
            "platform": {"os_release": "x", "kernel": "k", "glibc": "g", "arch": "a"},
            "python_build": "CPython 3.12.12",
            "env_snapshot": {"locale": None, "tz": None},
            "gpu_driver": None,
        },
    )
    m = json.loads((out / MANIFEST_FILENAME).read_text())
    assert sorted(m) == [
        "code_version", "data_version", "decisions", "dpkg_snapshot_sha256",
        "env_snapshot", "env_version", "finished_at", "git_dirty",
        "git_remote", "git_sha", "gpu_driver", "hermeticity", "host",
        "image", "input_versions", "lc_version", "output_id", "platform",
        "python_build", "recipe", "schema_version", "sdist_built",
        "universe_id", "uv_version", "worker_runtime",
    ]


def test_write_manifest_records_hermeticity(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _write(out / "x", b"1")
    write_manifest(
        output_dir=out,
        inputs={},
        cfg={
            "output_id": "o", "universe_id": "u", "recipe": "r",
            "decisions": {}, "code_version": "sha256:c", "env_version": _ENV,
        },
        hermeticity={
            "mechanism": "landlock", "fs": "declared",
            "network": "unenforced", "landlock_abi": 9,
        },
    )
    m = json.loads((out / MANIFEST_FILENAME).read_text())
    assert m["hermeticity"]["mechanism"] == "landlock"
    assert m["hermeticity"]["landlock_abi"] == 9


def test_write_manifest_records_image(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _write(out / "x", b"1")
    write_manifest(
        output_dir=out,
        inputs={},
        cfg={
            "output_id": "o", "universe_id": "u", "recipe": "r",
            "decisions": {}, "code_version": "sha256:c", "env_version": _ENV,
            "worker_runtime": "container",
            "image_tag": "lc-env-abcd", "image_digest": "sha256:dd",
            "dpkg_snapshot_sha256": "ee",
        },
    )
    m = json.loads((out / MANIFEST_FILENAME).read_text())
    assert m["worker_runtime"] == "container"
    assert m["image"] == {"tag": "lc-env-abcd", "digest": "sha256:dd"}
    assert m["dpkg_snapshot_sha256"] == "ee"


def test_is_pre_migration() -> None:
    assert is_pre_migration({"schema_version": 1, "code_version": "x"})
    assert is_pre_migration({})
    assert not is_pre_migration(
        {"schema_version": SCHEMA_VERSION, "env_version": _ENV}
    )
