"""Standalone manifest finalizer — the atomic commit point of every rule.

Runs at the end of every generated rule's ``shell:`` recipe. Reads
``.lightcone/snakefile-config.json``, hashes the output directory, fills
runtime fields, and atomically writes ``<output_dir>/.lightcone-manifest.json``
via ``os.replace``.

Two execution modes:

1. **Standalone CLI** (in-container): ``python3 _lc_finalize.py <rule_key>
   <universe> <output_dir>``. The Snakefile shells this out at the end of
   every rule. Must work in any container with ``python3`` — therefore
   stdlib only and zero local imports.

2. **Importable module** (host-side): ``manifest.py`` reuses ``sha256_dir``
   from here so ``lc verify`` hashes data the exact same way the finalizer
   did. Single source of truth for the hash algorithm.

Atomicity: the manifest is written to ``.lightcone-manifest.json.tmp`` and
then ``os.replace``-renamed onto its final name. POSIX guarantees that
rename is atomic on the same filesystem, so an external observer either
sees the complete manifest or no manifest at all. If anything fails before
the rename, the manifest is absent and Snakemake reruns the rule.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import time
from pathlib import Path

MANIFEST_FILENAME = ".lightcone-manifest.json"
SCHEMA_VERSION = 1

#: Filenames inside an output directory that the data_version hash MUST
#: ignore: the manifest itself (chicken-and-egg) and Snakemake's
#: ``directory()`` mtime marker (touched AFTER the rule body completes).
_HASH_EXCLUDE = frozenset({MANIFEST_FILENAME, ".snakemake_timestamp"})


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


def _fingerprint_external(path: Path) -> str:
    if not path.exists():
        return "missing"
    if path.is_dir():
        return sha256_dir(path)
    st = path.stat()
    return f"mtime-size:{st.st_mtime_ns}-{st.st_size}"


def _read_data_version(manifest_path: Path) -> str | None:
    try:
        with open(manifest_path) as f:
            return json.load(f).get("data_version")  # type: ignore[no-any-return]
    except (OSError, json.JSONDecodeError):
        return None


def _resolve_input_versions(inputs: dict[str, str]) -> dict[str, str]:
    """For each declared input, prefer chaining to its manifest's
    ``data_version``; fall back to an external fingerprint."""
    versions: dict[str, str] = {}
    for name, raw_path in inputs.items():
        path = Path(raw_path)
        manifest_path = path / MANIFEST_FILENAME
        if manifest_path.exists():
            dv = _read_data_version(manifest_path)
            if dv is not None:
                versions[name] = dv
                continue
        versions[name] = _fingerprint_external(path)
    return versions


def finalize(
    rule_key: str,
    universe: str,
    output_dir: Path,
    lightcone_dir: Path,
) -> Path:
    """Compute data_version, assemble the manifest, atomically commit it."""
    output_dir = Path(output_dir)
    lightcone_dir = Path(lightcone_dir)

    cfg_path = lightcone_dir / "snakefile-config.json"
    with open(cfg_path) as f:
        all_cfg = json.load(f)
    cfg = all_cfg[rule_key][universe]

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "output_id": cfg["output_id"],
        "universe_id": cfg["universe_id"],
        "code_version": cfg["code_version"],
        "data_version": sha256_dir(output_dir),
        "container_image": cfg.get("container_image"),
        "recipe": cfg["recipe"],
        "decisions": cfg.get("decisions", {}),
        "input_versions": _resolve_input_versions(cfg.get("inputs") or {}),
        "git_sha": cfg.get("git_sha"),
        "lc_version": cfg.get("lc_version"),
        "finished_at": time.time(),
        "host": socket.gethostname(),
        "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    }

    final_path = output_dir / MANIFEST_FILENAME
    tmp_path = final_path.with_suffix(final_path.suffix + ".tmp")
    with open(tmp_path, "w") as f:
        json.dump(manifest, f, sort_keys=True, indent=2)
    os.replace(tmp_path, final_path)
    return final_path


def main() -> None:
    if len(sys.argv) != 4:
        print(
            "usage: _lc_finalize.py <rule_key> <universe> <output_dir>",
            file=sys.stderr,
        )
        sys.exit(2)
    rule_key, universe, output_dir = sys.argv[1], sys.argv[2], sys.argv[3]
    # The script lives in <project>/.lightcone/_lc_finalize.py. Resolving
    # config relative to the script means cwd doesn't have to be the
    # project root — robust against container working-dir surprises.
    lightcone_dir = Path(__file__).resolve().parent
    finalize(rule_key, universe, Path(output_dir), lightcone_dir)


if __name__ == "__main__":
    main()
