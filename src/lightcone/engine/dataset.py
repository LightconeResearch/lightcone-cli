"""The git + git-annex seam: how a project stores what it produced.

Storage follows the DataLad model — git carries the pointers and the
history, git-annex carries the bytes — reached through ordinary ``git``
and ``git annex`` commands. ``.gitattributes`` routes content: the default
is ``annex.largefiles=nothing``, and outputs and declared inputs opt out
of it, so records and analysis code stay in git while results and data
go to the annex.

Every command goes through :func:`~lightcone.engine.project._run`, the
same seam convergence uses, so there is one place to monkeypatch and every
invocation is inspectable.

``.gitattributes`` sets ``filter=annex``, so an ordinary ``git add``
routes content to the annex. Nothing here — and nothing lc documents —
asks anyone to run a git-annex command by hand.
"""

from __future__ import annotations

import posixpath
import subprocess
import time
from collections.abc import Iterable, Sequence
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
# The annex filter: a missing git-annex must not be silent
# =============================================================================


def set_annex_filter_required(directory: Path) -> None:
    """Make git refuse, rather than corrupt, when the annex filter cannot run.

    ``git annex init`` wires ``filter.annex.*`` to a bare ``git-annex``,
    resolved from the ``PATH`` of whichever git runs — and the
    researcher's shell, unlike lc's own environment, may hold none.
    Measured: a ``git add`` whose clean filter cannot start prints an
    error, **exits 0**, and stages the raw bytes into git history, so a
    2 GB input lands in git proper on every clone forever. This flag is
    what turns that into git's own hard failure instead
    (``fatal: <path>: clean filter 'annex' failed``), which is the whole
    difference between a loud stop and silent corruption.

    Args:
        directory: A directory inside the repository.
    """
    _git(["config", "filter.annex.required", "true"], cwd=directory)


def annex_filter_required(directory: Path) -> bool:
    """Whether *this repository* already carries that flag.

    ``--local`` because the write is repository-local, and a probe that
    read the merged config would answer for the *host* instead: a user
    who once set ``filter.annex.required`` in ``~/.gitconfig`` would have
    every project report converged while its own ``.git/config`` carried
    nothing, leaving the protection behind the moment that repository is
    used under another ``HOME`` — a clone, CI, a container. ``--type=bool``
    because git's booleans are not one spelling: ``1``, ``yes``, ``on``
    and a valueless key all mean true to the filter, and reading them as
    drift would rewrite a repository that was already correct.

    Args:
        directory: A directory inside the repository.

    Returns:
        True if ``filter.annex.required`` is set, in this repository, to
        anything git reads as true.
    """
    set_to = _ask(
        ["config", "--local", "--get", "--type=bool", "filter.annex.required"],
        cwd=directory,
    )
    return (set_to or "").strip() == "true"


# =============================================================================
# What a run does to the repository
# =============================================================================


def status(
    directory: Path,
    *,
    untracked_files: bool = False,
    also: Sequence[str] = (),
    config: Sequence[str] = (),
) -> list[tuple[str, str]]:
    """List *directory*'s uncommitted changes.

    Honours ``.gitignore``, so ``.venv/`` never counts; ``data/`` and
    ``results/`` do, which is the point — inputs are committed before
    anything computes on them.

    A submodule counts as changed whenever its commit, its files or its
    untracked files differ, whatever ``.gitmodules`` asks git to ignore:
    a recipe that reads from it would otherwise run on bytes the commit
    does not name.

    Scoped to *directory* and reported relative to it, both deliberately.
    A project can sit inside a larger repository — ``lc init subdir/``
    adopts an enclosing work tree rather than nesting a new one — and
    porcelain otherwise covers that whole tree and names paths from *its*
    root: an edit somewhere else in the repository would refuse every run,
    and lc's own writes would arrive as ``subdir/results/…``, which no
    caller sorting by path class can recognise.

    Read NUL-separated and with renames off, so each entry is one path as
    it is on disk: git otherwise quotes a name holding a space or an
    accent, and pairs a renamed file with where it came from.

    Args:
        directory: The project to inspect.
        untracked_files: List each untracked file, where git otherwise
            names only the outermost untracked folder — for a caller that
            sorts changes by where they are.
        also: More pathspecs to cover, from *directory*: the folder of an
            analysis the project declares beside itself, or an exclusion.
        config: ``-c`` settings for the command, for a caller that keeps
            a filter from running.

    Returns:
        ``(status code, path)`` for each change, paths relative to
        *directory*, empty when clean. A wholly untracked project collapses
        to ``.``, which is git's own summary of it and what the caller
        would tell the user to add.
    """
    prefix = _git(["rev-parse", "--show-prefix"], cwd=directory).strip()
    each = ["--untracked-files=all"] if untracked_files else []
    argv = ["status", "--porcelain", "-z", "--no-renames", "--ignore-submodules=none"]
    listed = _git([*config, *argv, *each, "--", ".", *also], cwd=directory)
    changes = []
    for entry in filter(None, listed.split("\0")):
        path = posixpath.relpath(entry[3:], prefix or ".")
        # An untracked folder keeps the slash that says it is one.
        changes.append((entry[:2], f"{path}/" if entry.endswith("/") and path != "." else path))
    return changes


def require_committer(directory: Path) -> None:
    """Refuse a repository that cannot make a commit yet.

    git needs an identity to commit, and a fresh container or CI image has
    none — the case this CLI is most often run in. Asked at the start of a
    run rather than discovered at the first save, because by then a recipe
    has already run and its result would be reported made but not
    recorded, over a setting that takes one command to fix.

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
        when the repository has no remote.
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


def last_writer(directory: Path, *paths: Path | str) -> LastWrite:
    """Find the commit that last touched any of *paths*.

    Run from the project root with a relative pathspec, so it answers
    about the project's own subdirectory even inside an enclosing
    repository — and since nothing parses paths out of its output, no
    prefix handling is needed.

    "Cannot say" is the empty answer, never an error: the callers are
    ``lc status`` and the crate render, and a read-only verb must not
    refuse a project over an unborn HEAD, a deposit stripped of its
    ``.git``, or a host without git — states such projects are actually
    in.

    Several paths, because a result is a file *and* its record and a
    hand edit to either contradicts the other: the payload alone would
    miss a doctored record, and the record alone would miss the forged
    result that is the whole reason to ask. The record also carries the
    history across a re-declared format, where the payload's path is new.

    Args:
        directory: The project root.
        paths: What to ask about, absolute, repository-relative, or a git
            pathspec.

    Returns:
        The commit, falsy-empty when none has touched them — or when git
        cannot answer at all.
    """
    argv = [
        "log",
        "-1",
        "--format=%H%x00%s%x00%an%x00%ae%x00%as",
        "--",
        *(_rel(directory, p) for p in paths),
    ]
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


def untracked(directory: Path, paths: Iterable[str]) -> list[str]:
    """Find which of *paths* git is not tracking.

    Asked of the index rather than of ``status``, because an ignored file
    is neither tracked nor a change: a tree can be clean without holding
    it.

    Args:
        directory: The project root.
        paths: Files, from the project root.

    Returns:
        Those the index does not hold, in the order given.
    """
    paths = list(paths)
    listed = _git(["ls-files", "-z", "--", *paths], cwd=directory).split("\0")
    return [path for path in paths if path not in listed]


def tracked_records(directory: Path) -> list[str]:
    """Every record git is tracking under the project's own ``.astra/``.

    From the index rather than a directory walk: it is exact and it costs
    one process. Untracked strays are the dirty check's business, not
    this one's.

    Args:
        directory: The project root.

    Returns:
        Paths from the project root, unordered.
    """
    listed = _git(["ls-files", "-z", "--", ":(glob).astra/records/**/*.json"], cwd=directory)
    return [name for name in listed.split("\0") if name]


def annex_objects(directory: Path, keys: Sequence[str]) -> dict[str, Path]:
    """Find where the annex keeps the content of each of *keys*.

    One process for any number of keys. The store's layout is git-annex's
    own, so it is asked and never computed.

    Args:
        directory: A directory inside the repository.
        keys: git-annex keys.

    Returns:
        ``{key: object}`` for each key whose content this clone holds.

    Raises:
        ProjectError: If git-annex cannot be asked.
    """
    if not keys:
        return {}
    asked = project._run(
        ["git", "annex", "contentlocation", "--batch"],
        cwd=directory,
        stdin="".join(f"{key}\n" for key in keys),
    )
    if asked.returncode != 0:
        raise project.ProjectError(
            f"`git annex contentlocation` failed:\n{asked.stderr.strip()}"
        )
    # One line per key, empty for content that is elsewhere.
    answers = asked.stdout.splitlines()
    return {key: directory / where for key, where in zip(keys, answers, strict=True) if where}


def annex_get(directory: Path, keys: Sequence[str]) -> str:
    """Fetch into this clone the content of each of *keys* it lacks.

    By key and not by path, so it is the content a commit names that is
    fetched, whatever the working tree holds by now. Content already here
    costs nothing.

    Args:
        directory: A directory inside the repository.
        keys: git-annex keys.

    Returns:
        Empty when every key's content is here now. Otherwise git-annex's
        own account of what it could not get: a fetch that fails is the
        caller's to report, never an error.
    """
    if not keys:
        return ""
    got = project._run(
        ["git", "annex", "get", "--batch-keys"],
        cwd=directory,
        stdin="".join(f"{key}\n" for key in keys),
    )
    if got.returncode == 0:
        return ""
    # git-annex says why on stdout, and only how many failed on stderr.
    return "\n".join(filter(None, (got.stdout.strip(), got.stderr.strip())))


def is_ancestor(directory: Path, commit: str) -> bool:
    """Whether ``HEAD`` is *commit* or descends from it.

    Args:
        directory: The repository to ask in.
        commit: A commit id.

    Returns:
        True where *commit* is in the history ``HEAD`` is on.
    """
    return _git_ok(["merge-base", "--is-ancestor", commit, "HEAD"], cwd=directory)


def branch(directory: Path) -> str:
    """Name the branch ``HEAD`` is on.

    Args:
        directory: The repository to ask in.

    Returns:
        The full ref (``refs/heads/main``), or empty where ``HEAD`` is
        detached.
    """
    return (_ask(["symbolic-ref", "-q", "HEAD"], cwd=directory) or "").strip()


def busy(directory: Path) -> str:
    """Say what a commit made now would run into, or nothing.

    A merge, rebase, cherry-pick or revert that has not been concluded:
    each leaves a file in the git directory while it lasts, and git says
    where — a linked worktree keeps its own. A partial commit is refused
    during the first and the third, after its ``add`` has already staged
    into them; during a rebase or a revert it succeeds, onto a history
    that is about to be rewritten or abandoned (measured). Another git
    command at work on the index is no such state: it is over in a
    moment, and every command here waits it out (:func:`_run`).

    Args:
        directory: The repository to ask in.

    Returns:
        What is under way, in a few words; empty when nothing is.
    """
    states = {
        "MERGE_HEAD": "a merge is under way",
        "CHERRY_PICK_HEAD": "a cherry-pick is under way",
        "REVERT_HEAD": "a revert is under way",
        "rebase-merge": "a rebase is under way",
        "rebase-apply": "a rebase is under way",
    }
    asked = [flag for state in states for flag in ("--git-path", state)]
    where = _git(["rev-parse", *asked], cwd=directory).splitlines()
    return next(
        (why for why, path in zip(states.values(), where, strict=True)
         if (directory / path).exists()),
        "",
    )


def index_held(directory: Path) -> str:
    """Find an index lock that does not go away.

    A git command holds ``index.lock`` while it writes, and one that was
    killed leaves it for good: every commit after that fails, which for a
    run is every result, after its recipe has run. Asked before a run,
    with the patience a live command deserves.

    Args:
        directory: The repository to ask in.

    Returns:
        The lock's path while it is still there after :data:`_PATIENCE`
        seconds; empty once it is gone.
    """
    lock = directory / _git(["rev-parse", "--git-path", "index.lock"], cwd=directory).strip()
    deadline = time.monotonic() + _PATIENCE
    while lock.exists():
        if time.monotonic() > deadline:
            return str(lock)
        time.sleep(0.25)
    return ""


def save(directory: Path, paths: Iterable[Path | str], message: str) -> bool:
    """Commit *paths*.

    A plain ``git add``: ``.gitattributes`` sets ``filter=annex``, so git's
    own add routes content to the annex and everything else into git. lc
    runs no annex command here for the same reason it asks nobody else to.

    ``annex.thin`` is set for this add alone, so a file with one name —
    the image archive — is hard-linked to its annex object rather than
    copied. A landed result has a second name, in the run's checkout, and
    git-annex stores such a file by copy; ``checkout.land`` then makes the
    project's file a link to the stored object. Thin's hazard is editing a
    file in place, which rewrites the object under the key that names it,
    and lc never does. Setting it repository-wide would reach declared
    inputs instead, which a researcher adds with their own `git add` and
    whose tools very much do open files for update, so it is passed
    per-add and never written to the repository's config.

    ``annex.dotfiles`` is set for the same reason ``annex.thin`` is:
    without it, git-annex routes any file under a dot-directory to git
    *regardless* of what ``annex.largefiles`` says — so the image archive
    under ``.datalad/environments/``, or a ``.cache.h5`` a recipe writes
    into its output directory, would land as a full blob in git,
    silently, with every test green, and every clone would carry the
    bytes forever. With it, ``annex.largefiles`` alone decides — which is
    the storage policy the ``.gitattributes`` template already states.
    Per-add and never written to the repository's config, so a user's
    own ``git add`` keeps git-annex's stock behavior.

    The commit carries the same pathspec as the add, making it a
    *partial* commit: git builds it from HEAD plus these paths alone and
    leaves anything else in the index staged and untouched. Without the
    pathspec, ``git commit`` commits the whole index — so a file the
    user staged while a graph was running would be swept, silently,
    into whichever save landed next.

    The project's tree is in use while a run saves into it, and git
    refuses a second writer of the index rather than wait: every git
    command here waits that out (:func:`_run`).

    Args:
        directory: The repository root.
        paths: What to stage — a path, or a git pathspec.
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


def restore(directory: Path, paths: Iterable[Path | str]) -> None:
    """Put *paths* back the way the last commit had them.

    ``reset`` first for what an interrupted save had staged, since
    ``clean`` leaves anything the index holds; ``clean`` for what was
    written; then ``checkout`` for what was deleted or replaced — through
    ``_git_ok``, because a path that was never committed has nothing to
    go back to and ``checkout`` exits nonzero on a pathspec it cannot
    match.

    No guard in front of it: a pathspec may be a glob, and nothing cheap
    answers "is this in HEAD" for one. ``cat-file -e`` takes a single
    path, ``ls-tree`` refuses glob magic, and ``ls-files --with-tree``
    also lists what is staged but *unborn* — so it says yes exactly where
    ``checkout`` then says no, which is the state an interrupted save
    leaves behind.

    Args:
        directory: The repository root.
        paths: What to restore, as paths or git pathspecs. Scoped to these
            and never the whole tree: a failed run must not discard edits
            made while it ran.
    """
    for path in paths:
        rel = _rel(directory, path)
        _git_ok(["reset", "-q", "--", rel], cwd=directory)
        _git(["clean", "-qfdx", "--", rel], cwd=directory)
        _git_ok(["checkout", "-q", "HEAD", "--", rel], cwd=directory)


# =============================================================================
# Running git
# =============================================================================


#: How long a git command waits for another to let go of the index. A
#: plain ``git add`` of a file of a few gigabytes holds it for ten
#: seconds and more while the annex filter hashes it (measured).
_PATIENCE = 60.0


def _run(argv: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run git, waiting out another git command's hold on the index.

    git refuses a second writer of the index at once, and the project's
    tree is one the researcher, their editor and a run all use. Past
    :data:`_PATIENCE` seconds, git's own refusal is the answer.
    """
    deadline = time.monotonic() + _PATIENCE
    while True:
        proc = project._run(["git", *argv], cwd=cwd)
        if (
            proc.returncode == 0
            or "index.lock" not in (proc.stderr or "")
            or time.monotonic() > deadline
        ):
            return proc
        time.sleep(0.25)


def _ask(argv: list[str], *, cwd: Path) -> str | None:
    """Run git where "cannot say" must be an answer, never an error.

    The read-only-verbs discipline, as one seam: an unborn HEAD, a
    stripped ``.git``, a host without git — states a project can really
    be in — come back as ``None``, and the caller renders its own empty.

    Returns:
        git's stdout, or ``None`` when git cannot answer.
    """
    try:
        proc = _run(argv, cwd=cwd)
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return str(proc.stdout or "")


def _git(argv: list[str], *, cwd: Path) -> str:
    """Run git in *cwd*, returning its stdout; a nonzero exit raises."""
    proc = _run(argv, cwd=cwd)
    if proc.returncode != 0:
        raise project.ProjectError(f"`git {' '.join(argv)}` failed:\n{proc.stderr.strip()}")
    return str(proc.stdout or "")


def _git_ok(argv: list[str], *, cwd: Path) -> bool:
    """Run git as a yes/no probe: exit status is the answer, not a failure."""
    return bool(_run(argv, cwd=cwd).returncode == 0)


def _rel(directory: Path, path: Path | str) -> str:
    """*path* as a repository-relative POSIX pathspec.

    git pathspecs are ``/``-separated whatever the platform, and an
    absolute path would silently mean something else inside a repository
    reached through a symlink.

    A ``str`` passes through untouched: it is already a pathspec, and one
    carrying magic (``:(glob)…``) is not a path to be normalised.
    """
    if isinstance(path, str):
        return path
    resolved = Path(path)
    if resolved.is_absolute():
        resolved = resolved.relative_to(directory.resolve())
    return resolved.as_posix()
