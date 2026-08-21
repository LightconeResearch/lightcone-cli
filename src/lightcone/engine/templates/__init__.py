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

A template gets a function here only when there is something to decide:
a value the caller supplies (:func:`pyproject`, :func:`datalad_config`,
:func:`index_md`) or a policy for merging into a file the user already
owns (:func:`gitignore_repair`, :func:`gitattributes_repair`). Everything
else is its own content, and callers read it by name through
:func:`read` — a wrapper that only renames the file would be a second
place for the name to be wrong.

This module knows nothing about convergence bookkeeping or the console.
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
        A virtual uv project with no dependencies. The engine is not among
        them: lc runs from the host's tool install, and the project's lock
        carries only what the analysis itself imports.
    """
    return _render(
        "pyproject.toml.tmpl",
        name=name,
        requires_python=requires_python(),
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

    The running interpreter's minor version, consistent with
    :func:`python_version`, which pins the exact patch of the same
    interpreter — the bound and the pin come from one place, so they
    cannot disagree.

    Returns:
        The specifier.
    """
    return f">={sys.version_info.major}.{sys.version_info.minor}"


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
# `entries`, `missing` and `header` take the template name. The two
# `*_repair` functions are the exception to reading templates by name:
# convergence hands them to `_Converger.file` as callbacks over the text
# alone, so the name has to be bound here rather than at the call site.


#: The one line-managed template whose lines have to be in a particular
#: order to mean the right thing; ``.gitignore``'s only do so among
#: themselves, which appending already preserves.
_GITATTRIBUTES = "gitattributes.tmpl"


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


def gitignore_repair(text: str) -> str | None:
    """Append the managed ignore patterns a file is missing.

    Args:
        text: The file's current contents.

    Returns:
        The repaired text, or ``None`` if nothing was missing.
    """
    return _repair("gitignore.tmpl", text)


def gitattributes_repair(text: str) -> str | None:
    """Append the managed attribute lines a file is missing.

    More is at stake than in ``.gitignore``: a ``.gitattributes`` the user
    wrote first would leave result bytes routed into git, not the annex.
    What appending cannot always achieve is the *order* they need to be in
    — see :func:`gitattributes_disorder`.

    Args:
        text: The file's current contents.

    Returns:
        The repaired text, or ``None`` if nothing was missing.
    """
    return _repair(_GITATTRIBUTES, text)


def gitattributes_disorder(text: str) -> str:
    """Name the managed line a repair would leave in the wrong place.

    ``.gitattributes`` is last-match-wins, so the two ``*`` defaults have
    to come *before* the lines that opt out of them. A repair only
    appends, so a file already carrying ``results/** annex.largefiles=
    anything`` gets ``* annex.largefiles=nothing`` added after it — and
    every result then lands in git as a plain blob while convergence
    reports the file repaired and the project converged.

    Judged on the text a repair would produce, not the text as it stands:
    a file missing the defaults entirely is in order today and out of it
    the moment they are appended. And only lines setting the *same*
    attribute can override one another, so ``* filter=annex`` landing
    below ``results/** annex.largefiles=anything`` is not disorder —
    neither says anything about the other.

    Args:
        text: The file's current contents.

    Returns:
        The first managed line that ends up below one it has to precede,
        or empty when the order is right.
    """
    rank = {line: i for i, line in enumerate(entries(_GITATTRIBUTES))}
    lowest: dict[str, int] = {}
    for line in _lines(_repair(_GITATTRIBUTES, text) or text):
        if (place := rank.get(line)) is None:
            continue
        for attribute in _attributes(line):
            if place < lowest.get(attribute, -1):
                return line
            lowest[attribute] = place
    return ""


def _attributes(line: str) -> list[str]:
    """The attribute names one ``.gitattributes`` line sets.

    A line is a pattern followed by specs — ``attr``, ``-attr``, ``!attr``
    or ``attr=value``.
    """
    return [spec.split("=")[0].lstrip("-!") for spec in line.split()[1:]]


# =============================================================================
# Files the caller supplies a value for
# =============================================================================


def datalad_config(*, dataset_id: str) -> str:
    """Render ``.datalad/config``, the file that makes a project a dataset.

    A dataset id is the one thing a git + git-annex repository lacks to
    *be* a DataLad dataset. Read back only through ``dataset.dataset_id``,
    for the run record's ``dsid``.

    Args:
        dataset_id: A UUID, generated once and never regenerated.

    Returns:
        A git-config file carrying ``datalad.dataset.id``.
    """
    return _render("datalad-config.tmpl", dataset_id=dataset_id)


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
