"""lightcone-cli — the command-line surface for the Lightcone toolchain."""

from __future__ import annotations

try:
    from importlib.metadata import version

    __version__ = version("lightcone-cli")
except Exception:
    __version__ = "0.0.0.dev"


def main() -> None:
    import sys

    from lightcone.launcher import maybe_delegate

    # Execs the project-locked engine and never returns when it
    # delegates; otherwise falls through to normal Click dispatch.
    maybe_delegate(sys.argv[1:])

    from lightcone.cli.commands import main as _main

    _main()
