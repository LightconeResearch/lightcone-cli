"""Auto-build wheels and collect version metadata for eval runs."""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from pathlib import Path

from lightcone.eval.models import VersionInfo

logger = logging.getLogger(__name__)


def _get_repo_root() -> Path:
    """Find the lightcone-cli repo root via git."""
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
        info.lightcone_commit = result.stdout.strip()
    except subprocess.CalledProcessError:
        pass

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, check=True, cwd=repo_root,
        )
        info.lightcone_branch = result.stdout.strip()
    except subprocess.CalledProcessError:
        pass

    try:
        dirty = subprocess.run(
            ["git", "diff", "--quiet", "HEAD"],
            capture_output=True, cwd=repo_root,
        )
        info.lightcone_dirty = dirty.returncode != 0
    except subprocess.CalledProcessError:
        info.lightcone_dirty = True

    return info


def _build_wheel(repo_root: Path, outdir: Path) -> Path:
    """Build a wheel from the repo and return the wheel path."""
    result = subprocess.run(
        ["python", "-m", "build", "--wheel", "--outdir", str(outdir)],
        capture_output=True, text=True, cwd=repo_root,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Wheel build failed:\n{result.stderr}")

    wheels = list(outdir.glob("lightcone_cli-*.whl"))
    if not wheels:
        raise RuntimeError(f"No lightcone_cli wheel found in {outdir} after build")
    return wheels[0]


def _extract_version(wheel_path: Path) -> str:
    """Extract version string from a wheel filename."""
    # Wheel filenames: {name}-{version}-{tags}.whl
    match = re.match(r"[^-]+-([^-]+)-", wheel_path.name)
    return match.group(1) if match else wheel_path.name


def build_eval_wheels(evals_dir: Path) -> tuple[VersionInfo, list[Path]]:
    """Build the lightcone-cli wheel for sandbox injection.

    Only lightcone-cli is built locally — that's the package under test
    and must come from the branch's working tree, not from PyPI. ASTRA
    (``astra-tools`` + ``astra-spec``) is pre-installed in the sandbox
    image from PyPI alongside the other third-party deps; we record the
    host's resolved version here for the run report.

    Returns (version_info, [wheel_paths]).
    """
    import importlib.metadata

    repo_root = _get_repo_root()
    version_info = _get_git_info(repo_root)

    tmpdir = Path(tempfile.mkdtemp(prefix="lightcone-eval-wheels-"))
    logger.info("Building lightcone-cli wheel from %s ...", repo_root)
    lightcone_wheel = _build_wheel(repo_root, tmpdir)
    version_info.lightcone_version = _extract_version(lightcone_wheel)
    logger.info("Built %s (commit %s%s)",
                lightcone_wheel.name,
                version_info.lightcone_commit[:8],
                " dirty" if version_info.lightcone_dirty else "")

    try:
        version_info.astra_version = importlib.metadata.version("astra-tools")
    except importlib.metadata.PackageNotFoundError:
        pass

    return version_info, [lightcone_wheel]
