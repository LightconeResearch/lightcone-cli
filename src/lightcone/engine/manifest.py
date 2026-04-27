"""Per-output content-addressed manifests.

The integrity layer of lightcone-cli. Every materialized output gets a
sidecar JSON manifest at ``<output_dir>/.lightcone-manifest.json`` that
records:

- ``code_version``: sha256(recipe + container image + decisions). Stored
  in each rule's per-universe ``params.cfg`` so Snakemake's ``params``
  rerun-trigger detects drift automatically. (The ``code`` trigger only
  sees the rule body source, which is universe-parameterized and never
  changes — that is why ``lc run`` defaults to including ``params``.)
- ``data_version``: sha256 of the output directory's contents. Lets
  ``lc verify`` prove the bytes on disk are what the manifest claims.
- ``input_versions``: each declared input's ``data_version`` (if it's a
  materialized output) or ``(mtime, size)`` fingerprint (if it's an
  external file). This is the chain.

Manifests are written by :func:`write_manifest`, called from each rule's
``run:`` block on the host immediately after the recipe shell exits. The
``os.replace`` rename is the atomic commit point: either the rule produced
both data and manifest, or it failed and Snakemake will rerun it.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import time
from pathlib import Path
from typing import Any

MANIFEST_FILENAME = ".lightcone-manifest.json"
SCHEMA_VERSION = 1

#: Filenames inside an output directory that the data_version hash MUST
#: ignore: the manifest itself (chicken-and-egg) and Snakemake's
#: ``directory()`` mtime marker (touched AFTER the rule body completes).
_HASH_EXCLUDE = frozenset({MANIFEST_FILENAME, ".snakemake_timestamp"})

__all__ = [
    "MANIFEST_FILENAME",
    "SCHEMA_VERSION",
    "code_version",
    "fingerprint_external",
    "read_manifest",
    "sha256_dir",
    "write_manifest",
]


def _hash_file(path: Path, h: hashlib._Hash) -> None:
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)


def sha256_dir(path: Path) -> str:
    """Deterministic content hash of a directory.

    Walks ``path`` recursively, hashes each file along with its relative
    path (so renames change the hash), and excludes the manifest plus
    Snakemake's directory-output timestamp marker.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    h = hashlib.sha256()
    files: list[Path] = [
        p for p in path.rglob("*") if p.is_file() and p.name not in _HASH_EXCLUDE
    ]
    for p in sorted(files, key=lambda x: x.relative_to(path).as_posix()):
        rel = p.relative_to(path).as_posix().encode("utf-8")
        h.update(b"path:")
        h.update(rel)
        h.update(b"\0")
        h.update(b"data:")
        _hash_file(p, h)
        h.update(b"\0")
    return f"sha256:{h.hexdigest()}"


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


def code_version(
    *,
    recipe: str,
    container_image: str | None,
    decisions: dict[str, Any],
) -> str:
    """Compute a deterministic code version for an output.

    Hashes the recipe text, container image identifier, and canonicalized
    decisions. Anything that changes the materialization semantics flows
    through this hash; the *runtime* used to invoke the container
    (docker/podman/podman-hpc) is intentionally excluded — the same image
    produces the same data regardless of which OCI tool launched it.
    """
    payload = {
        "recipe": recipe,
        "container_image": container_image,
        "decisions": decisions,
    }
    return _sha256_bytes(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def read_manifest(output_dir: Path) -> dict[str, Any] | None:
    """Read the manifest at ``<output_dir>/.lightcone-manifest.json``.

    Returns ``None`` if the manifest is missing or unparseable. ``OSError``
    (permission denied, I/O failure) is intentionally **not** caught —
    those are real problems that should not silently look like a missing
    manifest in ``lc verify`` / ``lc status`` output.
    """
    p = Path(output_dir) / MANIFEST_FILENAME
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())  # type: ignore[no-any-return]
    except json.JSONDecodeError:
        return None


def write_manifest(
    *,
    output_dir: Path,
    inputs: dict[str, Path],
    cfg: dict[str, Any],
) -> Path:
    """Atomically write the manifest for an already-materialized output.

    Called from each rule's ``run:`` block after the recipe shell exits.
    Hashes the output dir, resolves input versions (chaining to upstream
    manifests when present, falling back to external fingerprints), and
    commits the manifest via ``os.replace``.

    Args:
        output_dir: Directory containing the materialized output files.
        inputs: Mapping of declared input id → filesystem path. Each is
            either a directory containing a sibling manifest (upstream
            output) or an external file/dir.
        cfg: Per-rule configuration. Required keys: ``output_id``,
            ``universe_id``, ``recipe``, ``container_image``, ``decisions``,
            ``code_version``, ``git_sha``, ``lc_version``.
    """
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
