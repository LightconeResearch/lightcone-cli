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
the user already owns (:func:`gitignore_repair`,
:func:`gitattributes_repair`). It knows nothing about convergence
bookkeeping or the console.
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
        "gitattributes.tmpl",
        "datalad-config.tmpl",
        "data-README.md.tmpl",
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
# Line-managed files — converged entry-wise, not by marker
# =============================================================================
#
# ``.gitignore`` and ``.gitattributes`` are both "a list of lines the user
# owns, some of which are ours". Convergence works against *the set of
# managed lines* rather than against a marker comment, so a project ends up
# with the right entries however its file got there — and lines added in a
# later lc release reach projects that already have one, instead of being
# skipped because a marker was present.


def _entries(name: str) -> tuple[str, ...]:
    """The meaningful lines of template *name*, in template order."""
    return tuple(_lines(read(name)))


def _lines(text: str) -> list[str]:
    """The meaningful (non-comment, non-blank) lines of *text*."""
    return [
        stripped
        for line in text.splitlines()
        if (stripped := line.strip()) and not stripped.startswith("#")
    ]


def _missing(name: str, text: str) -> list[str]:
    """Which of template *name*'s lines *text* does not carry, in order."""
    present = set(_lines(text))
    return [e for e in _entries(name) if e not in present]


def _repair(name: str, text: str) -> str | None:
    """*text* with every line of template *name* present, or ``None``.

    Only ever appends, and only what is missing — so idempotency is
    structural: a line already in the file is never added again, whoever
    put it there. The append preserves template order, which is what an
    ignore file's negation patterns depend on.

    Append-only also means a line a template *dropped* is never removed
    from a file an earlier lc wrote. That is the right default — the file
    is the user's — but it is why convergence checks separately that
    ``results/`` is not ignored: a ``results/*`` inherited from an older
    scaffold would make every materialized output silently uncommittable.
    """
    missing = _missing(name, text)
    if not missing:
        return None

    block = "\n".join(missing) + "\n"
    # The header is cosmetic, so add it only when it isn't already there;
    # a later repair then appends bare lines under the first one.
    header = _header(name)
    if header not in text:
        block = header + "\n" + block
    if not text.strip():
        return block
    return text.rstrip("\n") + "\n\n" + block


def _header(name: str) -> str:
    """Template *name*'s own leading comment.

    Read back out of the template rather than duplicated as a constant, so
    rewording it there can never leave :func:`_repair` appending a second
    header to a file that already carries the first.
    """
    return read(name).splitlines()[0]


# ---- .gitignore -------------------------------------------------------------


def gitignore() -> str:
    """The whole ``.gitignore``, for a project that has none."""
    return read("gitignore.tmpl")


def gitignore_header() -> str:
    """The ``.gitignore`` template's own leading comment."""
    return _header("gitignore.tmpl")


def gitignore_entries() -> tuple[str, ...]:
    """The patterns lightcone manages, in template order."""
    return _entries("gitignore.tmpl")


def missing_gitignore_entries(text: str) -> list[str]:
    """Which managed patterns *text* does not already carry, in order."""
    return _missing("gitignore.tmpl", text)


def gitignore_repair(text: str) -> str | None:
    """*text* with every managed ignore pattern present, or ``None``."""
    return _repair("gitignore.tmpl", text)


# =============================================================================
# The dataset — what git-annex stores, and what makes it a DataLad dataset
# =============================================================================


def gitattributes() -> str:
    """``.gitattributes`` — the whole storage policy, in four lines.

    A default of ``nothing`` and two exceptions: outputs and declared
    inputs are content and go to git-annex, everything else stays in git.
    The default is the line that is easy to leave out and expensive to —
    ``git annex add`` annexes whatever it is handed, so without it the
    documented save turns analysis code into read-only symlinks into the
    object store. Manifests are exempted again because they must be
    readable on a clone that has fetched no annex content.
    """
    return read("gitattributes.tmpl")


def gitattributes_entries() -> tuple[str, ...]:
    """The attribute lines lightcone manages, in template order."""
    return _entries("gitattributes.tmpl")


def missing_gitattributes_entries(text: str) -> list[str]:
    """Which managed attribute lines *text* does not carry, in order."""
    return _missing("gitattributes.tmpl", text)


def gitattributes_repair(text: str) -> str | None:
    """*text* with every managed attribute line present, or ``None``.

    Entry-wise for the same reason ``.gitignore`` is, and with more at
    stake: a ``.gitattributes`` that the user wrote first, or that an
    earlier lc wrote with fewer lines, would leave result bytes routed
    into git instead of the annex.
    """
    return _repair("gitattributes.tmpl", text)


def datalad_config(*, dataset_id: str) -> str:
    """``.datalad/config`` — a git-config file carrying the dataset id.

    A dataset id is the one thing a git + git-annex repository lacks to
    *be* a DataLad dataset, so writing it makes a scaffolded project one
    from birth, with no adoption step. lc never reads this file back;
    ``datalad`` does, if the researcher installs it.
    """
    return _render("datalad-config.tmpl", dataset_id=dataset_id)


def data_readme() -> str:
    """``data/README.md`` — where declared inputs live.

    Like ``results/``, the directory starts empty and git carries no empty
    directories, so the README is what makes it exist in a clone.
    """
    return read("data-README.md.tmpl")


# =============================================================================
# results/ and the MyST report
# =============================================================================


def results_readme() -> str:
    """``results/README.md`` — where outputs land.

    ``results/`` starts empty, and git does not track empty directories,
    so without the README the directory is absent from a clone and nothing
    explains the ``results/<universe>/<output_id>/`` layout.
    """
    return read("results-README.md.tmpl")


def myst_yml() -> str:
    """``myst.yml`` — the report's MyST configuration."""
    return read("myst.yml.tmpl")


def index_md(*, title: str) -> str:
    """The template report. References ``astra.yaml`` elements *by path*,
    so numbers and figures stay single-sourced in the analysis."""
    return _render("index.md.tmpl", title=title)
