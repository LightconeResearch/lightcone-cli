"""The build record: what was built, from what, with what result.

Lives at ``.lightcone/image/record.json`` (machine-local, gitignored —
digests differ per architecture and image store). The dpkg snapshot
*text* is archived beside it so the system-layer attestation outlives
image garbage-collection; manifests store only its sha256.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

RECORD_DIR = ".lightcone/image"
RECORD_NAME = "record.json"
_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class BuildRecord:
    tag: str
    image_id: str  # config digest — always present locally; the run pin
    digest: str | None  # manifest digest (registry spelling), if reported
    platform: str  # e.g. "linux/amd64"
    env_version: str
    lc_version: str
    base: str  # digest-pinned base ref
    built_at: str  # ISO 8601, informational
    dpkg_snapshot_sha256: str

    def to_json(self) -> str:
        return json.dumps(
            {"schema_version": _SCHEMA_VERSION, **asdict(self)},
            indent=2,
            sort_keys=True,
        )


def record_dir(project: Path) -> Path:
    return project / RECORD_DIR


def read_record(project: Path) -> BuildRecord | None:
    path = record_dir(project) / RECORD_NAME
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if data.pop("schema_version", None) != _SCHEMA_VERSION:
        return None
    try:
        return BuildRecord(**data)
    except TypeError:
        return None


def write_record(project: Path, record: BuildRecord, snapshot_text: str) -> None:
    directory = record_dir(project)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"dpkg-snapshot-{record.tag}.txt").write_text(snapshot_text)
    (directory / RECORD_NAME).write_text(record.to_json())


def snapshot_sha256(snapshot_text: str) -> str:
    return hashlib.sha256(snapshot_text.encode("utf-8")).hexdigest()
