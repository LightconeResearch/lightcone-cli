"""lightcone-cli — the command-line surface for the Lightcone toolchain."""

from __future__ import annotations

import sys


def main() -> None:
    """Run the CLI.

    The console-script entry point. Delegation comes first and click
    second: the engine that runs a project is the one inside its lock, and
    deciding that has to happen before any of this environment's modules
    are imported and used. Both imports are lazy, so ``lc --help`` and
    shell completion pay for neither.
    """
    from lightcone.cli.launcher import maybe_delegate

    maybe_delegate(sys.argv[1:])

    from lightcone.cli.commands import main as _main

    _main()
