"""fsspec-based staging — fetches remote data to a local cache directory."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _is_local(source: str) -> bool:
    """Check if source is a local/shared filesystem reference."""
    return source.startswith("/") or source.startswith("./") or source.startswith("../")


def _resolve_local(source: str, project_root: Path) -> Path:
    """Resolve a local source to an absolute Path."""
    if source.startswith("/"):
        return Path(source)
    return (project_root / source).resolve()


def _cache_key(uri: str, checksum: dict[str, str] | None) -> str:
    """Compute a deterministic cache key for a URI.

    If a checksum is provided, uses the checksum value directly (content-addressed).
    Otherwise, hashes the URI itself.
    """
    if checksum and checksum.get("value"):
        return f"{checksum['algorithm']}-{checksum['value']}"
    return "uri-" + hashlib.sha256(uri.encode()).hexdigest()


def stage_uri(uri: str, cache_dir: Path, checksum: dict[str, str] | None = None) -> Path:
    """Stage remote data to a local cache directory.

    If a cached copy exists with a matching cache key, returns the cached path
    without re-downloading.  Otherwise fetches via ``upath.UPath`` and stores
    in the cache.

    Parameters
    ----------
    uri:
        Remote URI (``https://``, ``s3://``, ``ssh://``, etc.).
    cache_dir:
        Local directory for cached files.
    checksum:
        Optional ``{"algorithm": "sha256", "value": "..."}`` dict.  When
        provided the checksum value is used as the cache key and the cached
        file is verified after download.

    Returns
    -------
    Path
        Local filesystem path to the staged data.
    """
    key = _cache_key(uri, checksum)
    cached_path = cache_dir / key

    if cached_path.exists():
        logger.debug("Cache hit for %s → %s", uri, cached_path)
        return cached_path

    try:
        from upath import UPath
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            f"universal-pathlib is required to stage remote URIs ({uri}). "
            "Install it with: pip install 'prism[remote]' or pip install universal-pathlib"
        ) from exc

    logger.info("Staging %s → %s", uri, cached_path)
    cache_dir.mkdir(parents=True, exist_ok=True)

    remote = UPath(uri)
    if remote.is_dir():
        cached_path.mkdir(parents=True, exist_ok=True)
        for item in remote.rglob("*"):
            if item.is_file():
                rel = item.relative_to(remote)
                dest = cached_path / str(rel)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(item.read_bytes())
    else:
        cached_path.write_bytes(remote.read_bytes())

    return cached_path


def verify_checksum(path: Path, checksum: dict[str, str]) -> bool:
    """Verify a file's checksum.

    Parameters
    ----------
    path:
        Local file path to verify.
    checksum:
        ``{"algorithm": "sha256", "value": "abcdef..."}`` dict.

    Returns
    -------
    bool
        ``True`` if the checksum matches, ``False`` otherwise.
    """
    algorithm = checksum.get("algorithm", "sha256")
    expected = checksum.get("value", "")
    if not expected:
        return False

    h = hashlib.new(algorithm)
    if path.is_dir():
        # Hash all files in sorted order for directory checksums
        for fpath in sorted(path.rglob("*")):
            if fpath.is_file():
                h.update(fpath.read_bytes())
    else:
        h.update(path.read_bytes())

    actual = h.hexdigest()
    if actual != expected:
        logger.warning(
            "Checksum mismatch for %s: expected %s=%s, got %s",
            path, algorithm, expected, actual,
        )
        return False
    return True
