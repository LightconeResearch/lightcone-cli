"""The capped, versioned tool → apt-package hint table.

Deliberately small: a dozen high-frequency scientific tools, with the
generic ``apt-cache search`` line as the fallback for everything else —
never an open-ended mapping (spec §7).
"""
from __future__ import annotations

HINT_TABLE_VERSION = 1

HINTS: dict[str, str] = {
    "latex": "texlive-latex-base",
    "pdflatex": "texlive-latex-base",
    "xelatex": "texlive-xetex",
    "Rscript": "r-base-core",
    "R": "r-base-core",
    "julia": "julia",
    "convert": "imagemagick",
    "pdftoppm": "poppler-utils",
    "gs": "ghostscript",
    "dot": "graphviz",
    "ffmpeg": "ffmpeg",
    "pandoc": "pandoc",
    "gfortran": "gfortran",
    "mpirun": "openmpi-bin",
}


def apt_hint(tool: str) -> str | None:
    """Best-guess apt package for *tool*, or None (caller falls back to
    the generic search line)."""
    return HINTS.get(tool)
