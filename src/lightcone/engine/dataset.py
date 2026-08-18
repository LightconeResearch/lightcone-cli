"""The git + git-annex seam: how a project stores what it produced.

Storage follows the DataLad model — **git carries the pointers and the
history, git-annex carries the bytes** — and the whole of it is reached
through ordinary ``git`` and ``git annex`` commands. What routes content
to the right one is ``.gitattributes``: the default is
``annex.largefiles=nothing``, and outputs and declared inputs opt out of
it, so a clone that has fetched no annex content can still read every
manifest and analysis code never becomes a read-only symlink.

Every command goes through :func:`~lightcone.engine.project._run`, the
same seam convergence uses, so there is one place the suite has to
monkeypatch and every invocation is inspectable.

The trap worth knowing: a plain ``git add`` of a file that
``.gitattributes`` marks ``annex.largefiles=anything`` does **not** annex
it — it silently commits the bytes into git. ``git annex add`` has to run
first, which is why :func:`save` is ordered the way it is.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Iterable
from pathlib import Path

from lightcone.engine import project

# =============================================================================
# Requirements
# =============================================================================


def require_git() -> None:
    """Refuse early when git is absent.

    git is the one tool uv cannot install, and the only admitted exception
    to an otherwise uv-installable stack. A project's results *are* its
    history, so there is no useful project without it.
    """
    if shutil.which("git") is None:
        raise project.ProjectError(
            "git is required (results are versioned in the repository). "
            "Install it: https://git-scm.com/downloads"
        )


def require_git_annex() -> None:
    """Refuse early when git-annex is not reachable as git reaches it.

    Probed *after* :func:`_put_our_bin_first`, and deliberately by the
    name ``git`` itself searches for: ``git annex`` is not a builtin, it
    is ``git`` finding a ``git-annex`` executable on ``PATH``. Asking any
    other way would answer a question git never asks.
    """
    _put_our_bin_first()
    if shutil.which("git-annex") is None:
        raise project.ProjectError(
            "git-annex is required (it stores the bytes results are made of) "
            "and is not on PATH. It ships as a wheel and installs with the "
            "engine: `uv sync` in the project, or reinstall lightcone-cli."
        )


def _put_our_bin_first() -> None:
    """Prepend the directory holding our own interpreter to ``PATH``.

    ``git annex`` dispatches by searching ``PATH`` for ``git-annex``, and
    ``uv tool install lightcone-cli`` links only *our* entry points into
    ``~/.local/bin`` — the git-annex we resolved as a dependency sits
    beside the interpreter instead. Without this, git either finds
    nothing or finds a system copy.

    Prepend, never append: a system git-annex winning would make the
    version recorded in the lock a fiction. Idempotent, and applied to
    our own environment because that is what child processes inherit.
    """
    ours = str(Path(sys.executable).parent)
    path = os.environ.get("PATH", "")
    if path.split(os.pathsep)[:1] == [ours]:
        return
    os.environ["PATH"] = ours + os.pathsep + path if path else ours


# =============================================================================
# The repository
# =============================================================================


def init_git(directory: Path) -> None:
    """``git init`` — the repository the project's history lives in."""
    _git(["init", "-q"], cwd=directory)


def init_annex(directory: Path) -> None:
    """``git annex init`` — the object store the bytes live in."""
    _git(["annex", "init", "-q"], cwd=directory)


def is_annexed(directory: Path) -> bool:
    """Whether the enclosing repository has an annex.

    ``annex.uuid`` is the marker git-annex itself writes on ``init`` and
    reads to decide the same question, so this asks git-annex rather than
    guessing from a directory listing.
    """
    return _git_ok(["config", "--get", "annex.uuid"], cwd=directory)


def ignore_rule(directory: Path, path: str) -> str | None:
    """Where *path* is git-ignored, as ``<file>:<line>:<pattern>``, or ``None``.

    ``--no-index`` asks about the *rules* rather than about the index:
    without it git answers "not ignored" for anything already tracked,
    which is exactly the project where someone tracked a result by hand
    and left the rule in place for the next one.

    ``-v`` is what makes the answer actionable. Convergence cannot repair
    this — ``.gitignore`` convergence only ever appends — so the message
    has to point at the line to delete, not merely report that one exists.
    """
    proc = project._run(["git", "check-ignore", "-v", "--no-index", "--", path], cwd=directory)
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    # `<source>:<line>:<pattern>\t<pathname>`, one line per pathspec.
    return proc.stdout.splitlines()[0].split("\t")[0]


# =============================================================================
# What a run does to the repository
# =============================================================================


def status(directory: Path) -> list[tuple[str, str]]:
    """The working tree's uncommitted changes, as ``(code, path)`` pairs.

    ``git status --porcelain`` honours ``.gitignore``, so ``.venv/`` never
    counts. ``data/`` and ``results/`` do, now that they are tracked —
    which is the point: inputs are committed before anything computes on
    them, and outputs are committed by the run that produced them.
    """
    lines = _git(["status", "--porcelain"], cwd=directory).splitlines()
    return [(line[:2], line[3:]) for line in lines if line.strip()]


def head(directory: Path) -> tuple[str, str]:
    """The commit ``HEAD`` is at, and the ``origin`` URL if there is one.

    Both are reads and neither takes the index lock, so this is the one
    thing about git a worker may do for itself — it needs them for the
    manifest, and a run starts from a clean tree, so every worker gets the
    same answer.

    The remote is empty rather than absent when there is none: a project
    that has never been pushed is perfectly normal, and a manifest field
    that is sometimes missing is worse to read than one that is sometimes
    blank.
    """
    remote = project._run(["git", "config", "--get", "remote.origin.url"], cwd=directory)
    return (
        _git(["rev-parse", "HEAD"], cwd=directory).strip(),
        remote.stdout.strip() if remote.returncode == 0 else "",
    )


def dataset_id(directory: Path) -> str:
    """The dataset's UUID, read back through git rather than parsed.

    lc writes ``.datalad/config`` and otherwise leaves ``.datalad/`` to
    datalad; asking git for one key out of a git-config file keeps it that
    way. Empty when there is none, which is a project someone assembled
    without ``lc init``.
    """
    found = project._run(
        ["git", "config", "-f", ".datalad/config", "--get", "datalad.dataset.id"],
        cwd=directory,
    )
    return found.stdout.strip() if found.returncode == 0 else ""


def is_clean(directory: Path) -> bool:
    """Whether the working tree has nothing uncommitted."""
    return not status(directory)


def save(directory: Path, paths: Iterable[Path], message: str) -> bool:
    """Commit *paths* with *message*. False if there was nothing to commit.

    ``git annex add`` first, so content that ``.gitattributes`` routes to
    the annex is never captured as a git blob; then ``git add -A``, which
    stages the deletions a rebuild left behind; then a commit with no
    pathspec, because what is staged is exactly what we staged — a run
    starts from a clean tree and workers never touch git.
    """
    relative = [_rel(directory, p) for p in paths]
    _git(["annex", "add", "--quiet", "--", *relative], cwd=directory)
    _git(["add", "-A", "--", *relative], cwd=directory)
    if _git_ok(["diff", "--cached", "--quiet"], cwd=directory):
        return False
    _git(["commit", "-q", "-m", message], cwd=directory)
    return True


def restore(directory: Path, paths: Iterable[Path]) -> None:
    """Put *paths* back the way the last commit had them.

    Scoped to the paths given and never to the whole tree: a run that
    fails must not discard edits made while it was running. ``clean``
    first for what the run wrote, then ``checkout`` for what it deleted or
    truncated — and only when the path is in ``HEAD`` at all, since a
    first materialization has nothing to go back to and ``checkout``
    would fail on the pathspec.
    """
    for path in paths:
        rel = _rel(directory, path)
        _git(["clean", "-qfdx", "--", rel], cwd=directory)
        if _git_ok(["cat-file", "-e", f"HEAD:{rel}"], cwd=directory):
            _git(["checkout", "-q", "HEAD", "--", rel], cwd=directory)


# =============================================================================
# Running git
# =============================================================================


def _git(argv: list[str], *, cwd: Path) -> str:
    """Run git in *cwd*, returning its stdout; a nonzero exit raises."""
    _put_our_bin_first()
    proc = project._run(["git", *argv], cwd=cwd)
    if proc.returncode != 0:
        raise project.ProjectError(f"`git {' '.join(argv)}` failed:\n{proc.stderr.strip()}")
    return str(proc.stdout or "")


def _git_ok(argv: list[str], *, cwd: Path) -> bool:
    """Run git as a yes/no probe: exit status is the answer, not a failure."""
    _put_our_bin_first()
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
