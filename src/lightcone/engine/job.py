"""The typed per-(rule, universe) job contract.

One value object defines what travels from the Snakefile generator
through ``snakefile-config.json`` into the worker's ``run_rule`` —
replacing ad-hoc dict keys with a schema both sides share. The JSON
form (``to_cfg``/``from_cfg``) is what ``params.cfg`` carries and what
``write_manifest`` consumes.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any


@dataclass(frozen=True)
class RuleJob:
    output_id: str
    universe_id: str
    recipe: str  # the raw authored template (recorded in the manifest)
    shell_command: str  # rendered, code_version-prefixed
    code_version: str
    env_version: str  # the mid-run gates' baseline
    output_type: str | None = None
    decisions: dict[str, Any] = field(default_factory=dict)
    writable_project: bool = False
    sdist_built: list[str] = field(default_factory=list)
    git_sha: str | None = None
    git_dirty: bool | None = None
    git_remote: str | None = None
    lc_version: str | None = None
    worker_runtime: str = "host"  # "host" | "container"
    image_tag: str | None = None
    image_digest: str | None = None  # driver-resolved; asserted worker-side
    dpkg_snapshot_sha256: str | None = None

    def to_cfg(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_cfg(cls, cfg: dict[str, Any]) -> RuleJob:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in cfg.items() if k in known})
