"""Complete uv environments, shared by runs of the existing environment identity.

Build at a unique final path: virtualenv scripts embed that path. Only after
uv finishes do we atomically publish a symlink under the identity. Readers
resolve it once, check with uv, and never sync a published directory. Two
builders may finish independently; neither sees the other's partial build.

Editable projects get a private environment in their pinned checkout, so
imports never reach another run's code. Old environments are retained:
a time-based sweep cannot prove a remote worker has finished using one.
"""

from __future__ import annotations

import os
import shutil
import tomllib
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from lightcone.engine import container


def prepare(
    project: Path, runtime: container.Runtime, identity: str,
) -> tuple[container.Runtime, list[str]]:
    """Prepare the run's environment without mutating one another run uses.

    Args:
        project: The researcher's project, which owns the cache.
        runtime: The runtime rooted in the run's checkout.
        identity: The existing environment IRI recorded with results.

    Returns:
        The runtime with its complete environment, and uv's warnings.
    """
    declared = tomllib.loads((runtime.root / "pyproject.toml").read_text())
    package = declared.get("tool", {}).get("uv", {}).get("package", "build-system" in declared)
    if package:
        return runtime, container.converge(runtime)
    envs = project / ".lightcone/envs"
    envs.mkdir(parents=True, exist_ok=True)
    name = identity.rsplit(":", 1)[-1]
    published = envs / name
    warnings: list[str] = []
    if published.is_symlink() and (found := published.resolve()).is_dir():
        prepared = replace(runtime, env_dir=found)
        if container.current(prepared):
            return prepared, warnings
        warnings.append(f"the environment {name[:12]} changed; built a new environment")
    made = envs / f"{name}.{uuid4().hex}"
    prepared = replace(runtime, env_dir=made)
    try:
        warnings.extend(container.converge(prepared))
    except BaseException:
        shutil.rmtree(made, ignore_errors=True)
        raise
    beside = envs / f".{uuid4().hex}"
    beside.symlink_to(made.name)
    os.replace(beside, published)
    return prepared, warnings
