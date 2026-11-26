"""A pinned, private checkout shared by all tasks of one execution.

The driver alone prepares it and talks to git-annex. Annex filters and hooks
are disabled in linked worktrees: otherwise git-annex populates them by
copy and rewrites their .git file. Exact keys come from the pinned commit;
whereis supplies known holders and contentlocation supplies real filenames.
Only requested objects are fetched, once per key. Declared paths are symlinks
to these objects, granted individually by the execution boundary.

Fresh results stay here until every submitted task reports. Workers move
completed payloads outside writable output directories and leave symlinks
for their readers, so a downstream recipe cannot write through an input.
Collection then renames payloads into the researcher's tree, with per-result
commits in graph order. An optimistic check refuses changed destinations;
a short directory lock serializes collectors, not execution. A crashed
collector's lock must be removed only after its driver has stopped.

Successful runs remove their worktrees. Interrupted or uncollected runs are
kept, locked in git, since an unreported worker may still be writing. We do
not guess liveness from a PID, elapsed time, or a reused allocation name.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from lightcone.engine import content, dataset, plan, project
from lightcone.engine.project import ProjectError

RUNS = ".lightcone/runs"


@dataclass
class Stage:
    """One execution's tree and the repository it will collect into."""

    project: Path
    top: Path
    root: Path
    commit: str
    branch: str
    trees: list[tuple[Path, Path]] = field(default_factory=list)

    def give(self, commit: str, repository: str | None, paths: Sequence[str]) -> None:
        """Stage declared files from another commit at ASTRA's given paths.

        Args:
            commit: The exact commit to read.
            repository: Its repository URL, or None for this project's.
            paths: Files measured from that repository's top.
        """
        from lightcone.engine import commits

        source = commits.repository(self.project, commit, repository)
        destination = self.root / plan.COMMITS / commit
        _checkout(source, destination, commit)
        self.trees.append((source, destination))
        fill(source, destination, destination, commit, paths, fetch=True)

    def close(self) -> None:
        """Remove the checkout only once no task can still be using it."""
        for source, top in reversed(self.trees):
            dataset._git([*dataset.PLAIN, "worktree", "remove", "--force", "--force", str(top)],
                         cwd=source)
        dataset._git([*dataset.PLAIN, "worktree", "remove", "--force", "--force", str(self.top)],
                     cwd=self.project)


def _checkout(source: Path, top: Path, commit: str) -> None:
    top.parent.mkdir(parents=True, exist_ok=True)
    dataset._git(
        [*dataset.PLAIN, "worktree", "add", "-q", "--detach", "--lock", str(top), commit],
        cwd=source,
    )


def take(root: Path, commit: str) -> Stage:
    """Make one detached checkout, preserving a project nested in its repository.

    Args:
        root: The researcher's project root.
        commit: The commit every recipe and record of this run uses.

    Returns:
        The private checkout. The caller owns its lifetime.
    """
    if dataset.ignore_rule(root, RUNS + "/") is None:
        raise ProjectError(".lightcone/runs/ must be ignored before execution; run `lc init`")
    base = root / RUNS
    base.mkdir(parents=True, exist_ok=True)
    # Stop uv's config discovery before it reaches the researcher's live tree.
    (base / "uv.toml").touch()
    top = base / uuid4().hex
    branch = dataset._git(["rev-parse", "--symbolic-full-name", "HEAD"], cwd=root).strip()
    _checkout(root, top, commit)
    prefix = dataset._git(["rev-parse", "--show-prefix"], cwd=root).strip()
    return Stage(root, top, top / prefix, commit, branch)


def fill(
    source: Path, top: Path, root: Path, commit: str, paths: Sequence[str], *, fetch: bool,
) -> list[str]:
    """Expose selected content using pinned annex keys and read-only symlink targets.

    Args:
        source: Repository owning the objects, never a linked execution tree.
        top: Top of the detached checkout.
        root: Project root inside that checkout.
        commit: Its pinned commit.
        paths: Declared files to make readable, relative to root. A probe can
            name every annexed file with fetch=False to expose local content.
        fetch: Fetch absent selected objects; probes leave absent files absent.

    Returns:
        Fetch failures, for the driver to report while independent tasks run.
    """
    prefix = root.relative_to(top).as_posix()
    prefix = "" if prefix == "." else prefix + "/"
    annexed = {
        path.removeprefix(prefix): key
        for path, key in dataset.annexed(source, commit).items()
        if path.startswith(prefix)
    } if dataset.is_annexed(source) else {}
    wanted = set(paths)
    keys = sorted({annexed[path] for path in wanted & annexed.keys()})
    held = dataset.copies(source, keys) if keys else {}
    # The log names holders; only contentlocation names the actual local file.
    locations = dataset.locations(source, keys)
    warnings = []
    if fetch and (absent := [key for key in keys if key not in locations]):
        # Let get ask its configured remotes even if a log is out of date.
        got = project._run(
            ["git", *dataset.reaching(source), "annex", "get", "--json", "--batch-keys"],
            cwd=source, stdin="".join(f"{key}\n" for key in absent),
        )
        locations.update(dataset.locations(source, absent))
        for key in absent:
            if key not in locations:
                why = got.stderr.strip() or (
                    dataset.NO_REMOTE if not any(held.get(key, (False, frozenset())))
                    else "the known copies could not be fetched"
                )
                named = ", ".join(sorted(path for path in wanted if annexed.get(path) == key))
                warnings.append(f"{named}: could not be fetched — {why}")
    for path, key in annexed.items():
        here = root / path
        here.unlink(missing_ok=True)
        if path in wanted and key in locations:
            here.symlink_to(locations[key])
    # Files another tool keeps are never mistaken for their pointers. A fetched
    # LFS file is admitted only if it matches the oid in the pinned pointer.
    source_top = dataset.toplevel(source)
    for name in wanted - annexed.keys():
        here = root / name
        there = source_top / prefix / name
        if content.standin(here) == "Git LFS":
            oid = next((s.removeprefix("oid ") for s in here.read_text().splitlines()
                        if s.startswith("oid ")), "")
            if there.is_file() and content.digest_file(there)[0] == oid:
                here.unlink()
                here.symlink_to(there.resolve())
        elif not os.path.lexists(here) and there.is_file():
            here.parent.mkdir(parents=True, exist_ok=True)
            here.symlink_to(there.resolve())
    # Build backends may ask git for a version. These link substitutions are
    # execution plumbing, not edits to the commit they should describe.
    tracked = dataset._git([*dataset.PLAIN, "ls-files", "-z"], cwd=root).split("\0")
    masked = sorted((wanted | annexed.keys()) & set(tracked))
    if masked:
        marked = project._run(
            ["git", *dataset.PLAIN, "update-index", "--assume-unchanged", "-z", "--stdin"],
            cwd=root, stdin="".join(f"{path}\0" for path in masked),
        )
        if marked.returncode != 0:
            raise ProjectError(f"cannot prepare the checkout's index: {marked.stderr.strip()}")
    return warnings


def seal(task: plan.Task) -> None:
    """Keep a completed payload outside every recipe's writable result directory.

    Args:
        task: The completed result in the run tree, already hashed and recorded.
    """
    payload = task.root / ".lightcone/payloads" / uuid4().hex
    payload.parent.mkdir(parents=True, exist_ok=True)
    task.path.rename(payload)
    for path in task.paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        path.symlink_to(payload)


@contextmanager
def collecting(stage: Stage, paths: Sequence[Path]) -> Iterator[None]:
    """Serialize collection and refuse destinations changed since this run started.

    Args:
        stage: The execution to collect.
        paths: Destination paths, in the researcher's tree.

    Yields:
        With collection reserved and all destinations checked.

    Raises:
        ProjectError: A competing collector, changed history, or changed destination.
    """
    folder = Path(dataset._git(["rev-parse", "--absolute-git-dir"], cwd=stage.project).strip())
    lock = folder / "lc-collecting"
    deadline = time.monotonic() + 60
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise ProjectError(
                    f"another collector holds {lock}; results remain in {stage.root}. "
                    "If its driver has stopped, remove that empty lock directory and retry."
                ) from None
            time.sleep(0.1)
    try:
        branch = dataset._git(["rev-parse", "--symbolic-full-name", "HEAD"],
                              cwd=stage.project).strip()
        if branch != stage.branch or not dataset._git_ok(
            ["merge-base", "--is-ancestor", stage.commit, "HEAD"], cwd=stage.project,
        ):
            raise ProjectError("the project's branch changed or no longer holds the run's commit")
        if any((folder / name).exists() for name in (
            "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply",
        )):
            raise ProjectError("finish the merge, rebase or cherry-pick before collecting results")
        canonical = stage.project.resolve()
        if any(path.parent.resolve() != canonical / path.parent.relative_to(stage.project)
               for path in paths):
            raise ProjectError("a result destination has a symlinked parent; nothing was collected")
        names = {path.relative_to(stage.project).as_posix() for path in paths}
        changed = {path for _, path in dataset.status(stage.project, untracked_files=True)}
        changed.update(dataset._git(
            ["diff", "--relative", "--name-only", "-z", stage.commit, "HEAD", "--", "."],
            cwd=stage.project,
        ).split("\0"))
        if conflicts := sorted(names & changed):
            raise ProjectError(
                "result destinations changed during the run: " + ", ".join(conflicts)
            )
        yield
    finally:
        lock.rmdir()


@contextmanager
def collect(task: plan.Task, destination: plan.Task) -> Iterator[None]:
    """Move finished bytes for a save, returning them to staging if it fails.

    Args:
        task: The completed result in the run checkout.
        destination: The same task rooted in the researcher's tree.

    Yields:
        With the payload and records at their destinations, ready to commit.
    """
    moved: list[tuple[Path, Path]] = []
    aliases: list[Path] = []
    try:
        for source, target in zip((*task.paths, *task.record_paths),
                                  (*destination.paths, *destination.record_paths), strict=True):
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = source.resolve() if source in task.paths else source
            if source in task.paths and not payload.exists():
                # Several ASTRA places name one payload, moved at its first place.
                target.unlink(missing_ok=True)
                os.link(destination.path, target)
                aliases.append(target)
            else:
                payload.replace(target)
                moved.append((payload, target))
        yield
    except BaseException:
        for alias in aliases:
            alias.unlink(missing_ok=True)
        for source, target in reversed(moved):
            if target.is_file() and not target.is_symlink():
                target.replace(source)
        dataset.restore(destination.root, [
            f":(literal){path.relative_to(destination.root).as_posix()}"
            for path in (*destination.paths, *destination.record_paths)
        ])
        raise


def probe_files(stage: Stage) -> list[Path]:
    """Expose local files and uncommitted work for a development probe, without downloads.

    Args:
        stage: The pinned base checkout.

    Returns:
        Exact file targets the probe needs granted outside the checkout.
    """
    prefix = stage.root.relative_to(stage.top).as_posix()
    prefix = "" if prefix == "." else prefix + "/"
    local = [path.removeprefix(prefix)
             for path in dataset.annexed(stage.project, stage.commit)
             if path.startswith(prefix)] if dataset.is_annexed(stage.project) else []
    # Keep locally fetched inputs of other tools available too. A probe must
    # still work while its analysis is missing or being repaired.
    from astra import api

    with suppress(ProjectError):
        with plan.asking_astra(stage.project):
            analysis = api.resolve(stage.project, offline=True)
        local.extend(declared.path for each in analysis.analyses if each.commit is None
                     for declared in each.inputs)
    fill(stage.project, stage.top, stage.root, stage.commit, local, fetch=False)
    # Probes intentionally see edits. Links avoid copying large uncommitted data;
    # their targets are readable, never writable, under the normal boundary.
    for _, name in dataset.status(stage.project, untracked_files=True):
        source, target = stage.project / name, stage.root / name
        if target.is_file() or target.is_symlink():
            target.unlink()
        if source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source.resolve())
    (stage.root / "results").mkdir(exist_ok=True)
    return [path.resolve() for path in stage.root.rglob("*")
            if path.is_symlink() and path.is_file()]
