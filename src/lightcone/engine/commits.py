"""The trees of the other commits a run's recipes read files at.

ASTRA names a file of an analysis declared with a ``commit`` at its path in
that commit's tree, under ``<COMMITS>/<commit id>/`` (:data:`plan.COMMITS`).
A run gives it there as anyone gets a commit: a clone of the repository
that holds it — the URL its analysis is declared with, or the project's
own — checked out at it, with what git-annex keeps fetched for each file a
recipe reads. The project's own tree is not touched, and the folder is
ignored and kept: a commit's tree is the same tree from run to run.

Only a run gives a tree, and the rerun entry point, which is one: read-only
verbs fetch nothing. The folder is lc's own, so git-annex runs in it as in
any clone, under the project's committer identity, which its first fetch
commits with.
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from lightcone.engine import dataset, project
from lightcone.engine.plan import COMMITS
from lightcone.engine.project import ProjectError


def give(root: Path, commit: str, repository: str | None, paths: Sequence[str]) -> None:
    """Give a run *paths* as *commit* has them, in that commit's tree.

    Args:
        root: The project root.
        commit: The commit's id.
        repository: The URL of the repository that holds it, or None for
            the project's own.
        paths: Files a recipe reads, from the top of that commit's tree.

    Raises:
        ProjectError: If the commit, or the content of a file, cannot be
            had, in git's or git-annex's own words.
    """
    tree = root / COMMITS / commit
    # The tree's remotes are reached as the project's own are.
    reach = dataset.reaching(root)
    if not _intact(tree, commit):
        _clone(root, commit, repository, tree, reach)
    if repository is None:
        # The project's own commit: what git-annex keeps is fetched into the
        # project, from wherever the project fetches its own content, and
        # the clone takes it from the project.
        keys = dataset.keys(root, [f"{commit}:{path}" for path in paths])
        if keys:
            _fetch(
                ["git", *reach, "annex", "get", "--batch-keys"],
                root,
                f"what {commit} holds could not be fetched into the project",
                stdin="".join(f"{key}\n" for key in keys.values()),
            )
        fetch = ["git", "annex", "get", "-q", "--from", "origin", "--", *paths]
    else:
        fetch = ["git", *reach, "annex", "get", "-q", "--", *paths]
    _fetch(fetch, tree, f"what {commit} holds could not be fetched into its tree")


def _fetch(argv: list[str], cwd: Path, failure: str, stdin: str | None = None) -> None:
    """Run a git-annex fetch that has to succeed. Another run of the
    project may be fetching the same content into the same tree, and is
    waited for (`dataset.transfer`)."""
    fetched = dataset.transfer(argv, cwd=cwd, stdin=stdin)
    if fetched.returncode != 0:
        raise ProjectError(f"{failure}:\n{fetched.stderr.strip()}")


def _intact(tree: Path, commit: str) -> bool:
    """Whether a commit's tree is there and still that commit's: checked out
    at it, and no file git tracks there changed. One git cannot even read
    is not."""
    if not (tree / ".git").exists():
        return False
    at = project._run(["git", "rev-parse", "HEAD"], cwd=tree)
    changed = project._run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=tree)
    return (
        at.returncode == 0
        and changed.returncode == 0
        and at.stdout.strip() == commit
        and not changed.stdout.strip()
    )


def _clone(root: Path, commit: str, repository: str | None, tree: Path, reach: list[str]) -> None:
    """Clone the repository that holds *commit* into *tree*, checked out at it.

    Made beside *tree* and moved into place once whole, so a clone an
    interrupted run left half made is never taken for the commit's tree.
    Under a name of its own, since two runs may give one commit's tree at
    once: the first to finish gives it, and the other's is discarded. A
    tree that is no longer its commit's, lc's own copy changed since it
    was made, is never trusted: it is moved aside as the new one takes its
    place, so that a run reading the tree never finds none there.
    """
    if repository is None and not dataset._git_ok(
        ["cat-file", "-e", f"{commit}^{{commit}}"], cwd=root
    ):
        # astra-tools names a URL for another repository, and none for a
        # local one such as a submodule's checkout: nothing says which
        # repository holds this commit, and the project's does not.
        raise ProjectError(
            f"{commit} is not in the project's repository, and astra-tools names no other "
            "that holds it: a submodule's commit, which only its checkout holds"
        )
    source = repository if repository is not None else str(dataset.toplevel(root))
    making = tree.with_name(f"{commit}.{uuid4().hex[:8]}.making")
    replaced = tree.with_name(f"{commit}.{uuid4().hex[:8]}.replaced")
    making.parent.mkdir(parents=True, exist_ok=True)
    try:
        dataset._git(
            [*reach, "clone", "-q", "--no-checkout", "--", source, str(making)], cwd=root
        )
        # A clone holds what its source's branches reach; a commit no branch
        # reaches is asked for by its id.
        if not dataset._git_ok(["cat-file", "-e", f"{commit}^{{commit}}"], cwd=making):
            dataset._git([*reach, "fetch", "-q", "origin", commit], cwd=making)
        dataset._git(["checkout", "-q", "--detach", commit], cwd=making)
        # git-annex commits to its own branch at its first fetch here.
        ident = dataset._git(["var", "GIT_COMMITTER_IDENT"], cwd=root)
        if found := re.match(r"(.*) <(.*)>", ident):
            dataset._git(["config", "user.name", found[1]], cwd=making)
            dataset._git(["config", "user.email", found[2]], cwd=making)
        if tree.exists() and not _intact(tree, commit):
            tree.rename(replaced)
        # Refused where another run gave the tree meanwhile: theirs is it.
        with suppress(OSError):
            making.rename(tree)
        if not _intact(tree, commit):
            raise ProjectError(f"the tree of {commit} could not be put in place at {tree}")
    finally:
        # Whichever was not moved into place, and whatever it replaced.
        for left in (making, replaced):
            for folder, _, _ in os.walk(left):
                os.chmod(folder, 0o700)  # git-annex keeps its objects' folders read-only
            shutil.rmtree(left, ignore_errors=True)
