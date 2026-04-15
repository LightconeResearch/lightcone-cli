"""Auto-build wheels and collect version metadata for eval runs."""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from pathlib import Path

from prism.eval.models import VersionInfo

logger = logging.getLogger(__name__)


def _get_repo_root() -> Path:
    """Find the Prism repo root via git."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    )
    return Path(result.stdout.strip())


def _get_git_info(repo_root: Path) -> VersionInfo:
    """Collect git metadata from the repo."""
    info = VersionInfo()

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, cwd=repo_root,
        )
        info.prism_commit = result.stdout.strip()
    except subprocess.CalledProcessError:
        pass

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, check=True, cwd=repo_root,
        )
        info.prism_branch = result.stdout.strip()
    except subprocess.CalledProcessError:
        pass

    try:
        result = subprocess.run(
            ["git", "diff", "--quiet", "HEAD"],
            capture_output=True, cwd=repo_root,
        )
        info.prism_dirty = result.returncode != 0
    except subprocess.CalledProcessError:
        info.prism_dirty = True

    return info


def _build_wheel(repo_root: Path, outdir: Path) -> Path:
    """Build a wheel from the repo and return the wheel path."""
    result = subprocess.run(
        ["python", "-m", "build", "--wheel", "--outdir", str(outdir)],
        capture_output=True, text=True, cwd=repo_root,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Wheel build failed:\n{result.stderr}")

    wheels = list(outdir.glob("lightcone_prism-*.whl"))
    if not wheels:
        raise RuntimeError(f"No lightcone_prism wheel found in {outdir} after build")
    return wheels[0]


def _extract_version(wheel_path: Path) -> str:
    """Extract version string from a wheel filename."""
    # Wheel filenames: {name}-{version}-{tags}.whl
    match = re.match(r"[^-]+-([^-]+)-", wheel_path.name)
    return match.group(1) if match else wheel_path.name


def _build_astra_wheel(outdir: Path) -> Path:
    """Build an ASTRA wheel from the version installed in the current environment.

    Uses importlib.metadata to find the git URL and commit of the installed astra
    package, then builds a wheel pinned to that exact commit via pip wheel.
    """
    import importlib.metadata
    import json

    dist = importlib.metadata.distribution("astra")
    url_text = dist.read_text("direct_url.json")
    if not url_text:
        raise RuntimeError(
            "Cannot determine ASTRA source — installed package has no direct_url.json. "
            "Ensure astra is installed from its git repo (not a plain wheel)."
        )
    url_info = json.loads(url_text)
    git_url = url_info["url"]
    commit = url_info.get("vcs_info", {}).get("commit_id", "")
    spec = f"astra @ git+{git_url}@{commit}" if commit else f"astra @ git+{git_url}"

    logger.info("Building ASTRA wheel from %s (commit %s) ...", git_url, commit[:8] or "HEAD")
    result = subprocess.run(
        ["pip", "wheel", "--no-deps", "--wheel-dir", str(outdir), spec],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ASTRA wheel build failed:\n{result.stderr}")

    wheels = list(outdir.glob("astra-*.whl"))
    if not wheels:
        raise RuntimeError(f"No astra wheel found in {outdir} after build")
    return wheels[0]


def build_eval_wheels(evals_dir: Path) -> tuple[VersionInfo, list[Path]]:
    """Build the Prism and ASTRA wheels for sandbox injection.

    Returns (version_info, [wheel_paths]).
    Both wheels are built fresh from the current environment:
    - Prism: built from the local git working tree
    - ASTRA: built from the git URL/commit recorded in the installed package metadata
    """
    repo_root = _get_repo_root()
    version_info = _get_git_info(repo_root)

    # Build Prism wheel into a temp dir
    tmpdir = Path(tempfile.mkdtemp(prefix="prism-eval-wheels-"))
    logger.info("Building Prism wheel from %s ...", repo_root)
    prism_wheel = _build_wheel(repo_root, tmpdir)
    version_info.prism_version = _extract_version(prism_wheel)
    logger.info("Built %s (commit %s%s)",
                prism_wheel.name,
                version_info.prism_commit[:8],
                " dirty" if version_info.prism_dirty else "")

    wheels: list[Path] = [prism_wheel]

    # Build ASTRA wheel from the installed package's git source
    try:
        astra_wheel = _build_astra_wheel(tmpdir)
        version_info.astra_version = _extract_version(astra_wheel)
        wheels.append(astra_wheel)
        logger.info("Built %s", astra_wheel.name)
    except Exception as exc:
        logger.warning("Failed to build ASTRA wheel: %s — sandbox may not have astra CLI", exc)

    return version_info, wheels
