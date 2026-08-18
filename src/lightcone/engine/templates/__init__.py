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

This module owns file *content*, including how content merges into a file
the user already owns (:func:`gitignore_repair`). It knows nothing about
convergence bookkeeping or the console.
"""

from __future__ import annotations

import sys
from importlib import resources
from string import Template

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
    """The scaffolded ``pyproject.toml`` for a project called *name*."""
    return _render(
        "pyproject.toml.tmpl",
        name=name,
        requires_python=requires_python(),
        lc_requirement=lightcone_requirement(),
    )


def python_version() -> str:
    """``.python-version`` — the exact interpreter patch, taken from the
    interpreter ``lc`` is running on.

    Deliberately not an engine constant: a new project pins the python the
    researcher actually has, rather than one lc would have to download to
    honor a number baked into a release.
    """
    v = sys.version_info
    return f"{v.major}.{v.minor}.{v.micro}\n"


def requires_python() -> str:
    """The scaffolded ``requires-python``, taken verbatim from
    lightcone-cli's own ``Requires-Python``.

    A scaffolded project depends on the engine, so uv enforces this bound
    during resolution regardless; declaring the same specifier states it
    rather than inventing a second, unrelated one. Verbatim, so a compound
    specifier carries over intact.

    It cannot conflict with :func:`python_version`: lc is only *able* to
    run on an interpreter satisfying this specifier, so the ambient pin
    always satisfies it. The fallback for a metadata-less install is the
    running interpreter's own minor version, which holds that property too.
    """
    from importlib.metadata import PackageNotFoundError, metadata

    try:
        # Message-style lookup: absent keys come back as None, not KeyError.
        declared = metadata("lightcone-cli")["Requires-Python"]
    except PackageNotFoundError:
        declared = None
    if declared:
        return str(declared)
    return f">={sys.version_info.major}.{sys.version_info.minor}"


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
# .gitignore — converged entry-wise, not by marker
# =============================================================================


def gitignore() -> str:
    """The whole ``.gitignore``, for a project that has none."""
    return read("gitignore.tmpl")


def gitignore_header() -> str:
    """The template's own leading comment.

    Read back out of the template rather than duplicated as a constant, so
    rewording it there can never leave :func:`gitignore_repair` appending a
    second header to a file that already carries the first.
    """
    return read("gitignore.tmpl").splitlines()[0]


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


def gitignore_repair(text: str) -> str | None:
    """*text* with every managed pattern present, or ``None`` if it already
    carries them all.

    Only ever appends, and only what is missing — so idempotency is
    structural: a pattern already in the file is never added again, whoever
    put it there.

    The append preserves template order, which is what keeps
    ``!results/README.md`` after the ``results/*`` it negates. (A file
    holding the negation *without* ``results/*`` would end up with them
    inverted; that state can only be hand-written, and re-ordering
    someone's ignores to fix it would be the more surprising behavior.)
    """
    missing = missing_gitignore_entries(text)
    if not missing:
        return None

    block = "\n".join(missing) + "\n"
    # The header is cosmetic, so add it only when it isn't already there;
    # a later repair then appends bare patterns under the first one.
    header = gitignore_header()
    if header not in text:
        block = header + "\n" + block
    if not text.strip():
        return block
    return text.rstrip("\n") + "\n\n" + block


# =============================================================================
# results/ and the MyST report
# =============================================================================


def results_readme() -> str:
    """``results/README.md`` — where outputs land.

    ``results/`` is git-ignored and starts empty, so without the README
    the directory is invisible in a clone and nothing explains the
    ``results/<universe>/<output_id>/`` layout.
    """
    return read("results-README.md.tmpl")


def myst_yml() -> str:
    """``myst.yml`` — the report's MyST configuration."""
    return read("myst.yml.tmpl")


def index_md(*, title: str) -> str:
    """The template report. References ``astra.yaml`` elements *by path*,
    so numbers and figures stay single-sourced in the analysis."""
    return _render("index.md.tmpl", title=title)
