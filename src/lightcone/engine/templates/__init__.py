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
    """Read a template's raw text.

    Args:
        name: A file name from :data:`TEMPLATE_NAMES`.

    Returns:
        The template's text.

    Raises:
        KeyError: If *name* is not a shipped template.
    """
    if name not in TEMPLATE_NAMES:
        raise KeyError(f"unknown template: {name!r}")
    return (resources.files(__name__) / "files" / name).read_text(encoding="utf-8")


def _render(name: str, /, **values: str) -> str:
    return Template(read(name)).substitute(**values)


# =============================================================================
# The uv project
# =============================================================================


def pyproject(*, name: str) -> str:
    """Render the scaffolded ``pyproject.toml``.

    Args:
        name: The project name.

    Returns:
        A virtual uv project depending on lightcone-cli.
    """
    return _render(
        "pyproject.toml.tmpl",
        name=name,
        requires_python=requires_python(),
        lc_requirement=lightcone_requirement(),
    )


def python_version() -> str:
    """Render ``.python-version``.

    Deliberately not an engine constant: a new project pins the python the
    researcher actually has, rather than one lc would have to download to
    honour a number baked into a release.

    Returns:
        The exact patch of the interpreter ``lc`` is running on.
    """
    v = sys.version_info
    return f"{v.major}.{v.minor}.{v.micro}\n"


def requires_python() -> str:
    """Render the scaffolded ``requires-python``.

    Taken verbatim from lightcone-cli's own ``Requires-Python``: a
    scaffolded project depends on the engine, so uv enforces this bound
    during resolution regardless, and declaring the same specifier states
    it rather than inventing a second one. It cannot conflict with
    :func:`python_version`, since lc only runs on an interpreter that
    already satisfies it.

    Returns:
        The specifier, or the running interpreter's minor version for a
        metadata-less install.
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
    """Render the ``lightcone-cli`` requirement for the scaffold.

    The engine lives inside the experiment's lock, pinned to the version
    that ran ``lc init``, so the engine that produced a result stays
    recoverable.

    Returns:
        A pinned requirement, or an unpinned one for a dev build, whose
        version is not published and would make the lock unsolvable.
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
#
# `entries`, `missing` and `header` take the template name; only the two
# `*_repair` functions are named per file, because convergence hands those
# to `_Converger.file` as callbacks over the text alone.


def entries(name: str) -> tuple[str, ...]:
    """List the lines a template manages.

    Args:
        name: A line-managed template's file name.

    Returns:
        Its non-comment, non-blank lines, in template order.
    """
    return tuple(_lines(read(name)))


def _lines(text: str) -> list[str]:
    """The meaningful (non-comment, non-blank) lines of *text*."""
    return [
        stripped
        for line in text.splitlines()
        if (stripped := line.strip()) and not stripped.startswith("#")
    ]


def missing(name: str, text: str) -> list[str]:
    """Find the managed lines a file does not already carry.

    Args:
        name: A line-managed template's file name.
        text: The file's current contents.

    Returns:
        The absent lines, in template order.
    """
    present = set(_lines(text))
    return [e for e in entries(name) if e not in present]


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
    absent = missing(name, text)
    if not absent:
        return None

    block = "\n".join(absent) + "\n"
    # The header is cosmetic, so add it only when it isn't already there;
    # a later repair then appends bare lines under the first one.
    first = header(name)
    if first not in text:
        block = first + "\n" + block
    if not text.strip():
        return block
    return text.rstrip("\n") + "\n\n" + block


def header(name: str) -> str:
    """Read a template's own leading comment.

    Read back out of the template rather than duplicated as a constant, so
    rewording it there cannot leave a repair appending a second header.

    Args:
        name: A line-managed template's file name.

    Returns:
        Its first line.
    """
    return read(name).splitlines()[0]


# ---- .gitignore -------------------------------------------------------------


def gitignore() -> str:
    """Render the whole ``.gitignore``, for a project that has none."""
    return read("gitignore.tmpl")


def gitignore_repair(text: str) -> str | None:
    """Append the managed ignore patterns a file is missing.

    Args:
        text: The file's current contents.

    Returns:
        The repaired text, or ``None`` if nothing was missing.
    """
    return _repair("gitignore.tmpl", text)


# =============================================================================
# The dataset — what git-annex stores, and what makes it a DataLad dataset
# =============================================================================


def gitattributes() -> str:
    """Render ``.gitattributes``, the whole storage policy.

    A default of ``nothing`` and two exceptions: outputs and declared
    inputs go to git-annex, everything else stays in git. The default is
    the load-bearing line — ``git annex add`` annexes whatever it is
    handed, so without it a save turns analysis code into read-only
    symlinks. Manifests are exempted back out so they stay readable on a
    clone that has fetched no annex content.

    Returns:
        The four attribute lines, with the default first.
    """
    return read("gitattributes.tmpl")


def gitattributes_repair(text: str) -> str | None:
    """Append the managed attribute lines a file is missing.

    More is at stake than in ``.gitignore``: a ``.gitattributes`` the user
    wrote first would leave result bytes routed into git, not the annex.

    Args:
        text: The file's current contents.

    Returns:
        The repaired text, or ``None`` if nothing was missing.
    """
    return _repair("gitattributes.tmpl", text)


def datalad_config(*, dataset_id: str) -> str:
    """Render ``.datalad/config``, the file that makes a project a dataset.

    A dataset id is the one thing a git + git-annex repository lacks to
    *be* a DataLad dataset. lc never reads this back; datalad does.

    Args:
        dataset_id: A UUID, generated once and never regenerated.

    Returns:
        A git-config file carrying ``datalad.dataset.id``.
    """
    return _render("datalad-config.tmpl", dataset_id=dataset_id)


def data_readme() -> str:
    """Render ``data/README.md``, which says where declared inputs live.

    Returns:
        The README that makes an otherwise empty directory survive a clone.
    """
    return read("data-README.md.tmpl")


# =============================================================================
# results/ and the MyST report
# =============================================================================


def results_readme() -> str:
    """Render ``results/README.md``, which says where outputs land.

    Returns:
        The README that makes an otherwise empty directory survive a clone
        and explains the ``results/<universe>/<output_id>/`` layout.
    """
    return read("results-README.md.tmpl")


def myst_yml() -> str:
    """Render ``myst.yml``, the report's MyST configuration."""
    return read("myst.yml.tmpl")


def index_md(*, title: str) -> str:
    """Render ``index.md``, the template report.

    References ``astra.yaml`` elements by path, so numbers and figures
    stay single-sourced in the analysis.

    Args:
        title: The report title.

    Returns:
        A MyST document.
    """
    return _render("index.md.tmpl", title=title)
