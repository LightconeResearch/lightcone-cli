"""Project discovery.

One rule, shared by the launcher and every CLI verb: the project root
is the nearest ancestor (including the start directory) containing an
``astra.yaml`` file. uv's native walk-up discovery is never trusted —
every uv invocation downstream carries an explicit ``--project <root>``.
"""
from __future__ import annotations

from pathlib import Path

SPEC_FILENAME = "astra.yaml"


def find_root(start: Path | None = None) -> Path | None:
    """Walk up from *start* (default: cwd) to the project root, or ``None``."""
    p = (start or Path.cwd()).resolve()
    for parent in [p, *p.parents]:
        if (parent / SPEC_FILENAME).is_file():
            return parent
    return None
