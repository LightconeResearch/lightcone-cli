"""The git + git-annex seam: how a project stores what it produced.

Storage follows the DataLad model — git carries the pointers and the
history, git-annex carries the bytes — reached through ordinary ``git``
and ``git annex`` commands. ``.gitattributes`` routes content: the default
is ``annex.largefiles=nothing``, and results and declared inputs opt out
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

import json
import os
import posixpath
import re
import subprocess
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from lightcone.engine import github, project

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


def status(directory: Path, *, untracked_files: bool = False) -> list[tuple[str, str]]:
    """List *directory*'s uncommitted changes.

    Honours ``.gitignore``, so ``.venv/`` never counts; ``data/`` and
    ``results/`` do, which is the point — inputs are committed before
    anything computes on them.

    Untracked files count whatever ``status.showUntrackedFiles`` says:
    set to ``no``, in the repository or in the user's own config, it
    would hide a file a recipe reads but no commit holds. The setting is
    passed on the command line, which is also what reaches the ``git
    status`` git runs inside each submodule.

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
            sorts changes by whose files they are.

    Returns:
        ``(status code, path)`` for each change, paths relative to
        *directory*, empty when clean. An untracked folder is one entry
        ending in ``/``, and a wholly untracked project collapses to
        ``.``, which is git's own summary of it and what the caller would
        tell the user to add.
    """
    prefix = _git(["rev-parse", "--show-prefix"], cwd=directory).strip()
    listed = _git(
        [
            "-c", f"status.showUntrackedFiles={'all' if untracked_files else 'normal'}",
            "status", "--porcelain", "-z", "--no-renames", "--ignore-submodules=none",
            "--", ".",
        ],
        cwd=directory,
    )  # fmt: skip
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


def toplevel(directory: Path) -> Path:
    """Find the top of the repository *directory* is in.

    Args:
        directory: A folder inside a repository.

    Returns:
        The repository's top folder.
    """
    return Path(_git(["rev-parse", "--show-toplevel"], cwd=directory).strip())


def head(directory: Path) -> str:
    """Read the commit ``HEAD`` is at.

    Args:
        directory: The repository to read.

    Returns:
        The commit's sha.
    """
    return _git(["rev-parse", "HEAD"], cwd=directory).strip()


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


def not_in_git(directory: Path, paths: Iterable[str]) -> list[str]:
    """Find which of *paths* git does not hold as the bytes in the tree.

    Asked of the index rather than of ``status``, because an ignored file
    is neither tracked nor a change: a tree can be clean without holding
    it. A symlink git tracks is held as the path it points to, and an
    annexed file as a pointer to its bytes, so what is held is compared
    with the file's own bytes, hashed as git would with no filter.

    Args:
        directory: The project root.
        paths: Files, from the project root.

    Returns:
        Those the index does not hold as their own bytes — untracked, a
        symlink, a submodule or annexed — in the order given.
    """
    paths = list(paths)
    listed = _git(["ls-files", "-s", "-z", "--", *paths], cwd=directory).split("\0")
    # `<mode> <object> <stage>\t<path>`: a file's own bytes are mode 100644
    # or 100755.
    held = {
        entry.partition("\t")[2]: entry.split(" ")[1]
        for entry in listed
        if entry.startswith(("100644 ", "100755 "))
    }
    files = [path for path in held if not (directory / path).is_symlink()]
    hashed = (
        _git(["hash-object", "--no-filters", "--", *files], cwd=directory).split() if files else []
    )
    own = {path for path, blob in zip(files, hashed, strict=True) if held[path] == blob}
    return [path for path in paths if path not in own]


def require_free_index(directory: Path) -> None:
    """Refuse a repository whose index lock does not go away.

    A git command holds ``index.lock`` while it writes, and one that was
    killed leaves it for good: every commit after that fails, which for a
    run is every result, after its recipe has run. Asked before work that
    ends in a commit, with the patience a live command deserves.

    Args:
        directory: The repository that is about to be committed to.

    Raises:
        ProjectError: If the lock is still there after :data:`_PATIENCE`
            seconds.
    """
    lock = directory / _git(["rev-parse", "--git-path", "index.lock"], cwd=directory).strip()
    deadline = time.monotonic() + _PATIENCE
    while lock.exists():
        if time.monotonic() > deadline:
            raise project.ProjectError(
                f"another git command holds the project's index ({lock}), so nothing "
                "could be committed. Wait for it to end; if none is running, a git that "
                "was killed left that file, and removing it is the remedy."
            )
        time.sleep(0.25)


def save(
    directory: Path,
    paths: Iterable[Path | str],
    message: str,
    *,
    annexed: Iterable[Path] = (),
    in_git: Iterable[Path] = (),
) -> bool:
    """Commit *paths*.

    A plain ``git add``: ``.gitattributes`` sets ``filter=annex``, so git's
    own add routes content to the annex and everything else into git.

    What *annexed* and *in_git* name is checked where git staged it before
    anything is committed, since that is the one answer to where its bytes
    go: the lines that route it are the researcher's to edit, an
    ``annex.largefiles`` expression can turn on the very bytes, and a
    clone where git-annex was never set up takes no filter at all. Each is
    asked of git-annex's own lookup in the index.

    ``annex.thin`` is set for this add alone, so a result is hard-linked to
    its annex object rather than copied — one copy on disk instead of two.
    It is safe precisely here: thin's hazard is editing a file in place,
    which rewrites the object under the key that names it, and lc never
    does — the worker removes a result's files before making it again, and
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
    records under ``.astra/`` staying in git through their own line.
    Per-add and never written to the repository's config, so a user's
    own ``git add`` keeps git-annex's stock behavior.

    The commit carries the same pathspec as the add, making it a
    *partial* commit: git builds it from HEAD plus these paths alone and
    leaves anything else in the index staged and untouched. Without the
    pathspec, ``git commit`` commits the whole index — so a file the
    user staged while a graph was running would be swept, silently,
    into whichever save landed next. The end-of-run warning names such
    edits; this is what keeps lc's commits from eating them.

    The project's tree is in use while a run saves into it, and git
    refuses a second writer of the index rather than wait: every git
    command here waits that out (:func:`_run`).

    git-annex keeps what the add told it — that this repository holds the
    content — in its journal, until one of its own commands commits that to
    its branch; a clone made before then is told of no copy, and cannot
    fetch what was just stored (*measured*). The command here is a sync of
    the git-annex branch alone, with no remote and no content: never ``git
    annex merge``, which merges a pending ``synced/`` branch into the one
    checked out (*measured*), in the middle of a run.

    Args:
        directory: The repository root.
        paths: What to stage — a path, or a git pathspec.
        message: The commit message.
        annexed: Files among *paths* whose bytes have to go to the annex.
        in_git: Files among *paths* that have to stay in git.

    Returns:
        False if there was nothing to commit.

    Raises:
        ProjectError: Naming each file staged where it must not go, with
            nothing committed and nothing left staged.
    """
    relative = [_rel(directory, p) for p in paths]
    annex = ["-c", "annex.thin=true", "-c", "annex.dotfiles=true"]
    _git([*annex, "add", "-A", "--", *relative], cwd=directory)
    # Scoped like the commit: foreign staged content must neither count
    # as "something to commit" here nor be committed below.
    if _git_ok(["diff", "--cached", "--quiet", "--", *relative], cwd=directory):
        return False
    to_annex = [_rel(directory, path) for path in annexed]
    to_git = [_rel(directory, path) for path in in_git]
    if asked := [*to_annex, *to_git]:
        looked = project._run(
            ["git", "annex", "lookupkey", "--batch"],
            cwd=directory,
            stdin="".join(f"{path}\n" for path in asked),
        )
        if looked.returncode != 0:
            raise project.ProjectError(
                f"`git annex lookupkey` failed:\n{looked.stderr.strip()}"
            )
        # One line out per path in: its key, or empty for one git holds.
        key = dict(zip(asked, looked.stdout.split("\n"), strict=False))
        wrong = [f"{path} was staged in git itself, not the annex"
                 for path in to_annex if not key.get(path)]
        wrong += [f"{path} was staged in the annex, where a clone with no content cannot read it"
                  for path in to_git if key.get(path)]
        if wrong:
            _git(["reset", "-q", "--", *relative], cwd=directory)
            raise project.ProjectError(
                "git would not store these as lc stores them, so nothing was "
                "committed:\n  " + "\n  ".join(wrong) + "\nThe last .gitattributes line "
                "that matches each decides, and `lc init` sets up git-annex in a clone "
                "where it is not."
            )
    # The annex config rides on the commit too: a partial commit takes
    # the paths' content through the clean filter again, and without the
    # flags that pass would route the bytes by stock rules.
    _git([*annex, "commit", "-q", "-m", message, "--", *relative], cwd=directory)
    _git(
        ["annex", "sync", "-q", "--only-annex", "--no-pull", "--no-push", "--no-content",
         "--no-commit"],
        cwd=directory,
    )  # fmt: skip
    return True


def restore(directory: Path, paths: Iterable[Path | str]) -> None:
    """Put *paths* back the way the last commit had them.

    ``clean`` first for what a run wrote, then ``checkout`` for what it
    deleted or truncated — through ``_git_ok``, because a first
    materialization has nothing to go back to and ``checkout`` exits
    nonzero on a pathspec it cannot match.

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
        _git(["clean", "-qfdx", "--", rel], cwd=directory)
        _git_ok(["checkout", "-q", "HEAD", "--", rel], cwd=directory)


# =============================================================================
# Where the bytes are
# =============================================================================

#: Why bytes are out of this clone's reach: no repository it can fetch
#: from holds them.
NO_REMOTE = "no remote of this clone holds its bytes"


def populate(directory: Path) -> None:
    """Give every annexed file whose bytes this clone holds those bytes.

    A file git checks out is a stand-in until git-annex fills it in from
    its annex: its hooks do after a checkout or a merge, but ``git reset
    --hard`` and ``git stash`` leave a stand-in even then, and nothing
    does where ``core.hooksPath`` sends git's hooks elsewhere (measured).
    ASTRA's checker reads such a file as bytes this clone lacks, and lc
    would send whoever reads the status to fetch what is already here.
    This is what those hooks run: nothing is transferred, and nothing git
    compares changes.

    Args:
        directory: The repository root, where git-annex is set up.

    Raises:
        ProjectError: If git-annex cannot do it.
    """
    _git(["annex", "smudge", "--update"], cwd=directory)


def enable_store(directory: Path) -> None:
    """Enable each store the project declares that this clone has not yet.

    git-annex enables a declared store when it is first set up in a clone,
    and not afterwards: one a ``git pull`` brought stays unknown to this
    clone, and what it holds reads as held nowhere. In a clone already set
    up, git-annex enables it only when forced, since it cannot tell
    whether another of its processes is running (measured). Forced, the
    store comes up exactly as in a fresh clone: read from freely, and
    written to by nothing until this clone confirms it
    (``annex-verified = automatic``, measured), which only ``lc publish``
    does, and only when told to. A store the clone already has is left as
    it stands, confirmed or not (measured).

    Forced only where a declaration waits, under whatever name. A store
    marked dead stays declared and is enabled by nothing (measured), so
    it is not waited for.

    Naming the store to ``git annex enableremote`` instead leaves it
    unreadable until confirmed (measured).

    Args:
        directory: The repository root, where git-annex is set up.
    """
    here = _configured(directory)
    waiting = {
        settings["uuid"] for name, settings in _declared(directory).items() if name not in here
    }
    if not waiting:
        return
    dead = {
        uuid for uuid, fields in _annex_log(directory, "trust.log").items() if fields[:1] == ["X"]
    }
    if waiting - dead:
        project._run(["git", "annex", "init", "--autoenable", "--force"], cwd=directory)


def holders(directory: Path, paths: Sequence[str]) -> dict[str, tuple[str, ...]]:
    """Name where this clone could fetch each file's bytes from.

    git-annex's answer, from its log of which repository holds what: kept
    in git, and read without the network. ``whereis`` first merges into
    the log what each remote's ``git-annex`` branch brought, a commit on
    that branch alone that leaves HEAD and the working tree as they were
    (measured); without it, the copies a ``git pull`` told of go unseen,
    and a result a remote holds would be taken for one none does.

    Only a repository this clone has as a remote counts. The log also
    names repositories it cannot reach, the one a colleague made a result
    in once they push the log but not the bytes, and ``git annex get``
    fails on those (measured).

    The log says what was last heard of each remote: one may have lost a
    copy since, which :func:`get` finds out. A copy in this clone's own
    annex is not counted: :func:`populate` has given its bytes to every
    file of it.

    Args:
        directory: The repository root.
        paths: Annexed files, from the repository root.

    Returns:
        For each file git-annex answers for, the remotes that hold its
        bytes, by name and in name order; empty where none does.
    """
    if not paths:
        return {}
    asked = project._run(
        ["git", "annex", "whereis", "--json", "--batch"],
        cwd=directory,
        stdin="".join(f"{path}\n" for path in paths),
    )
    # Read after asking, which is what merges in a declaration a pull brought.
    remotes = _remotes(directory)
    held = {}
    # One line per file asked, and an empty one for a file git-annex keeps
    # nothing of.
    for line in asked.stdout.splitlines():
        if not line.strip():
            continue
        answer = json.loads(line)
        found = [*answer.get("whereis", []), *answer.get("untrusted", [])]
        # By name: git-annex lists copies by repository id, which says nothing.
        held[answer["file"]] = tuple(
            sorted({remotes[copy["uuid"]] for copy in found if copy["uuid"] in remotes})
        )
    return held


def get(directory: Path, paths: Sequence[str]) -> dict[str, str]:
    """Fetch each file's bytes into this clone, from wherever git-annex finds them.

    ``git annex get`` says the same of a remote it cannot reach and of one
    that no longer holds what the log says it does, and leaves the log as
    it was (measured): every later fetch would try that remote again. So
    each remote the log names for a file not fetched is asked whether it
    holds it (``fsck --from --fast``), which corrects the log where it
    does not; the file is then one :data:`NO_REMOTE` holds, and whatever
    reads the log next says so. A remote that cannot be reached is left
    as the log has it.

    Args:
        directory: The repository root.
        paths: Annexed files, from the repository root.

    Returns:
        Why each file that was not fetched was not, by path.
    """
    if not paths:
        return {}
    reach = reaching(directory)
    got = project._run(
        ["git", *reach, "annex", "get", "--json", "--batch"],
        cwd=directory,
        stdin="".join(f"{path}\n" for path in paths),
    )
    failed = {}
    # One line per file asked, empty for one whose bytes were already here.
    for line in got.stdout.splitlines():
        if line.strip() and not (answer := json.loads(line)).get("success"):
            failed[answer["file"]] = answer.get("note") or "; ".join(answer["error-messages"])
    if got.returncode != 0 and not failed:
        # git-annex refused before it reached any file.
        return dict.fromkeys(paths, got.stderr.strip())
    named = holders(directory, list(failed))
    for remote in sorted({name for names in named.values() for name in names}):
        there = [path for path in failed if remote in named.get(path, ())]
        # `fsck` takes its files as arguments only, and a command line
        # holds only so many.
        for start in range(0, len(there), _MANY):
            project._run(
                ["git", *reach, "annex", "fsck", "--fast", "--quiet", "--from", remote, "--",
                 *there[start : start + _MANY]],
                cwd=directory,
            )  # fmt: skip
    now = holders(directory, list(failed))
    return {path: NO_REMOTE if now.get(path) == () else why for path, why in failed.items()}


# =============================================================================
# Where the bytes go
# =============================================================================

#: The name lc declares a project's data store under. A store is declared
#: once, in git-annex's branch, as a special remote git-annex enables by
#: itself: no clone configures it, and DataLad finds it the same way. The
#: name is lc's choice and no part of what makes a remote a store
#: (:func:`stores`).
STORE = "origin-storage"

#: The name lc declares a second remote under, for a store in a
#: repository's Git LFS: the same store, at the repository's ssh address.
STORE_SSH = "origin-storage-ssh"

#: What git-annex records of a store it enabled that this clone has not
#: confirmed: enabled by itself, or by name. It sends nothing to either.
_UNCONFIRMED = ("automatic", "manual")

#: The stores lc publishes a project's data to, by git-annex's type for
#: each → what lc calls it.
_KINDS: dict[str, Literal["github-lfs", "directory"]] = {
    "git-lfs": "github-lfs",
    "git": "directory",
}


@dataclass(frozen=True)
class Store:
    """A store the project declares for its bytes, as this clone has it."""

    #: A repository's Git LFS storage, or a git-annex repository in a
    #: directory: the kinds lc publishes to. None for a store of any
    #: other, a bucket say.
    kind: Literal["github-lfs", "directory"] | None
    #: Where it is: the repository's address, or the directory's, a path
    #: or an ssh URL. Empty for a store that is no git remote, which lc
    #: publishes nothing to.
    location: str
    #: git-annex's id for it, which its log places bytes by.
    uuid: str
    #: Each remote that reaches it from this clone, the one it was
    #: declared as first → its address there, and whether this clone may
    #: send to it: it declared the store, or confirmed it since.
    remotes: dict[str, tuple[str, bool]]

    def remote(self, *, ssh: bool) -> str:
        """Name the remote a clone sends through.

        For Git LFS declared at two addresses, the one of the kind the
        clone's ``origin`` uses, which is the one its credentials are for.

        Args:
            ssh: Whether the clone reaches its ``origin`` over ssh.

        Returns:
            That remote, or the one the store was declared as where no
            address is of that kind.
        """
        return next(
            (
                name
                for name, (address, _) in self.remotes.items()
                if (there := github.parse(address)) is not None and there.ssh == ssh
            ),
            next(iter(self.remotes)),
        )


def stores(directory: Path) -> list[Store]:
    """Read each store the project declares, as this clone has it.

    What makes a remote a store is its declaration in git-annex's branch,
    as one git-annex enables by itself. Its name is no part of that, nor
    its kind: lc declares a store as :data:`STORE`, in Git LFS or in a
    directory, and one declared by hand or by another tool, under another
    name or of another kind, is the project's store all the same. A
    project is meant to declare one; what to make of several, or of a
    kind lc publishes nothing to, is the caller's.

    Where a store is, and whether this clone may send to it, come from
    the clone's own configuration, which is what git-annex acts on: a
    change to a declaration that a pull brings takes effect only once
    confirmed.

    Args:
        directory: The repository root.

    Returns:
        Each declared store this clone has enabled (:func:`enable_store`),
        by id.
    """
    here = _configured(directory)
    kinds: dict[str, Literal["github-lfs", "directory"] | None] = {}
    reached: dict[str, dict[str, tuple[str, bool]]] = {}
    # A store's own declaration before another address of it.
    for name, settings in sorted(
        _declared(directory).items(), key=lambda item: ("sameas-uuid" in item[1], item[0])
    ):
        if name not in here:
            continue
        kinds.setdefault(settings["uuid"], _KINDS.get(settings.get("type", "")))
        reached.setdefault(settings["uuid"], {})[name] = (
            here[name].get("url", ""),
            here[name].get("annex-verified") not in _UNCONFIRMED,
        )
    return [
        Store(kinds[uuid], next(iter(remotes.values()))[0], uuid, remotes)
        for uuid, remotes in sorted(reached.items())
    ]


def unsettled(directory: Path) -> dict[str, str]:
    """Name what this clone has yet to note of where the project is published.

    Two settings a clone keeps, for an ``origin`` that is a repository on
    GitHub. GitHub holds no bytes for git-annex, so ``origin`` is marked
    as a remote git-annex leaves alone, as DataLad marks a sibling on
    GitHub: git-annex otherwise finds that out by asking GitHub. And
    DataLad sends bytes to the store before it pushes there, as ``lc
    publish`` does, through ``datalad-publish-depends``. Neither travels
    with the repository, so each clone writes its own.

    Args:
        directory: The repository root.

    Returns:
        Each setting this clone lacks → the value it takes; empty where
        ``origin`` is not on GitHub.
    """
    published_to = target(directory)
    if published_to is None:
        return {}
    wanted = {"remote.origin.annex-ignore": "true"}
    declared = stores(directory)
    if len(declared) == 1:
        wanted["remote.origin.datalad-publish-depends"] = declared[0].remote(ssh=published_to.ssh)
    return {
        key: value
        for key, value in wanted.items()
        if (_ask(["config", "--local", "--get", key], cwd=directory) or "").strip() != value
    }


def settle(directory: Path) -> None:
    """Write what :func:`unsettled` names into this clone's configuration.

    Args:
        directory: The repository root.
    """
    for key, value in unsettled(directory).items():
        _git(["config", key, value], cwd=directory)


def confirm(directory: Path, remote: str, declared: dict[str, str]) -> None:
    """Record that this clone may send to a store someone else declared.

    A store's declaration is data from whoever can push to the
    repository, and git-annex sends nothing to one this clone has not
    confirmed. It shows the declaration and takes the code it gives with
    it as the confirmation, so what is confirmed is what was shown. lc
    confirms only a declaration that is, setting for setting, the one it
    would write for the place the user named: anything more is not lc's
    to vouch for.

    Args:
        directory: The repository root.
        remote: The remote to confirm.
        declared: The settings lc declares for the place the user named.

    Raises:
        ProjectError: If git-annex shows another declaration, with
            nothing confirmed.
    """
    shown = _git(["annex", "verifyremote", remote], cwd=directory)
    code = re.search(r"--safe (\S+)", shown)
    if code is None:
        # Nothing left to confirm: git-annex shows a code only with a
        # declaration this clone has not confirmed.
        return
    settings = next((line.strip() for line in shown.splitlines() if line.startswith("\t")), "")
    if dict(pair.split("=", 1) for pair in settings.split() if "=" in pair) != declared:
        raise project.ProjectError(
            f"what the project declares for `{remote}` is not what lc declares for that "
            f"place, so lc does not confirm it:\n  {settings}\nNothing was sent."
        )
    _git(["annex", "verifyremote", remote, "--safe", code[1]], cwd=directory)


def annexed(directory: Path, commit: str) -> dict[str, str]:
    """List the files of *commit* that git-annex keeps the bytes of.

    Asked of the commit, not of the working tree: what is published is
    what the commit names, whatever has been edited since.

    Args:
        directory: The repository root.
        commit: The commit.

    Returns:
        Each such file, from the top of the repository → the key
        git-annex keeps its bytes under.
    """
    listed = _git(["annex", "find", f"--branch={commit}", "--anything", "--json"], cwd=directory)
    return {
        entry["file"]: entry["key"]
        for entry in map(json.loads, filter(None, listed.splitlines()))
    }


def keys(directory: Path, files: Sequence[str]) -> dict[str, str]:
    """Look up the key of files at other commits.

    Args:
        directory: The repository root.
        files: Each as ``<commit>:<path from the top of the repository>``.

    Returns:
        Each file git-annex keeps → its key. One git keeps itself, or
        that the commit does not hold, is left out.
    """
    if not files:
        return {}
    looked = project._run(
        ["git", "annex", "lookupkey", "--ref", "--batch"],
        cwd=directory,
        stdin="".join(f"{name}\n" for name in files),
    )
    # One line out per file in: its key, or empty.
    return {
        name: key
        for name, key in zip(files, looked.stdout.split("\n"), strict=False)
        if key.strip()
    }


def copies(directory: Path, wanted: Iterable[str]) -> dict[str, tuple[bool, frozenset[str]]]:
    """Say where git-annex's log puts the bytes kept under each key.

    The log, read without the network, with what each remote's
    ``git-annex`` branch brought merged in first, as :func:`holders`
    reads it.

    Args:
        directory: The repository root.
        wanted: Keys.

    Returns:
        Each key → whether this clone holds its bytes, and the id of each
        other repository the log says does.
    """
    asked = project._run(
        ["git", "annex", "whereis", "--json", "--batch-keys"],
        cwd=directory,
        stdin="".join(f"{key}\n" for key in wanted),
    )
    held = {}
    for line in asked.stdout.splitlines():
        if not line.strip():
            continue
        answer = json.loads(line)
        found = [*answer.get("whereis", []), *answer.get("untrusted", [])]
        held[answer["key"]] = (
            any(copy["here"] for copy in found),
            frozenset(copy["uuid"] for copy in found if not copy["here"]),
        )
    return held


def send(directory: Path, remote: str, wanted: Sequence[str]) -> dict[str, str]:
    """Send the bytes kept under each key to *remote*, a store.

    An explicit copy of exactly these, never ``git annex push`` or ``git
    annex sync``: those push ``synced/`` branches, merge, and may drop
    what a preferred-content setting does not want kept (measured).
    ``--fast`` takes the log's word that the store lacks them, where
    git-annex would otherwise ask the store about each before sending it.

    Only a key git-annex answers for as sent was sent. For one whose
    bytes its log places in this clone and its annex does not hold, it
    answers with an empty line and exits 0 (measured).

    Args:
        directory: The repository root.
        remote: The store's remote.
        wanted: Keys whose bytes this clone holds.

    Returns:
        Why the bytes of each key that was not sent were not, by key.
    """
    if not wanted:
        return {}
    sent = project._run(
        ["git", *reaching(directory), "annex", "copy", "--to", remote, "--fast", "--json",
         "--json-error-messages", "--batch-keys"],
        cwd=directory,
        stdin="".join(f"{key}\n" for key in wanted),
    )  # fmt: skip
    answers = {
        answer["key"]: answer
        for answer in map(json.loads, filter(str.strip, sent.stdout.splitlines()))
    }
    unanswered = (
        # git-annex refused before it reached any of them.
        (sent.stderr or "").strip()
        if sent.returncode != 0 and not answers
        else "git-annex sent nothing for it: its bytes are not in this clone's annex"
    )
    return {
        key: "; ".join(answers.get(key, {}).get("error-messages") or []) or unanswered
        for key in wanted
        if not answers.get(key, {}).get("success")
    }


def record_copies(directory: Path) -> None:
    """Commit to git-annex's branch what it has noted of where bytes are.

    git-annex keeps that in its journal until one of its own commands
    commits it, and merges into its branch what each remote's
    ``git-annex`` branch brought (measured): both have to be in the
    branch before it is pushed. A sync of that branch alone, with no
    remote and no content; never ``git annex merge``, which merges a
    pending ``synced/`` branch into the one checked out (measured).

    Args:
        directory: The repository root.
    """
    _git(
        ["annex", "sync", "-q", "--only-annex", "--no-pull", "--no-push", "--no-content",
         "--no-commit"],
        cwd=directory,
    )  # fmt: skip


# =============================================================================
# Reaching a remote
# =============================================================================

#: A git credential helper that answers for github.com with the token in
#: the environment. It names the variables and never holds the token, so
#: no command line carries one.
_TOKEN_HELPER = (
    '!f() { test "$1" = get && echo username=x-access-token && '
    'echo "password=${GH_TOKEN:-$GITHUB_TOKEN}"; }; f'
)


def remote_url(directory: Path, name: str) -> str:
    """Read the address of remote *name*.

    Args:
        directory: The repository root.
        name: The remote.

    Returns:
        Its URL as configured, or empty where this clone has no such
        remote.
    """
    return (_ask(["config", "--get", f"remote.{name}.url"], cwd=directory) or "").strip()


def target(directory: Path) -> github.Repository | None:
    """Name the repository on GitHub this clone publishes to.

    Its ``origin``, where that is one: a project with none is not online.

    Args:
        directory: The repository root.

    Returns:
        The repository, or None.
    """
    return github.parse(remote_url(directory, "origin"))


def ssh(directory: Path) -> str:
    """Give the ssh command to reach a remote with, which never waits on anyone.

    The user's own, ``GIT_SSH_COMMAND`` or else the repository's
    ``core.sshCommand``, in batch mode: a missing key or an unknown host
    is a refusal, never a hang.

    Args:
        directory: The repository root.

    Returns:
        The command, as a shell would take it.
    """
    own = os.environ.get("GIT_SSH_COMMAND") or (
        _ask(["config", "--get", "core.sshCommand"], cwd=directory) or ""
    )
    return f"{own.strip() or 'ssh'} -o BatchMode=yes"


def reaching(directory: Path) -> list[str]:
    """Give git's options for a command that reaches a remote.

    Nothing lc runs waits on anyone: git never prompts
    (:func:`~lightcone.engine.project.child_env`), and ssh runs in batch
    mode (:func:`ssh`). git takes its ssh command from ``core.sshCommand``
    and git-annex its ssh options from ``annex.ssh-options``, each
    ignoring the other's (measured), so both are given. git itself takes
    a ``GIT_SSH_COMMAND`` from the environment as it is, over either.

    A GitHub token in the environment is offered to git for github.com
    alone, after the helpers git already has, and so is ``gh``'s where it
    is installed: lc stores no credential, and writes none into the
    repository. git passes these options on to git-annex.

    Args:
        directory: The repository root.

    Returns:
        Options to put between ``git`` and its command.
    """
    more = (_ask(["config", "--get", "annex.ssh-options"], cwd=directory) or "").strip()
    options = [
        "-c", f"core.sshCommand={ssh(directory)}",
        "-c", "annex.ssh-options=" + " ".join(filter(None, (more, "-o BatchMode=yes"))),
    ]  # fmt: skip
    if os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"):
        options += ["-c", f"credential.https://github.com.helper={_TOKEN_HELPER}"]
    elif github.has_gh():
        options += ["-c", "credential.https://github.com.helper=!gh auth git-credential"]
    return options


def _remotes(directory: Path) -> dict[str, str]:
    """Each repository this clone can fetch from, by git-annex's id for it
    → the remote's name. One reached at two addresses is named by the
    first.

    git-annex's log places bytes in repositories by id. For a store in a
    directory, git-annex notes that id in a clone's configuration only
    once it has looked there (measured): until then the store's
    declaration gives it, or what the log places in the store would read
    as bytes no remote of the clone holds."""
    here, declared = _configured(directory), _declared(directory)
    remotes: dict[str, str] = {}
    for name in sorted(here):
        if uuid := here[name].get("annex-uuid") or declared.get(name, {}).get("uuid"):
            remotes.setdefault(uuid, name)
    return remotes


def _configured(directory: Path) -> dict[str, dict[str, str]]:
    """Each remote of this clone, by name → what its configuration says of
    it: its ``url``, git-annex's id for what it reaches (``annex-uuid``),
    and where confirming it stands (``annex-verified``)."""
    listed = _ask(
        ["config", "--get-regexp", r"^remote\..+\.(url|annex-uuid|annex-verified)$"],
        cwd=directory,
    )
    found: dict[str, dict[str, str]] = {}
    for line in (listed or "").splitlines():
        key, _, value = line.partition(" ")
        name, _, setting = key.removeprefix("remote.").rpartition(".")
        found.setdefault(name, {})[setting] = value
    return found


def _declared(directory: Path) -> dict[str, dict[str, str]]:
    """Each remote the project declares for git-annex to enable by itself,
    by name → its declaration, with ``uuid``, the id of the repository it
    reaches.

    One line of ``remote.log`` each. A second address of a store is a line
    of its own, which names the store by ``sameas-uuid`` and itself by
    ``sameas-name`` (measured)."""
    declared: dict[str, dict[str, str]] = {}
    for uuid, fields in _annex_log(directory, "remote.log").items():
        settings = dict(field.split("=", 1) for field in fields if "=" in field)
        name = settings.get("sameas-name") or settings.get("name")
        if name and settings.get("autoenable") == "true":
            declared[name] = {**settings, "uuid": settings.get("sameas-uuid", uuid)}
    return declared


def _annex_log(directory: Path, log: str) -> dict[str, list[str]]:
    """Read one of git-annex's logs of repositories: each id it names →
    the fields of its newest line.

    From the clone's own ``git-annex`` branch and from each one a remote
    brought: git-annex merges those only when one of its commands runs
    (measured), and what a ``git pull`` just told of is read before that.
    A line is ``<id> <field>... timestamp=<seconds>s``, and where two name
    one id the newer holds, which is how git-annex merges them."""
    refs = _ask(
        ["for-each-ref", "--format=%(refname)", "refs/heads/git-annex",
         "refs/remotes/*/git-annex"],
        cwd=directory,
    )  # fmt: skip
    newest: dict[str, tuple[float, list[str]]] = {}
    for ref in (refs or "").split():
        for line in (_ask(["show", f"{ref}:{log}"], cwd=directory) or "").splitlines():
            uuid, *fields = line.split() or [""]
            stamps = [field for field in fields if field.startswith("timestamp=")]
            when = float(stamps[-1].removeprefix("timestamp=").rstrip("s")) if stamps else 0.0
            if uuid and (uuid not in newest or when >= newest[uuid][0]):
                newest[uuid] = (when, [field for field in fields if field not in stamps])
    return {uuid: fields for uuid, (_, fields) in newest.items()}


#: How many paths lc puts on one command line, far under what any system
#: takes. Whatever git or git-annex reads from standard input goes there.
_MANY = 1000


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
    :data:`_PATIENCE` seconds, git's own refusal is the answer: it names
    the lock and how to remove it.
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
