"""Repositories holding the pinned commits a run reads.

Local history uses the project's own repository and annex. Another repository
is cloned without checking out files, so no filter downloads or copies data.
The clone is published only when complete; executions then take independent
plain worktrees and fetch exact keys into this shared annex. Nothing ever
repairs or replaces a tree a running recipe may be reading.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from uuid import uuid4

from lightcone.engine import dataset
from lightcone.engine.project import ProjectError


def repository(root: Path, commit: str, url: str | None) -> Path:
    """Locate a repository for an exact commit, fetching remote history as needed.

    Args:
        root: The researcher's project.
        commit: The commit to read.
        url: Its repository, or None for local history.

    Returns:
        A repository from which the driver can stage files and fetch annex keys.

    Raises:
        ProjectError: If the declared commit cannot be obtained.
    """
    if url is None:
        if not dataset._git_ok(["cat-file", "-e", f"{commit}^{{commit}}"], cwd=root):
            raise ProjectError(
                f"{commit} is not in the project's repository, and astra-tools names "
                "no other that holds it: a submodule's commit needs its repository"
            )
        return root
    base = root / ".lightcone/repositories"
    base.mkdir(parents=True, exist_ok=True)
    cache = base / hashlib.sha256(url.encode()).hexdigest()
    reach = dataset.reaching(root)
    if not cache.exists():
        making = base / uuid4().hex
        try:
            dataset._git([*reach, "clone", "-q", "--no-checkout", "--", url, str(making)], cwd=root)
            for name in ("name", "email"):
                value = dataset._git(["config", "--get", f"user.{name}"], cwd=root).strip()
                dataset._git(["config", f"user.{name}", value], cwd=making)
            dataset.init_annex(making)
            try:
                making.rename(cache)
            except OSError:
                if not cache.is_dir():
                    raise
        finally:
            if making.exists():
                for folder, _, _ in os.walk(making):
                    os.chmod(folder, 0o700)
                shutil.rmtree(making)
    if dataset.toplevel(cache) != cache.resolve():
        raise ProjectError(f"the cached repository at {cache} is not a git repository")
    if not dataset._git_ok(["cat-file", "-e", f"{commit}^{{commit}}"], cwd=cache):
        dataset._git([*reach, "fetch", "-q", "origin", commit], cwd=cache)
    return cache
