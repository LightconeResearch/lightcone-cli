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

    wheels = list(outdir.glob("prism-*.whl"))
    if not wheels:
        raise RuntimeError(f"No prism wheel found in {outdir} after build")
    return wheels[0]


def _extract_version(wheel_path: Path) -> str:
    """Extract version string from a wheel filename."""
    # Wheel filenames: {name}-{version}-{tags}.whl
    match = re.match(r"[^-]+-([^-]+)-", wheel_path.name)
    return match.group(1) if match else wheel_path.name


def build_eval_wheels(evals_dir: Path) -> tuple[VersionInfo, list[Path]]:
    """Build the Prism wheel and collect all wheels for sandbox injection.

    Returns (version_info, [wheel_paths]).
    The Prism wheel is always built fresh from the current working tree.
    The ASTRA wheel is expected in evals/deps/ (external repo, not auto-built).
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

    # Find ASTRA wheel in evals/deps/
    deps_dir = evals_dir / "deps"
    if deps_dir.exists():
        for whl in sorted(deps_dir.glob("astra-*.whl")):
            version_info.astra_version = _extract_version(whl)
            wheels.append(whl)
            logger.info("Using ASTRA wheel: %s", whl.name)

    if not version_info.astra_version:
        logger.warning("No ASTRA wheel found in %s — sandbox may not have astra CLI", deps_dir)

    return version_info, wheels
