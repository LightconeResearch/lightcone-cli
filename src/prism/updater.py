"""Self-update and version-check utilities for Prism."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

# Repos that the installer manages (relative to the lightcone root)
_PYTHON_REPOS = ["ASTRA", "Prism"]
_ALL_REPOS = ["ASTRA", "Prism", "Prism-UI"]

_CHECK_FILE = Path.home() / ".prism" / ".update_check"
_CHECK_INTERVAL = 86400  # 24 hours


def _get_lightcone_root() -> Path | None:
    """Discover the lightcone install root from Prism's editable-install location."""
    import prism

    # prism.__file__ is .../Prism/src/prism/__init__.py
    prism_repo = Path(prism.__file__).resolve().parent.parent.parent
    if not (prism_repo / "pyproject.toml").exists():
        return None
    # The lightcone root is one level above the repo dir
    root = prism_repo.parent
    # Sanity check: at least one sibling repo should exist
    if (root / "ASTRA").is_dir() or (root / "Prism-UI").is_dir():
        return root
    return None


def _git(repo_dir: Path, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def pull_repos(root: Path) -> list[tuple[str, bool, str]]:
    """Pull all repos. Returns list of (name, success, message)."""
    results: list[tuple[str, bool, str]] = []
    for repo_name in _ALL_REPOS:
        repo_dir = root / repo_name
        if not (repo_dir / ".git").is_dir():
            results.append((repo_name, False, "not found, skipping"))
            continue
        # Fetch first
        fetch = _git(repo_dir, "fetch", "--quiet")
        if fetch.returncode != 0:
            results.append((repo_name, False, f"fetch failed: {fetch.stderr.strip()}"))
            continue
        # Check if behind
        status = _git(repo_dir, "rev-list", "--count", "HEAD..@{u}")
        if status.returncode != 0:
            # No upstream tracking branch
            results.append((repo_name, True, "no upstream, skipped"))
            continue
        behind = int(status.stdout.strip() or "0")
        if behind == 0:
            results.append((repo_name, True, "already up to date"))
            continue
        # Pull
        pull = _git(repo_dir, "pull", "--ff-only", "--quiet")
        if pull.returncode != 0:
            results.append((
                repo_name, False,
                f"pull failed (local changes?): {pull.stderr.strip()}"
            ))
        else:
            results.append((repo_name, True, f"updated ({behind} new commit{'s' if behind != 1 else ''})"))
    return results


def reinstall_packages(root: Path) -> list[tuple[str, bool, str]]:
    """Re-install Python packages in editable mode."""
    results: list[tuple[str, bool, str]] = []
    pip = sys.executable
    for repo_name in _PYTHON_REPOS:
        repo_dir = root / repo_name
        if not (repo_dir / "pyproject.toml").exists():
            continue
        proc = subprocess.run(
            [pip, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "-e", str(repo_dir)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode == 0:
            results.append((repo_name, True, "installed"))
        else:
            results.append((repo_name, False, proc.stderr.strip()[:200]))
    return results


def check_for_updates(quiet_if_current: bool = True) -> str | None:
    """Check if any repos are behind their remote. Returns a message or None.

    Uses a cache file to avoid hitting the network on every invocation.
    Only checks once per _CHECK_INTERVAL seconds.
    """
    # Check cache
    if _CHECK_FILE.exists():
        try:
            data = json.loads(_CHECK_FILE.read_text())
            last_check = data.get("timestamp", 0)
            if time.time() - last_check < _CHECK_INTERVAL:
                # Return cached message if any
                return data.get("message")
        except (json.JSONDecodeError, OSError):
            pass

    root = _get_lightcone_root()
    if root is None:
        return None

    behind_repos: list[str] = []
    for repo_name in _ALL_REPOS:
        repo_dir = root / repo_name
        if not (repo_dir / ".git").is_dir():
            continue
        try:
            fetch = _git(repo_dir, "fetch", "--quiet", timeout=10)
            if fetch.returncode != 0:
                continue
            status = _git(repo_dir, "rev-list", "--count", "HEAD..@{u}", timeout=5)
            if status.returncode != 0:
                continue
            behind = int(status.stdout.strip() or "0")
            if behind > 0:
                behind_repos.append(repo_name)
        except (subprocess.TimeoutExpired, ValueError):
            continue

    message: str | None = None
    if behind_repos:
        names = ", ".join(behind_repos)
        message = f"Updates available for: {names}. Run: [cyan]prism update[/cyan]"

    # Cache result
    _CHECK_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CHECK_FILE.write_text(json.dumps({"timestamp": time.time(), "message": message}))

    return message
