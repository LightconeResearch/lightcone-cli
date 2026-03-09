"""Tests for fsspec staging functions."""
from __future__ import annotations

from pathlib import Path

import pytest

from prism.dagster.staging import (
    _cache_key,
    _is_local,
    _resolve_local,
    stage_uri,
    verify_checksum,
)


class TestIsLocal:
    def test_absolute_path(self):
        assert _is_local("/pscratch/sd/f/francois/data") is True

    def test_relative_dot_slash(self):
        assert _is_local("./data/input.fits") is True

    def test_relative_dot_dot_slash(self):
        assert _is_local("../shared/catalog.hdf5") is True

    def test_https_url(self):
        assert _is_local("https://example.com/data.tar") is False

    def test_s3_uri(self):
        assert _is_local("s3://bucket/key/data.fits") is False

    def test_ssh_uri(self):
        assert _is_local("ssh://host/path/data.fits") is False

    def test_bare_relative(self):
        # Without ./ prefix — not considered local
        assert _is_local("data/input.fits") is False


class TestResolveLocal:
    def test_absolute_path_unchanged(self, tmp_path):
        result = _resolve_local("/pscratch/data", tmp_path)
        assert result == Path("/pscratch/data")

    def test_relative_path_resolved_to_project_root(self, tmp_path):
        result = _resolve_local("./data/input.fits", tmp_path)
        assert result == (tmp_path / "data" / "input.fits").resolve()

    def test_dot_dot_relative(self, tmp_path):
        result = _resolve_local("../shared/catalog.hdf5", tmp_path)
        assert result == (tmp_path / ".." / "shared" / "catalog.hdf5").resolve()


class TestCacheKey:
    def test_with_checksum(self):
        checksum = {"algorithm": "sha256", "value": "abc123"}
        key = _cache_key("https://example.com/data.fits", checksum)
        assert key == "sha256-abc123"

    def test_without_checksum(self):
        key = _cache_key("https://example.com/data.fits", None)
        assert key.startswith("uri-")
        assert len(key) > 10

    def test_same_uri_same_key(self):
        k1 = _cache_key("https://example.com/data.fits", None)
        k2 = _cache_key("https://example.com/data.fits", None)
        assert k1 == k2

    def test_different_uri_different_key(self):
        k1 = _cache_key("https://example.com/a.fits", None)
        k2 = _cache_key("https://example.com/b.fits", None)
        assert k1 != k2


class TestVerifyChecksum:
    def test_matching_checksum(self, tmp_path):
        import hashlib

        data = b"hello world"
        f = tmp_path / "test.dat"
        f.write_bytes(data)
        expected = hashlib.sha256(data).hexdigest()
        checksum = {"algorithm": "sha256", "value": expected}
        assert verify_checksum(f, checksum) is True

    def test_mismatching_checksum(self, tmp_path):
        f = tmp_path / "test.dat"
        f.write_bytes(b"hello world")
        checksum = {"algorithm": "sha256", "value": "0000bad"}
        assert verify_checksum(f, checksum) is False

    def test_empty_value(self, tmp_path):
        f = tmp_path / "test.dat"
        f.write_bytes(b"data")
        assert verify_checksum(f, {"algorithm": "sha256", "value": ""}) is False

    def test_directory_checksum(self, tmp_path):
        import hashlib

        d = tmp_path / "dir"
        d.mkdir()
        (d / "a.txt").write_bytes(b"aaa")
        (d / "b.txt").write_bytes(b"bbb")

        h = hashlib.sha256()
        for fpath in sorted(d.rglob("*")):
            if fpath.is_file():
                h.update(fpath.read_bytes())
        expected = h.hexdigest()

        assert verify_checksum(d, {"algorithm": "sha256", "value": expected}) is True


class TestStageUri:
    def test_stage_local_file_uri(self, tmp_path):
        """Stage a file:// URI (no network needed)."""
        source = tmp_path / "source.dat"
        source.write_bytes(b"test data")
        cache = tmp_path / "cache"

        result = stage_uri(f"file://{source}", cache)
        assert result.exists()
        assert result.read_bytes() == b"test data"

    def test_cache_hit(self, tmp_path):
        """Second call should return cached path without re-fetching."""
        source = tmp_path / "source.dat"
        source.write_bytes(b"test data")
        cache = tmp_path / "cache"

        path1 = stage_uri(f"file://{source}", cache)
        path2 = stage_uri(f"file://{source}", cache)
        assert path1 == path2

    def test_cache_hit_with_checksum(self, tmp_path):
        """Checksum-keyed cache hit."""
        import hashlib

        data = b"test data"
        source = tmp_path / "source.dat"
        source.write_bytes(data)
        cache = tmp_path / "cache"
        checksum = {
            "algorithm": "sha256",
            "value": hashlib.sha256(data).hexdigest(),
        }

        path1 = stage_uri(f"file://{source}", cache, checksum=checksum)
        # Remove source to prove cache is used
        source.unlink()
        path2 = stage_uri(f"file://{source}", cache, checksum=checksum)
        assert path1 == path2
        assert path2.read_bytes() == data

    def test_missing_upath_raises_clear_error(self, tmp_path, monkeypatch):
        """If universal-pathlib is not installed, give a clear error."""
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "upath":
                raise ModuleNotFoundError("No module named 'upath'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        cache = tmp_path / "cache"
        with pytest.raises(RuntimeError, match="universal-pathlib is required"):
            stage_uri("https://example.com/data.fits", cache)
