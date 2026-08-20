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

from collections.abc import Iterable
from pathlib import Path

from lightcone.engine import project

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

    Args:
        directory: The repository root.
        paths: What to stage, absolute or repository-relative.
        message: The commit message.

    Returns:
        False if there was nothing to commit.
    """
    relative = [_rel(directory, p) for p in paths]
    _git(
        ["-c", "annex.thin=true", "-c", "annex.dotfiles=true", "add", "-A", "--", *relative],
        cwd=directory,
    )
    if _git_ok(["diff", "--cached", "--quiet"], cwd=directory):
        return False
    _git(["commit", "-q", "-m", message], cwd=directory)
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
