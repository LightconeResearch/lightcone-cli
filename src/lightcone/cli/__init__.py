"""lightcone-cli — the command-line surface for the Lightcone toolchain."""

from __future__ import annotations


def main() -> None:
    from lightcone.cli.commands import main as _main

    _main()
