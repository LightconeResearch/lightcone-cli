"""Default file templates for a scaffolded project.

The templates live as real files under ``templates/files/`` rather than as
string literals in the code that writes them: they *are* files, and
keeping them as files means they can be read, diffed, and highlighted as
whatever they will become. ``lc init`` is the only consumer — editing a
template here changes what new projects look like.

Placeholders use ``string.Template`` (``${name}``) rather than
``str.format``, because several templates legitimately contain braces
(TOML tables, MyST roles like ``{astra}``) that ``format`` would try to
interpret. Substitution is strict: a missing key raises rather than
silently emitting a placeholder.
"""

from __future__ import annotations

from importlib import resources
from string import Template

from lightcone.engine.constants import (
    DEFAULT_PYTHON_FLOOR,
    MIN_UV_VERSION,
)

#: Comment introducing lightcone's entries in a ``.gitignore`` it did not
#: author. Cosmetic only — what makes a repair idempotent is the entry set
#: (:func:`gitignore_entries`), not this line.
GITIGNORE_HEADER = "# lightcone-cli"

#: Every template shipped, by file name. The loader checks membership so a
#: typo fails loudly at the call site instead of as a packaging mystery.
TEMPLATE_NAMES = frozenset(
    {
        "pyproject.toml.tmpl",
        "gitignore.tmpl",
        "results-README.md.tmpl",
        "myst.yml.tmpl",
        "index.md.tmpl",
    }
)


def read(name: str) -> str:
    """Return the raw text of template *name*."""
    if name not in TEMPLATE_NAMES:
        raise KeyError(f"unknown template: {name!r}")
    return (resources.files(__name__) / "files" / name).read_text(encoding="utf-8")


def _render(name: str, /, **values: str) -> str:
    return Template(read(name)).substitute(**values)


# =============================================================================
# The uv project
# =============================================================================


def pyproject(*, name: str) -> str:
    """The scaffolded ``pyproject.toml`` for a project called *name*.

    Deliberately **virtual** — no ``[build-system]``. Containerized mode
    builds the image ``--no-install-project`` (project code never enters
    an image), so a packaged project's ``import my_analysis`` would fail
    inside its own container (spec §1).
    """
    return _render(
        "pyproject.toml.tmpl",
        name=name,
        python_floor=DEFAULT_PYTHON_FLOOR,
        min_uv=MIN_UV_VERSION,
        lc_requirement=lightcone_requirement(),
    )


def lightcone_requirement() -> str:
    """The ``lightcone-cli`` requirement pinned into the scaffold.

    The engine lives *inside the experiment's lock* — pinned to the
    version that ran ``lc init``, so driver and project stay in lockstep
    and the engine that produced a result stays recoverable. Dev builds
    fall back to unpinned: their versions aren't published, so pinning one
    would make the project's lock unsolvable.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        v = version("lightcone-cli")
    except PackageNotFoundError:
        v = ""
    return f"lightcone-cli=={v}" if v and "dev" not in v else "lightcone-cli"


# =============================================================================
# Managed files
# =============================================================================


def gitignore() -> str:
    """The whole ``.gitignore``, for a project that has none."""
    return read("gitignore.tmpl")


def gitignore_entries() -> tuple[str, ...]:
    """The patterns lightcone manages, in template order.

    The template minus its comments and blank lines. Convergence works
    against *this set* rather than against a marker, so a project ends up
    with the right ignores however its ``.gitignore`` got there — and
    entries added in a later lc release reach projects that already have
    one, instead of being skipped because a marker was present.
    """
    return tuple(_patterns(read("gitignore.tmpl")))


def _patterns(text: str) -> list[str]:
    """The meaningful (non-comment, non-blank) lines of a gitignore."""
    return [
        stripped
        for line in text.splitlines()
        if (stripped := line.strip()) and not stripped.startswith("#")
    ]


def missing_gitignore_entries(text: str) -> list[str]:
    """Which managed patterns *text* does not already carry, in order."""
    present = set(_patterns(text))
    return [e for e in gitignore_entries() if e not in present]


def results_readme() -> str:
    """``results/README.md`` — where outputs land.

    ``results/`` is git-ignored and starts empty, so without the README
    the directory is invisible in a clone and nothing explains the
    ``results/<universe>/<output_id>/`` layout.
    """
    return read("results-README.md.tmpl")


# =============================================================================
# The MyST report
# =============================================================================


def myst_yml() -> str:
    return read("myst.yml.tmpl")


def index_md(*, title: str) -> str:
    """The template report. References ``astra.yaml`` elements *by path*,
    so numbers and figures stay single-sourced in the analysis."""
    return _render("index.md.tmpl", title=title)
