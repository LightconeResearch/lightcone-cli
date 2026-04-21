"""Plugin bundle discovery — finds the Claude Code skills/hooks shipped with lightcone-cli.

Kept deliberately leaf (no imports from :mod:`lightcone.cli.commands` or :mod:`lightcone.eval`)
so it can be used by both the CLI and the eval harness without introducing an import cycle.
"""

from __future__ import annotations

from pathlib import Path


def get_plugin_source_dir() -> Path | None:
    """Find the lightcone Claude plugin source directory.

    Looks for the plugin files in:

    1. Bundled location (installed package): ``lightcone/cli/claude/lightcone/``
    2. Development location (repo): ``claude/lightcone/`` relative to repo root
    """
    import lightcone.cli

    package_dir = Path(lightcone.cli.__file__).parent
    bundled_plugin = package_dir / "claude" / "lightcone"
    if bundled_plugin.exists():
        return bundled_plugin

    # Try development location (running from repo)
    # package_dir == <repo>/src/lightcone/cli → parents[2] == <repo>
    repo_root = package_dir.parents[2]
    dev_plugin = repo_root / "claude" / "lightcone"
    if dev_plugin.exists():
        return dev_plugin

    return None
