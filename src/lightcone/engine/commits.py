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
from pathlib import Path

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
    if (tree / ".git").exists() and not _intact(tree, commit):
        # lc's own copy, changed since it was made: made again, never trusted.
        for folder, _, _ in os.walk(tree):
            os.chmod(folder, 0o700)  # git-annex keeps its objects' folders read-only
        shutil.rmtree(tree)
    if not (tree / ".git").exists():
        _clone(root, commit, repository, tree, reach)
    if repository is None:
        # The project's own commit: what git-annex keeps is fetched into the
        # project, from wherever the project fetches its own content, and
        # the clone takes it from the project.
        keys = dataset.keys(root, [f"{commit}:{path}" for path in paths])
        if keys:
            fetched = project._run(
                ["git", *reach, "annex", "get", "--batch-keys"],
                cwd=root,
                stdin="".join(f"{key}\n" for key in keys.values()),
            )
            if fetched.returncode != 0:
                raise ProjectError(
                    f"what {commit} holds could not be fetched into the project:\n"
                    f"{fetched.stderr.strip()}"
                )
        dataset._git(["annex", "get", "-q", "--from", "origin", "--", *paths], cwd=tree)
    else:
        dataset._git([*reach, "annex", "get", "-q", "--", *paths], cwd=tree)


def _intact(tree: Path, commit: str) -> bool:
    """Whether a commit's tree is still that commit's: checked out at it, and
    no file git tracks there changed. One git cannot even read is not."""
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
    making = tree.with_name(f"{commit}.making")
    shutil.rmtree(making, ignore_errors=True)
    making.parent.mkdir(parents=True, exist_ok=True)
    dataset._git([*reach, "clone", "-q", "--no-checkout", "--", source, str(making)], cwd=root)
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
    making.rename(tree)
