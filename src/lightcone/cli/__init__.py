"""lightcone-cli — the command-line surface for the Lightcone toolchain."""

from __future__ import annotations

try:
    from importlib.metadata import version

    __version__ = version("lightcone-cli")
except Exception:
    __version__ = "0.0.0.dev"


def main() -> None:
    # The launcher (discover → mode-detect → scrub UV_* → converge →
    # delegate into the project-locked engine, spec §4) hooks in here when
    # its layer lands. Until then `lc` dispatches directly.
    from lightcone.cli.commands import main as _main

    _main()
