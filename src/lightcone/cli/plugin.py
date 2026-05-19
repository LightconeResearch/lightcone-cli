"""Plugin marketplace discovery — finds the agent plugin marketplace shipped with lightcone-cli.

The marketplace manifest lives at ``<root>/.claude-plugin/marketplace.json`` and
points at the actual plugin under ``<root>/claude/lightcone/``. ``lc init``
shells out to ``claude plugin marketplace add <root>`` followed by ``claude
plugin install lightcone@lightcone-cli``; this module returns the right
``<root>`` to pass to the CLI.

Kept deliberately leaf (no imports from :mod:`lightcone.cli.commands` or
:mod:`lightcone.eval`) so it can be used by both the CLI and the eval harness
without introducing an import cycle.
"""

from __future__ import annotations

from pathlib import Path

# Names declared in .claude-plugin/marketplace.json (the marketplace name) and
# claude/lightcone/.claude-plugin/plugin.json (the plugin name). The install
# reference passed to Claude/Codex is ``PLUGIN@MARKETPLACE``.
MARKETPLACE_NAME = "lightcone-cli"
PLUGIN_NAME = "lightcone"
CODEX_MARKETPLACE_NAME = MARKETPLACE_NAME
CODEX_PLUGIN_NAME = PLUGIN_NAME


def get_marketplace_root() -> Path | None:
    """Find the directory containing ``.claude-plugin/marketplace.json``.

    The returned path is what ``claude plugin marketplace add`` registers; the
    Claude CLI then reads ``marketplace.json`` from that root and discovers
    the plugin at ``./claude/lightcone``.

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
