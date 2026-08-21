"""The git + git-annex seam: how a project stores what it produced.

Storage follows the DataLad model — git carries the pointers and the
history, git-annex carries the bytes — reached through ordinary ``git``
and ``git annex`` commands. ``.gitattributes`` routes content: the default
is ``annex.largefiles=nothing``, and outputs and declared inputs opt out
of it, so manifests and analysis code stay in git while results and data
go to the annex.

Every command goes through :func:`~lightcone.engine.project._run`, the
same seam convergence uses, so there is one place to monkeypatch and every
invocation is inspectable.

``.gitattributes`` sets ``filter=annex``, so an ordinary ``git add``
routes content to the annex. Nothing here — and nothing lc documents —
asks anyone to run a git-annex command by hand.
"""

from __future__ import annotations

import os
import shlex
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from lightcone.engine import project


@dataclass(frozen=True)
class LastWrite:
    """The commit that last touched a path — empty fields mean "cannot
    say", and the whole record is falsy then."""

    sha: str = ""
    subject: str = ""
    author: str = ""
    email: str = ""
    date: str = ""

    def __bool__(self) -> bool:
        return bool(self.sha)

# =============================================================================
# The repository
# =============================================================================


def init_git(directory: Path) -> None:
    """Create the repository the project's history lives in.

    Args:
        directory: Where to run ``git init``.
    """
    _git(["init", "-q"], cwd=directory)


def init_annex(directory: Path) -> None:
    """Create the object store the bytes live in.

    Args:
        directory: A directory inside the repository to annex.
    """
    _git(["annex", "init", "-q"], cwd=directory)


def is_annexed(directory: Path) -> bool:
    """Report whether the enclosing repository has an annex.

    Asks git-annex's own question — ``annex.uuid`` is the marker it writes
    on ``init`` — rather than guessing from a directory listing.

    Args:
        directory: A directory inside the repository.

    Returns:
        True if the repository has been annexed.
    """
    return _git_ok(["config", "--get", "annex.uuid"], cwd=directory)


def ignore_rule(directory: Path, path: str) -> str | None:
    """Find the ignore rule covering *path*, if there is one.

    ``--no-index`` asks about the *rules* rather than the index: without
    it, git answers "not ignored" for anything already tracked, which is
    exactly the project where someone tracked a result by hand and left
    the rule for the next one.

    Args:
        directory: The repository to ask in.
        path: The pathspec to ask about, with a trailing slash for a
            directory — a rule like ``results/*`` ignores the contents and
            does not match the bare name.

    Returns:
        ``<file>:<line>:<pattern>``, or ``None`` if nothing ignores it.
        Convergence cannot repair this, so the message must name the line.
    """
    proc = project._run(["git", "check-ignore", "-v", "--no-index", "--", path], cwd=directory)
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    # `<source>:<line>:<pattern>\t<pathname>`, one line per pathspec.
    return proc.stdout.splitlines()[0].split("\t")[0]


# =============================================================================
# The annex plumbing: how the researcher's own git reaches git-annex
# =============================================================================
#
# `git annex init` wires the repository to a bare `git-annex`, resolved
# from whatever PATH the *researcher's* git runs under — the filter
# drivers in `.git/config` and four hooks. When their shell has no
# git-annex (lc itself always does — it bundles one), a plain `git add`
# prints an error, exits 0, and stages the raw bytes into git history:
# a 2 GB dataset lands in git proper, silently, on every clone forever.
# `filter.annex.required=true` turns that into git's own hard failure,
# and — only where PATH would not resolve git-annex — the plumbing is
# pinned to an absolute path instead, so the ordinary `git add` the docs
# promise keeps working. Measured: with the filter pinned, `git add` of
# annexed content stages a pointer with no git-annex on PATH at all.


#: The marker `git annex init` leaves in every hook it writes — and the
#: ownership test: git-annex itself rewrites only hooks that carry it,
#: and so does lc. A hook without it is the user's, never touched.
_HOOK_MARKER = "automatically configured by git-annex"

#: What each hook runs — git-annex's own hook bodies, mirrored, with the
#: annex spelling left open: ``git annex`` in stock form, an absolute
#: path when pinned.
_HOOKS = {
    "pre-commit": "{annex} pre-commit .",
    "post-checkout": "{annex} smudge --update",
    "post-merge": "{annex} smudge --update",
    "post-receive": (
        "if {annex} post-receive --help >/dev/null 2>&1; then {annex} post-receive; fi"
    ),
}

#: The filter drivers `git annex init` configures, same convention
#: (stock spells these ``git-annex``, without the space). The
#: smudge/clean pair stays beside ``process``: git falls back to it when
#: the process filter cannot start, and a fallback that resolves
#: git-annex differently from the filter it stands in for would route
#: the same add two ways.
_FILTERS = {
    "filter.annex.process": "{annex} filter-process",
    "filter.annex.smudge": "{annex} smudge -- %f",
    "filter.annex.clean": "{annex} smudge --clean -- %f",
}


def pinned_annex(directory: Path) -> Path | None:
    """Read which absolute git-annex the filter is pinned to, if any.

    The record convergence's repair rule works from: the first word of
    ``filter.annex.process``. Stock plumbing spells it bare
    (``git-annex filter-process``), which is not a pin.

    Args:
        directory: A directory inside the repository.

    Returns:
        The pinned executable, or ``None`` for stock or absent plumbing.
    """
    try:
        words = shlex.split(_config(directory, "filter.annex.process"))
    except ValueError:
        return None
    if words and Path(words[0]).is_absolute():
        return Path(words[0])
    return None


def annex_runs(path: Path) -> bool:
    """Whether *path* is still an executable — the repair-rule probe.

    A pinned engine can be moved or its uv cache pruned; a pin that no
    longer runs is what turns ``required=true``'s loud failure back into
    a working ``git add`` on the next ``lc init``.

    Args:
        path: The recorded git-annex executable.

    Returns:
        True if the file exists and is executable.
    """
    return path.is_file() and os.access(path, os.X_OK)


def converge_annex_plumbing(directory: Path, pin: Path | None) -> None:
    """Write the plumbing that lets the researcher's own git find git-annex.

    ``filter.annex.required=true`` always: without it a ``git add`` whose
    filter cannot start exits 0 and stages raw bytes into git history —
    silent corruption; with it, git refuses loudly and ``lc init`` is the
    remedy. The filter drivers and the four hooks are written in stock
    form (PATH-resolved, as ``git annex init`` writes them) or pinned to
    one absolute executable — the caller decides which, because only it
    knows what the researcher's shell resolves. Hooks keep git-annex's
    own shebang and marker comment, so git-annex still recognises them
    as machinery; a hook without the marker is the user's and is left
    alone.

    Args:
        directory: A directory inside the repository.
        pin: The absolute git-annex to pin everything to, or ``None``
            for the stock, PATH-resolved form.
    """
    _git(["config", "filter.annex.required", "true"], cwd=directory)
    config, hook = _annex_spellings(pin)
    for key, command in _FILTERS.items():
        _git(["config", key, command.format(annex=config)], cwd=directory)
    # A hooks directory that is not there holds nothing git would run, so
    # there is nothing to converge in it — `git init` always creates one.
    hooks = _hooks_dir(directory)
    if not hooks.is_dir():
        return
    marker = _HOOK_MARKER + (", pinned by lightcone-cli" if pin else "")
    for name, command in _HOOKS.items():
        path = hooks / name
        if path.exists() and _HOOK_MARKER not in path.read_text():
            continue
        path.write_text(f"#!/bin/sh\n# {marker}\n{command.format(annex=hook)}\n")
        path.chmod(0o755)


def annex_plumbing_current(directory: Path, *, stock_ok: bool) -> bool:
    """Whether the plumbing needs no write on this host.

    Current means: ``required=true`` is set, and the filter drivers and
    every hook lc owns resolve git-annex the same one way — a stock,
    PATH-resolved spelling where the shell can honour it, or a pin that
    still runs. A pin that works stays current even when PATH could now
    serve (both work, and rewriting would flip-flop between a tool
    install and a uvx cache); only a broken state reports otherwise.

    Args:
        directory: A directory inside the repository.
        stock_ok: Whether PATH-resolved plumbing works for the
            researcher's shell — the caller's :func:`~lightcone.engine.
            project.ambient_annex` question.

    Returns:
        True if a convergence would write nothing.
    """
    if _config(directory, "filter.annex.required") != "true":
        return False
    pin = pinned_annex(directory)
    if pin is None and not stock_ok:
        return False
    if pin is not None and not annex_runs(pin):
        return False
    config, hook = _annex_spellings(pin)
    if any(_config(directory, k) != c.format(annex=config) for k, c in _FILTERS.items()):
        return False
    hooks = _hooks_dir(directory)
    if not hooks.is_dir():
        return True  # nothing git would run — the write path's judgment too
    for name, command in _HOOKS.items():
        path = hooks / name
        text = path.read_text() if path.exists() else ""
        if text and _HOOK_MARKER not in text:
            continue  # the user's hook — theirs in the write path too
        if command.format(annex=hook) not in text:
            return False
    return True


def _annex_spellings(pin: Path | None) -> tuple[str, str]:
    """How the config and the hooks spell git-annex, stock or pinned.

    Stock differs between the two sites — the filters call the
    executable (``git-annex``), the hooks go through git's dispatch
    (``git annex``) — while a pin is one absolute path at both.
    """
    if pin is None:
        return "git-annex", "git annex"
    quoted = shlex.quote(pin.as_posix())
    return quoted, quoted


def _config(directory: Path, key: str) -> str:
    """Read one repository config value, empty when unset."""
    found = project._run(["git", "config", "--get", key], cwd=directory)
    return str(found.stdout or "").strip() if found.returncode == 0 else ""


def _hooks_dir(directory: Path) -> Path:
    """Where this repository's hooks live.

    Asked of git rather than spelled ``.git/hooks``: in a linked
    worktree ``.git`` is a file, hooks live in the common directory, and
    ``core.hooksPath`` can move them anywhere — all questions
    ``--git-path`` already answers.
    """
    hooks = _git(["rev-parse", "--git-path", "hooks"], cwd=directory).strip()
    return (directory / hooks).resolve()


# =============================================================================
# What a run does to the repository
# =============================================================================


def status(directory: Path) -> list[tuple[str, str]]:
    """List *directory*'s uncommitted changes.

    Honours ``.gitignore``, so ``.venv/`` never counts; ``data/`` and
    ``results/`` do, which is the point — inputs are committed before
    anything computes on them.

    Scoped to *directory* and reported relative to it, both deliberately.
    A project can sit inside a larger repository — ``lc init subdir/``
    adopts an enclosing work tree rather than nesting a new one — and
    porcelain otherwise covers that whole tree and names paths from *its*
    root: an edit somewhere else in the repository would refuse every run,
    and lc's own writes would arrive as ``subdir/results/…``, which no
    caller sorting by path class can recognise.

    Args:
        directory: The project to inspect.

    Returns:
        ``(status code, path)`` for each change, paths relative to
        *directory*, empty when clean. A wholly untracked project collapses
        to ``.``, which is git's own summary of it and what the caller
        would tell the user to add.
    """
    prefix = _git(["rev-parse", "--show-prefix"], cwd=directory).strip()
    lines = _git(["status", "--porcelain", "--", "."], cwd=directory).splitlines()
    return [
        (line[:2], line[3:].removeprefix(prefix) or ".") for line in lines if line.strip()
    ]


def require_committer(directory: Path) -> None:
    """Refuse a repository that cannot make a commit yet.

    git needs an identity to commit, and a fresh container or CI image has
    none — the case this CLI is most often run in. Asked at the start of a
    run rather than discovered at the first save, because by then a recipe
    has already run and its work is about to be restored away over a
    setting that takes one command to fix.

    Asked as ``git var``, which is the question a commit itself asks:
    an identity can come from ``user.email``, ``EMAIL``, the author and
    committer variables, or three levels of config, and reimplementing
    that lookup here is how a probe comes to disagree with the thing it
    is standing in for.

    Args:
        directory: The repository that is about to be committed to.

    Raises:
        ProjectError: If no committer identity resolves.
    """
    proc = project._run(["git", "var", "GIT_COMMITTER_IDENT"], cwd=directory)
    if proc.returncode != 0:
        raise project.ProjectError(
            "git has no identity to commit with, and every materialized output "
            "is committed:\n"
            '  git config --global user.name "Your Name"\n'
            '  git config --global user.email "you@example.com"'
        )


def head(directory: Path) -> tuple[str, str]:
    """Read the commit ``HEAD`` is at and the ``origin`` URL.

    Both are reads and neither takes the index lock.

    Args:
        directory: The repository to read.

    Returns:
        ``(commit sha, origin URL)``. The URL is empty rather than absent
        when the repository has no remote — a manifest field that is
        sometimes missing reads worse than one that is sometimes blank.
    """
    remote = project._run(["git", "config", "--get", "remote.origin.url"], cwd=directory)
    return (
        _git(["rev-parse", "HEAD"], cwd=directory).strip(),
        remote.stdout.strip() if remote.returncode == 0 else "",
    )


def dataset_id(directory: Path) -> str:
    """Read the dataset's UUID out of ``.datalad/config``.

    Through ``git config`` rather than by parsing: lc writes that file and
    otherwise leaves ``.datalad/`` to datalad.

    Args:
        directory: The project root.

    Returns:
        The UUID, or empty for a project assembled without ``lc init``.
    """
    found = project._run(
        ["git", "config", "-f", ".datalad/config", "--get", "datalad.dataset.id"],
        cwd=directory,
    )
    return found.stdout.strip() if found.returncode == 0 else ""


def last_writer(directory: Path, path: Path) -> LastWrite:
    """Find the commit that last touched *path*.

    Run from the project root with a relative pathspec, so it answers
    about the project's own subdirectory even inside an enclosing
    repository — and since nothing parses paths out of its output, no
    prefix handling is needed.

    "Cannot say" is the empty answer, never an error: the callers are
    ``lc status`` and the crate render, and a read-only verb must not
    refuse a project over an unborn HEAD, a deposit stripped of its
    ``.git``, or a host without git — states such projects are actually
    in.

    Args:
        directory: The project root.
        path: The path to ask about, absolute or repository-relative.

    Returns:
        The commit, falsy-empty when none has touched the path — or when
        git cannot answer at all.
    """
    argv = ["log", "-1", "--format=%H%x00%s%x00%an%x00%ae%x00%as", "--", _rel(directory, path)]
    out = _ask(argv, cwd=directory)
    if not (out := (out or "").strip("\n")):
        return LastWrite()
    return LastWrite(*out.split("\0"))


def annex_keys(directory: Path) -> dict[str, str]:
    """Map every annexed file to its key, repository-relative.

    One process for the whole tree. ``--include=*`` is load-bearing: a
    bare ``find`` lists only files whose *content* is present, and the
    crate must answer on a clone that holds none of the bytes — the keys
    are repository state, which is what keeps the render pure.

    "Cannot say" — no annex, no git, an unborn repository — is the empty
    answer, never an error, the :func:`last_writer` discipline.

    Args:
        directory: The project root.

    Returns:
        ``{relative path: key}`` for every annexed file.
    """
    out = _ask(["annex", "find", "--include=*", "--format=${file}\\t${key}\\n"], cwd=directory)
    keys: dict[str, str] = {}
    for line in (out or "").splitlines():
        # From the *last* tab: git-annex emits ${file} unescaped, so a
        # tab in a filename would otherwise split inside the path and
        # hand back a truncated file with a corrupted key. Keys never
        # contain tabs, so the rightmost split is always the real one.
        file, sep, key = line.rpartition("\t")
        if sep and file and key:
            keys[file] = key
    return keys


def save(directory: Path, paths: Iterable[Path], message: str) -> bool:
    """Commit *paths*.

    A plain ``git add``: ``.gitattributes`` sets ``filter=annex``, so git's
    own add routes content to the annex and everything else into git. lc
    runs no annex command here for the same reason it asks nobody else to.

    ``annex.thin`` is set for this add alone, so a result is hard-linked to
    its annex object rather than copied — one copy on disk instead of two.
    It is safe precisely here: thin's hazard is editing a file in place,
    which rewrites the object under the key that names it, and lc never
    does — the worker removes an output directory before rebuilding it, and
    an unlink leaves the object untouched. Setting it repository-wide would
    reach declared inputs instead, which a researcher adds with their own
    `git add` and whose tools very much do open files for update, so it is
    passed per-add and never written to the repository's config.

    ``annex.dotfiles`` is set for the same reason ``annex.thin`` is:
    without it, git-annex routes any file under a dot-directory to git
    *regardless* of what ``annex.largefiles`` says — so the image archive
    under ``.datalad/environments/``, or a ``.cache.h5`` a recipe writes
    into its output directory, would land as a full blob in git,
    silently, with every test green, and every clone would carry the
    bytes forever. With it, ``annex.largefiles`` alone decides — which is
    the storage policy the ``.gitattributes`` template already states,
    dot-named manifests staying in git through their own exemption.
    Per-add and never written to the repository's config, so a user's
    own ``git add`` keeps git-annex's stock behavior.

    The commit carries the same pathspec as the add, making it a
    *partial* commit: git builds it from HEAD plus these paths alone and
    leaves anything else in the index staged and untouched. Without the
    pathspec, ``git commit`` commits the whole index — so a file the
    user staged while a graph was running would be swept, silently,
    into whichever save landed next. The end-of-run warning names such
    edits; this is what keeps lc's commits from eating them.

    Args:
        directory: The repository root.
        paths: What to stage, absolute or repository-relative.
        message: The commit message.

    Returns:
        False if there was nothing to commit.
    """
    relative = [_rel(directory, p) for p in paths]
    annex = ["-c", "annex.thin=true", "-c", "annex.dotfiles=true"]
    _git([*annex, "add", "-A", "--", *relative], cwd=directory)
    # Scoped like the commit: foreign staged content must neither count
    # as "something to commit" here nor be committed below.
    if _git_ok(["diff", "--cached", "--quiet", "--", *relative], cwd=directory):
        return False
    # The annex config rides on the commit too: a partial commit takes
    # the paths' content through the clean filter again, and without the
    # flags that pass would route the bytes by stock rules.
    _git([*annex, "commit", "-q", "-m", message, "--", *relative], cwd=directory)
    return True


def restore(directory: Path, paths: Iterable[Path]) -> None:
    """Put *paths* back the way the last commit had them.

    ``clean`` first for what a run wrote, then ``checkout`` for what it
    deleted or truncated — and only when the path is in ``HEAD``, since a
    first materialization has nothing to go back to.

    Args:
        directory: The repository root.
        paths: What to restore. Scoped to these and never the whole tree:
            a failed run must not discard edits made while it ran.
    """
    for path in paths:
        rel = _rel(directory, path)
        _git(["clean", "-qfdx", "--", rel], cwd=directory)
        if _git_ok(["cat-file", "-e", f"HEAD:{rel}"], cwd=directory):
            _git(["checkout", "-q", "HEAD", "--", rel], cwd=directory)


# =============================================================================
# Running git
# =============================================================================


def _ask(argv: list[str], *, cwd: Path) -> str | None:
    """Run git where "cannot say" must be an answer, never an error.

    The read-only-verbs discipline, as one seam: an unborn HEAD, a
    stripped ``.git``, a host without git — states a project can really
    be in — come back as ``None``, and the caller renders its own empty.

    Returns:
        git's stdout, or ``None`` when git cannot answer.
    """
    try:
        proc = project._run(["git", *argv], cwd=cwd)
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return str(proc.stdout or "")


def _git(argv: list[str], *, cwd: Path) -> str:
    """Run git in *cwd*, returning its stdout; a nonzero exit raises."""
    proc = project._run(["git", *argv], cwd=cwd)
    if proc.returncode != 0:
        raise project.ProjectError(f"`git {' '.join(argv)}` failed:\n{proc.stderr.strip()}")
    return str(proc.stdout or "")


def _git_ok(argv: list[str], *, cwd: Path) -> bool:
    """Run git as a yes/no probe: exit status is the answer, not a failure."""
    return bool(project._run(["git", *argv], cwd=cwd).returncode == 0)


def _rel(directory: Path, path: Path) -> str:
    """*path* as a repository-relative POSIX pathspec.

    git pathspecs are ``/``-separated whatever the platform, and an
    absolute path would silently mean something else inside a repository
    reached through a symlink.
    """
    resolved = Path(path)
    if resolved.is_absolute():
        resolved = resolved.relative_to(directory.resolve())
    return resolved.as_posix()
