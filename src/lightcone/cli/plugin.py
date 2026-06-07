"""Agent-bundle discovery for the lightcone-cli Claude/Codex/Pi integrations.

The Claude marketplace manifest lives at ``<root>/.claude-plugin/marketplace.json``
and points at the shared bundle under ``<root>/claude/lightcone/``. Claude and
Codex install from that marketplace root; Pi installs the bundle directory
itself as a local package. This module returns the paths ``lc init`` needs
without importing the CLI command module, so both the CLI and eval harness can
reuse it without an import cycle.
"""

from __future__ import annotations

from pathlib import Path

# Names declared in .claude-plugin/marketplace.json (the marketplace name) and
# claude/lightcone/.claude-plugin + .codex-plugin/plugin.json (the plugin name).
# The install reference passed to Claude/Codex is ``PLUGIN@MARKETPLACE``.
MARKETPLACE_NAME = "lightcone-cli"
PLUGIN_NAME = "lightcone"
CODEX_MARKETPLACE_NAME = MARKETPLACE_NAME
CODEX_PLUGIN_NAME = PLUGIN_NAME
BUNDLE_RELATIVE_PATH = Path("claude") / "lightcone"


def get_marketplace_root() -> Path | None:
    """Find the directory containing ``.claude-plugin/marketplace.json``.

    The returned path is what ``claude plugin marketplace add`` and
    ``codex plugin marketplace add`` register. The marketplace then points at
    the shared agent bundle in ``./claude/lightcone``.

    Looks in two locations, in order:

    1. **Bundled** (installed wheel): ``lightcone/cli/`` — populated by the
       ``force-include`` rules in ``pyproject.toml`` so the marketplace root
       is reachable without a checkout.
    2. **Development** (running from repo): the repo root, three levels above
       ``lightcone/cli/`` in the src-layout.
    """
    import lightcone.cli

    package_dir = Path(lightcone.cli.__file__).parent
    bundled_root = package_dir
    if (bundled_root / ".claude-plugin" / "marketplace.json").is_file():
        return bundled_root

    # Try development location (running from repo)
    # package_dir == <repo>/src/lightcone/cli → parents[2] == <repo>
    repo_root = package_dir.parents[2]
    if (repo_root / ".claude-plugin" / "marketplace.json").is_file():
        return repo_root

    return None


def get_agent_bundle_root() -> Path | None:
    """Return the shared Claude/Codex/Pi bundle directory.

    The bundle lives under ``claude/lightcone`` in both source checkouts and
    wheel installs. Claude/Codex treat it as a plugin source; Pi installs the
    same directory as a local package.
    """
    marketplace_root = get_marketplace_root()
    if marketplace_root is None:
        return None

    bundle_root = marketplace_root / BUNDLE_RELATIVE_PATH
    return bundle_root if bundle_root.is_dir() else None
