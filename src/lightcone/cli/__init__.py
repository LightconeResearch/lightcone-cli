"""lightcone-cli — the command-line surface for the Lightcone toolchain."""

from __future__ import annotations


def main() -> None:
    """Run the CLI.

    The console-script entry point. Imports the command module lazily, so
    the cost of click and the engine is paid only once a command runs.
    """
    from lightcone.cli.commands import main as _main

    _main()
