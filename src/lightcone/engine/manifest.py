"""Per-output content-addressed manifests.

The integrity layer of lightcone-cli. Every materialized output gets a
sidecar JSON manifest at ``<output_dir>/.lightcone-manifest.json`` that
records:

- ``code_version``: sha256(recipe + container image + decisions + finalizer
  source). Embedded in the rule's shell command so Snakemake's ``code``
  rerun-trigger detects drift automatically.
- ``data_version``: sha256 of the output directory's contents. Lets
  ``lc verify`` prove the bytes on disk are what the manifest claims.
- ``input_versions``: each declared input's ``data_version`` (if it's a
  materialized output) or ``(mtime, size)`` fingerprint (if it's an
  external file). This is the chain.

Manifests are written by ``_lc_finalize.py``, the standalone script that
runs at the end of every rule's containerized shell recipe. This module
exposes the host-side helpers (``code_version``, ``read_manifest``,
``fingerprint_external``) and re-exports ``sha256_dir`` and
``MANIFEST_FILENAME`` from the finalizer so verify and finalize hash data
the exact same way.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

# Single source of truth for the hash algorithm and manifest filename.
# The finalizer is the canonical implementation; we import here so
# ``lc verify`` recomputes data_version exactly the way the rule wrote it.
from lightcone.engine._lc_finalize import (
    MANIFEST_FILENAME,
    SCHEMA_VERSION,
    _hash_file,
    sha256_dir,
)

__all__ = [
    "MANIFEST_FILENAME",
    "SCHEMA_VERSION",
    "code_version",
    "fingerprint_external",
    "read_manifest",
    "sha256_dir",
    "write_manifest",
]


def _sha256_bytes(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    _hash_file(path, h)
    return f"sha256:{h.hexdigest()}"


def fingerprint_external(path: Path, *, strict: bool = False) -> str:
    """Fingerprint an external input.

    For files: ``(mtime, size)`` by default; sha256 when ``strict=True``.
    For directories: always sha256.
    For missing paths: returns the literal string ``"missing"``.
    """
    if not path.exists():
        return "missing"
    if path.is_dir():
        return sha256_dir(path)
    if strict:
        return _sha256_file(path)
    st = path.stat()
    return f"mtime-size:{st.st_mtime_ns}-{st.st_size}"


def _finalizer_source_hash() -> str:
    """Hash the bytes of ``_lc_finalize.py`` so that any change to the
    integrity layer invalidates every existing manifest."""
    from lightcone.engine import _lc_finalize

    src = Path(_lc_finalize.__file__).read_bytes()
    return _sha256_bytes(src)


def code_version(
    *,
    recipe: str,
    container_image: str | None,
    decisions: dict[str, Any],
) -> str:
    """Compute a deterministic code version for an output.

    Hashes the recipe text, container image identifier, canonicalized
    decisions, and the finalizer source. Anything that changes the
    materialization semantics — including the manifest schema itself —
    flows through this hash.
    """
    payload = {
        "recipe": recipe,
        "container_image": container_image,
        "decisions": decisions,
        "finalizer": _finalizer_source_hash(),
    }
    return _sha256_bytes(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def read_manifest(output_dir: Path) -> dict[str, Any] | None:
    """Read the manifest at ``<output_dir>/.lightcone-manifest.json``.

    Returns ``None`` if the manifest is missing or unparseable.
    """
    p = Path(output_dir) / MANIFEST_FILENAME
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())  # type: ignore[no-any-return]
    except (json.JSONDecodeError, OSError):
        return None


def write_manifest(
    *,
    output_dir: Path,
    inputs: dict[str, Path],
    cfg: dict[str, Any],
) -> Path:
    """Write a manifest for an already-materialized output.

    This is a host-side helper used primarily by tests to construct
    fixture manifests. The production write path is
    ``_lc_finalize.finalize`` invoked from the rule's ``shell:`` recipe.
    Both produce manifests with identical schema and field semantics.

    Args:
        output_dir: Directory containing the materialized output files.
        inputs: Mapping of declared input id → filesystem path. Each is
            either a directory containing a sibling manifest (upstream
            output) or an external file/dir.
        cfg: Per-rule configuration. Required keys: ``output_id``,
            ``universe_id``, ``recipe``, ``container_image``, ``decisions``,
            ``code_version``, ``git_sha``, ``lc_version``.
    """
    import os
    import socket
    import time

    output_dir = Path(output_dir)

    input_versions: dict[str, str] = {}
    for inp_id, inp_path in inputs.items():
        inp_path = Path(inp_path)
        upstream = read_manifest(inp_path)
        if upstream is not None:
            input_versions[inp_id] = upstream["data_version"]
        else:
            input_versions[inp_id] = fingerprint_external(inp_path)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "output_id": cfg["output_id"],
        "universe_id": cfg["universe_id"],
        "code_version": cfg["code_version"],
        "data_version": sha256_dir(output_dir),
        "container_image": cfg.get("container_image"),
        "recipe": cfg["recipe"],
        "decisions": cfg.get("decisions", {}),
        "input_versions": input_versions,
        "git_sha": cfg.get("git_sha"),
        "lc_version": cfg.get("lc_version"),
        "finished_at": time.time(),
        "host": socket.gethostname(),
        "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    }

    final_path = output_dir / MANIFEST_FILENAME
    tmp_path = final_path.with_suffix(final_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(manifest, sort_keys=True, indent=2))
    os.replace(tmp_path, final_path)
    return final_path
