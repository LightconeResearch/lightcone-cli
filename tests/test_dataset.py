"""Tests for `lightcone.engine.dataset` — how a project stores what it made.

Most of this file runs against a **real** git + git-annex repository, via
the `real_tools` fixture. That is deliberate and it is the exception in
this suite: the question these tests ask is whether bytes land in the
annex or as a blob in git, and a fake that answered it would only be
restating what the code already believes.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from lightcone.engine import content, dataset, project, templates


@pytest.fixture
def repo(tmp_path: Path, real_tools: None) -> Path:
    """A converged-enough dataset: a repository, an annex, a storage policy.

    Identity is set locally rather than relied on from the host, because
    `git annex init` and every `save` make a commit.
    """
    root = tmp_path / "demo"
    (root / "results").mkdir(parents=True)
    (root / "data").mkdir()
    (root / ".gitattributes").write_text(templates.read("gitattributes.tmpl"))
    dataset.init_git(root)
    for key, value in (("user.email", "t@example.com"), ("user.name", "Test")):
        dataset._git(["config", key, value], cwd=root)
    dataset.init_annex(root)
    dataset.save(root, [root], "scaffold")
    return root


def _rebuild(output: Path) -> None:
    """What a worker does before it runs a recipe: the recipe owns the
    directory, and a stale file must not survive into the next hash.

    With `filter=annex` an annexed file is writable, so this is about
    what a rebuild *means* rather than about permissions.
    """
    shutil.rmtree(output, ignore_errors=True)
    output.mkdir(parents=True)


def _annexed(repo: Path, path: Path) -> bool:
    """Whether git-annex holds the bytes rather than git.

    Asked of git-annex, because `filter=annex` means an annexed file is an
    ordinary writable file in the tree with no symlink to look at.
    `lookupkey` names a key for content git-annex holds and says nothing
    for content git carries itself.
    """
    rel = str(path.relative_to(repo))
    # Not `_git`: lookupkey exits nonzero for a file git carries itself,
    # which is an answer rather than a failure.
    found = project._run(["git", "annex", "lookupkey", rel], cwd=repo)
    return found.returncode == 0 and bool(found.stdout.strip())


# ---- what git-annex stores, and what git carries ---------------------------


def test_save_puts_result_bytes_in_the_annex_and_the_record_in_git(repo: Path) -> None:
    """The whole storage policy, exercised end to end. The record has to
    stay a plain git blob: ASTRA reads it on clones that have fetched no
    annex content at all."""
    results = repo / "results" / "baseline"
    results.mkdir(parents=True)
    (results / "best_fit.csv").write_text("a,b\n1,2\n")
    record = repo / ".astra" / "records" / "baseline" / "best_fit.json"
    record.parent.mkdir(parents=True)
    record.write_text('{"content_hash": "sha256:abc"}\n')

    assert dataset.save(repo, [results, record], "materialize best_fit")

    assert _annexed(repo, results / "best_fit.csv")
    assert not _annexed(repo, record)
    assert not dataset.status(repo)


def test_a_plain_git_add_annexes_content_by_itself(repo: Path) -> None:
    """`filter=annex` is what makes git's own add route content, which is
    what lets lc — and everyone else — never run a git-annex command."""
    results = repo / "results" / "baseline"
    results.mkdir(parents=True)
    (results / "best_fit.csv").write_text("a,b\n1,2\n")

    dataset._git(["add", "-A", "--", "results"], cwd=repo)
    dataset._git(["commit", "-q", "-m", "plain git"], cwd=repo)

    assert _annexed(repo, results / "best_fit.csv")


def test_dot_paths_follow_the_storage_policy_not_annex_defaults(repo: Path) -> None:
    """git-annex routes any file under a dot-directory to git whatever
    `annex.largefiles` says, unless the add opts in — so the image
    archive under `.datalad/environments/`, or a `.cache.h5` a recipe
    writes into results/, would land as a full blob in git, silently,
    and every clone would carry the bytes forever. `save` opts in
    unconditionally, so the attributes alone decide: archives and dot
    outputs reach the annex, records under `.astra/` keep their exemption.
    The mutation check is a *plain* `git add` of the same shape, which
    keeps git-annex's stock dotfile behavior."""
    archive = repo / ".datalad" / "environments" / "lc-env-abc" / "image"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"pretend image bytes\n" * 64)
    output = repo / "results" / "baseline"
    output.mkdir(parents=True)
    (output / ".cache.h5").write_bytes(b"intermediate\n" * 64)
    record = repo / ".astra" / "records" / "baseline" / "fit.json"
    record.parent.mkdir(parents=True)
    record.write_text("{}\n" * 64)

    dataset.save(repo, [archive.parent, output, record], "routed")

    assert _annexed(repo, archive)
    assert _annexed(repo, output / ".cache.h5")
    assert not _annexed(repo, record)

    plain = repo / ".datalad" / "environments" / "lc-env-def" / "image"
    plain.parent.mkdir(parents=True)
    plain.write_bytes(b"pretend image bytes\n" * 64)
    dataset._git(["add", "-A", "--", ".datalad/environments/lc-env-def"], cwd=repo)
    dataset._git(["commit", "-q", "-m", "plain add"], cwd=repo)
    assert not _annexed(repo, plain)


def test_analysis_code_stays_in_git_and_stays_writable(repo: Path) -> None:
    """The default `annex.largefiles=nothing` is what keeps `filter=annex`
    from routing source files into the annex along with the data."""
    (repo / "src").mkdir()
    (repo / "src" / "fit.py").write_text("print('hi')\n")

    dataset.save(repo, [repo], "the analysis")

    assert not _annexed(repo, repo / "src" / "fit.py")
    (repo / "src" / "fit.py").write_text("print('edited')\n")


def test_data_is_annexed_too(repo: Path) -> None:
    """Declared inputs are content as much as outputs are; the repository
    is the complete record of what produced what."""
    (repo / "data" / "catalog.fits").write_bytes(b"\x00" * 64)
    dataset.save(repo, [repo / "data"], "input data")

    assert _annexed(repo, repo / "data" / "catalog.fits")


# ---- one copy on disk, and finding what is not there -----------------------


def _content_object(repo: Path, path: Path) -> Path:
    """Where git-annex keeps the bytes for *path*."""
    rel = str(path.relative_to(repo))
    key = dataset._git(["annex", "lookupkey", rel], cwd=repo).strip()
    return (repo / dataset._git(["annex", "contentlocation", key], cwd=repo).strip()).resolve()


def test_a_saved_result_is_hard_linked_to_its_annex_object(repo: Path) -> None:
    """`annex.thin` for lc's own add: a result exists once on disk, not
    twice. Safe here and only here — thin's hazard is an in-place write,
    and a worker removes an output directory before rebuilding it."""
    output = repo / "results" / "baseline" / "best_fit"
    output.mkdir(parents=True)
    (output / "fit.csv").write_text("a,b\n1,2\n")

    dataset.save(repo, [output], "materialize best_fit")

    result = output / "fit.csv"
    assert result.stat().st_ino == _content_object(repo, result).stat().st_ino
    assert result.stat().st_nlink == 2


def test_a_researchers_own_add_is_left_as_a_copy(repo: Path) -> None:
    """Thin is set for lc's add alone, never in the repository's config —
    a declared input is added by the researcher, whose tools do open files
    for update, and an in-place write to a thin file rewrites the annex
    object under the key that names it."""
    (repo / "data" / "catalog.fits").write_bytes(b"\x00" * 64)

    dataset._git(["add", "-A", "--", "data"], cwd=repo)
    dataset._git(["commit", "-q", "-m", "input data"], cwd=repo)

    catalog = repo / "data" / "catalog.fits"
    assert _annexed(repo, catalog)
    assert catalog.stat().st_nlink == 1


def test_content_that_is_not_here_is_refused_thin_or_not(repo: Path) -> None:
    """The requirement a researcher's own choices create: `annex.thin` and
    `git annex lock` are theirs to set on their clone, so lc must recognise
    an absent file in every shape it can arrive in — never hash it."""
    output = repo / "results" / "baseline" / "best_fit"
    output.mkdir(parents=True)
    (output / "thin.bin").write_bytes(b"\x01" * 4096)
    (output / "fat.bin").write_bytes(b"\x02" * 4096)
    dataset.save(repo, [output], "materialize best_fit")
    # `save` made both thin; put one back to a copy, so the two shapes an
    # *unlocked* file takes are both represented.
    dataset._git(["-c", "annex.thin=false", "annex", "fix", "fat.bin"], cwd=output)
    assert (output / "thin.bin").stat().st_nlink == 2
    assert (output / "fat.bin").stat().st_nlink == 1

    for name in ("thin.bin", "fat.bin"):
        dataset._git(["annex", "drop", "--force", "--", name], cwd=output)
        with pytest.raises(content.ContentNotFetchedError, match="not in this clone"):
            content.require_fetched(output / name)


def test_a_locked_file_without_its_content_is_refused_too(repo: Path) -> None:
    """`git annex lock` turns the tree back into symlinks, and an unfetched
    one dangles rather than reading as a pointer."""
    output = repo / "results" / "baseline" / "best_fit"
    output.mkdir(parents=True)
    (output / "fit.csv").write_text("a,b\n1,2\n")
    dataset.save(repo, [output], "materialize best_fit")

    dataset._git(["annex", "lock", "--", "fit.csv"], cwd=output)
    dataset._git(["annex", "drop", "--force", "--", "fit.csv"], cwd=output)

    assert (output / "fit.csv").is_symlink()
    assert not (output / "fit.csv").exists()
    with pytest.raises(content.ContentNotFetchedError, match="not in this clone"):
        content.require_fetched(output / "fit.csv")


# ---- committing ------------------------------------------------------------


def test_save_leaves_foreign_staged_work_staged_and_uncommitted(repo: Path) -> None:
    """The user can `git add` while a graph runs; the next save must not
    sweep it. The commit is a partial commit — built from HEAD plus the
    saved paths alone — so their work stays exactly where they left it:
    staged, and in no commit of lc's."""
    (repo / "notes.py").write_text("draft = True\n")
    dataset._git(["add", "--", "notes.py"], cwd=repo)
    out = repo / "results" / "fit"
    out.mkdir()
    (out / "value.txt").write_text("42\n")

    assert dataset.save(repo, [out], "make fit")

    committed = dataset._git(["show", "--name-only", "--format=", "HEAD"], cwd=repo).split()
    assert "notes.py" not in committed
    assert sorted(committed) == ["results/fit/value.txt"]
    staged = dataset._git(["diff", "--cached", "--name-only"], cwd=repo).split()
    assert staged == ["notes.py"], "still staged, exactly as the user left it"


def test_save_sees_nothing_to_commit_past_foreign_staged_work(repo: Path) -> None:
    """The nothing-to-commit probe is scoped like the commit, or foreign
    staged content would make an empty save attempt a commit and fail."""
    (repo / "notes.py").write_text("draft = True\n")
    dataset._git(["add", "--", "notes.py"], cwd=repo)

    assert not dataset.save(repo, [repo / "results"], "nothing here")


def test_a_save_refuses_what_git_staged_where_it_must_not_go(repo: Path) -> None:
    """Where bytes go is what git staged, whatever the attributes seem to
    say: an expression can turn on the bytes. Refused, nothing is committed
    and nothing is left staged."""
    (repo / ".gitattributes").write_text(
        (repo / ".gitattributes").read_text()
        + "results/** annex.largefiles=largerthan=1kb\n"
        + ".astra/** annex.largefiles=largerthan=1b\n"
    )
    dataset.save(repo, [repo / ".gitattributes"], "route by size")
    result = repo / "results" / "fit.csv"
    result.write_text("a,b\n")
    record = repo / ".astra" / "records" / "fit.json"
    record.parent.mkdir(parents=True)
    record.write_text("{}\n")
    head = dataset.head(repo)

    with pytest.raises(project.ProjectError) as raised:
        dataset.save(repo, [result, record], "fit", annexed=[result], in_git=[record])

    assert "results/fit.csv was staged in git itself" in str(raised.value)
    assert ".astra/records/fit.json was staged in the annex" in str(raised.value)
    assert dataset.head(repo) == head
    assert not dataset._git(["diff", "--cached", "--name-only"], cwd=repo).strip()


def test_a_clone_made_right_after_a_save_can_fetch_what_it_stored(
    repo: Path, tmp_path: Path
) -> None:
    """git-annex journals where content is when an add stores it, and a
    clone made before that is committed to its branch is told of no copy
    (*measured*)."""
    result = repo / "results" / "fit.csv"
    result.write_text("a,b\n1,2\n")
    dataset.save(repo, [result], "fit")
    clone = tmp_path / "clone"
    dataset._git(["clone", "-q", str(repo), str(clone)], cwd=tmp_path)
    for key, value in (("user.email", "t@example.com"), ("user.name", "Test")):
        dataset._git(["config", key, value], cwd=clone)
    dataset.init_annex(clone)

    dataset._git(["annex", "get", "-q", "--", "results/fit.csv"], cwd=clone)

    assert (clone / "results" / "fit.csv").read_text() == "a,b\n1,2\n"


def test_save_reports_when_there_was_nothing_to_commit(repo: Path) -> None:
    """`lc materialize` may not leave an empty commit behind for an output
    that produced nothing new."""
    assert dataset.save(repo, [repo / "results"], "again") is False


def test_save_stages_what_a_rebuild_deleted(repo: Path) -> None:
    """A rebuild resets the output directory, so a file the previous run
    produced and this one did not has to leave the commit as well."""
    output = repo / "results" / "baseline" / "best_fit"
    output.mkdir(parents=True)
    (output / "fit.csv").write_text("first\n")
    (output / "extra.csv").write_text("gone next time\n")
    dataset.save(repo, [output], "first")

    _rebuild(output)
    (output / "fit.csv").write_text("second\n")
    assert dataset.save(repo, [output], "second")

    assert not dataset.status(repo)
    tracked = dataset._git(["ls-files", "--", "results"], cwd=repo)
    assert "extra.csv" not in tracked


# ---- leaving the tree as clean as it was found -----------------------------


def test_restore_undoes_a_half_written_rebuild(repo: Path) -> None:
    """The invariant that makes the dirty-tree refusal survivable: a recipe
    that truncates a committed output and then fails must not leave the
    next run telling the user to commit the wreckage."""
    output = repo / "results" / "baseline" / "best_fit"
    output.mkdir(parents=True)
    (output / "fit.csv").write_text("good\n")
    dataset.save(repo, [output], "good")

    _rebuild(output)
    (output / "fit.csv").write_text("truncated\n")
    (output / "junk.tmp").write_text("half a run\n")

    dataset.restore(repo, [output])

    assert not dataset.status(repo)
    assert not (output / "junk.tmp").exists()
    assert (output / "fit.csv").read_text() == "good\n"


def test_restore_of_a_never_committed_result_is_not_an_error(repo: Path) -> None:
    """A first materialization has nothing in HEAD to go back to, and the
    naive `git checkout HEAD -- <dir>` exits nonzero on the pathspec."""
    output = repo / "results" / "baseline" / "best_fit"
    output.mkdir(parents=True)
    (output / "fit.csv").write_text("never committed\n")

    dataset.restore(repo, [output])

    assert not dataset.status(repo)
    assert not (output / "fit.csv").exists()


def test_restore_of_a_staged_but_unborn_result_does_not_raise(repo: Path) -> None:
    """The state an interrupted `save` leaves: staged, never committed.
    `ls-files --with-tree=HEAD` calls such a path known while
    `checkout HEAD --` then exits nonzero on it — so a guard built on the
    former makes the restore in materialize's `finally` raise over
    whatever interrupted the run. Unstaging is not restore's job (git's
    own `clean` leaves an index entry alone); not raising is."""
    output = repo / "results" / "baseline" / "best_fit.csv"
    output.parent.mkdir(parents=True)
    output.write_text("staged, never committed\n")
    dataset._git(["add", "--", "results"], cwd=repo)

    dataset.restore(repo, [":(glob)results/baseline/best_fit.*"])


def test_restore_takes_a_glob_pathspec(repo: Path) -> None:
    """An output owns whatever its id names, so the pathspec is a glob —
    which is what rules out `cat-file -e` and `ls-tree` as guards."""
    output = repo / "results" / "baseline" / "best_fit.csv"
    output.parent.mkdir(parents=True)
    output.write_text("committed\n")
    dataset.save(repo, [output], "the first version")
    output.unlink()

    dataset.restore(repo, [":(glob)results/baseline/best_fit.*"])

    assert output.read_text() == "committed\n"


def test_restore_is_scoped_to_the_paths_it_is_given(repo: Path) -> None:
    """Never `git checkout HEAD -- .`: a failed task must not discard edits
    made elsewhere while the graph was running."""
    (repo / "notes.md").write_text("original\n")
    output = repo / "results" / "baseline" / "best_fit"
    output.mkdir(parents=True)
    (output / "fit.csv").write_text("good\n")
    dataset.save(repo, [repo], "notes and a result")

    (repo / "notes.md").write_text("edited mid-run\n")
    _rebuild(output)
    (output / "fit.csv").write_text("wreckage\n")

    dataset.restore(repo, [output])

    assert (repo / "notes.md").read_text() == "edited mid-run\n"
    assert dataset.status(repo) == [(" M", "notes.md")]


# ---- reading the state of the tree -----------------------------------------


def test_status_reports_uncommitted_changes(repo: Path) -> None:
    assert not dataset.status(repo)

    (repo / "src").mkdir()
    (repo / "src" / "fit.py").write_text("print('hi')\n")

    assert dataset.status(repo)
    assert ("??", "src/") in dataset.status(repo)


def test_status_honours_gitignore(repo: Path) -> None:
    """`.venv/` must never dirty a tree — a materialize would refuse to run
    in any project that had been synced."""
    (repo / ".gitignore").write_text(templates.read("gitignore.tmpl"))
    dataset.save(repo, [repo], "ignores")
    (repo / ".venv" / "bin").mkdir(parents=True)
    (repo / ".venv" / "bin" / "python").write_text("")

    assert not dataset.status(repo)


def test_status_is_scoped_to_the_project_inside_a_larger_repository(
    tmp_path: Path, real_tools: None
) -> None:
    """`lc init subdir/` adopts an enclosing work tree rather than nesting a
    new one, so a project can sit inside a bigger repository. Unscoped,
    porcelain covers that whole tree — an unrelated edit anywhere in it
    would refuse every run in the project."""
    outer = tmp_path / "outer"
    project_root = outer / "project"
    (project_root / "results").mkdir(parents=True)
    (project_root / "src.py").write_text("print('hi')\n")
    (outer / "unrelated.txt").write_text("someone else's work\n")
    dataset.init_git(outer)
    for key, value in (("user.email", "t@example.com"), ("user.name", "Test")):
        dataset._git(["config", key, value], cwd=outer)
    dataset._git(["add", "-A", "."], cwd=outer)
    dataset._git(["commit", "-q", "-m", "outer"], cwd=outer)

    (outer / "unrelated.txt").write_text("edited while the project is clean\n")
    assert not dataset.status(project_root)

    (project_root / "results" / "fit.csv").write_text("1\n")
    # Relative to the project, not to the repository — a caller sorting by
    # path class cannot recognise `project/results/`.
    assert dataset.status(project_root) == [("??", "results/")]


def test_status_names_each_path_as_it_is_on_disk(repo: Path) -> None:
    """git quotes a name holding a space or an accent unless asked for
    NUL-separated output, and no comparison with a real path then holds."""
    (repo / "étape une").mkdir()
    (repo / "étape une" / "a b.txt").write_text("1\n")
    dataset.save(repo, [repo], "a folder with a space and an accent")
    (repo / "étape une" / "a b.txt").write_text("2\n")
    (repo / "étape une" / "c d.txt").write_text("new\n")

    assert sorted(dataset.status(repo)) == [
        (" M", "étape une/a b.txt"),
        ("??", "étape une/c d.txt"),
    ]


def test_a_rename_is_a_deletion_and_an_addition(repo: Path) -> None:
    """Paired, the entry would name two paths in one, and a caller sorting
    by path class would see neither."""
    (repo / "old.txt").write_text("1\n")
    dataset.save(repo, [repo], "a file")
    dataset._git(["mv", "old.txt", "new.txt"], cwd=repo)

    assert sorted(dataset.status(repo)) == [("A ", "new.txt"), ("D ", "old.txt")]


@pytest.fixture
def with_submodule(repo: Path, tmp_path: Path) -> Path:
    """*repo* with a committed submodule at `sub`, whose `.gitmodules`
    asks git to ignore it entirely."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    dataset.init_git(upstream)
    for key, value in (("user.email", "t@example.com"), ("user.name", "Test")):
        dataset._git(["config", key, value], cwd=upstream)
    (upstream / "code.py").write_text("print('hi')\n")
    dataset._git(["add", "code.py"], cwd=upstream)
    dataset._git(["commit", "-q", "-m", "code"], cwd=upstream)
    dataset._git(
        ["-c", "protocol.file.allow=always", "submodule", "add", "-q", str(upstream), "sub"],
        cwd=repo,
    )
    dataset._git(["config", "-f", ".gitmodules", "submodule.sub.ignore", "all"], cwd=repo)
    dataset._git(["add", ".gitmodules", "sub"], cwd=repo)
    dataset._git(["commit", "-q", "-m", "a submodule"], cwd=repo)
    assert not dataset.status(repo)
    return repo


def test_a_submodule_with_an_untracked_file_is_a_change(with_submodule: Path) -> None:
    """A recipe that reads from it would run on bytes the commit does not
    name — whatever `.gitmodules` asks git to ignore."""
    (with_submodule / "sub" / "stray.py").write_text("print('stray')\n")

    assert dataset.status(with_submodule) == [(" M", "sub")]


def test_a_submodule_at_another_commit_is_a_change(with_submodule: Path) -> None:
    sub = with_submodule / "sub"
    (sub / "code.py").write_text("print('moved')\n")
    dataset._git(
        ["-c", "user.email=t@example.com", "-c", "user.name=Test", "commit", "-qam", "move"],
        cwd=sub,
    )

    assert dataset.status(with_submodule) == [(" M", "sub")]


def test_untracked_files_count_whatever_the_repository_config_says(repo: Path) -> None:
    """`status.showUntrackedFiles=no` would hide a file a recipe reads but
    no commit holds."""
    dataset._git(["config", "status.showUntrackedFiles", "no"], cwd=repo)
    (repo / "fit.py").write_text("print('hi')\n")

    assert dataset.status(repo) == [("??", "fit.py")]


def test_untracked_files_count_whatever_the_users_config_says(
    with_submodule: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Set in the user's own config, the setting reaches the `git status`
    git runs inside a submodule, where no flag of the outer command does."""
    config = tmp_path / "gitconfig"
    config.write_text("[status]\n\tshowUntrackedFiles = no\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    (with_submodule / "fit.py").write_text("print('hi')\n")
    (with_submodule / "sub" / "stray.py").write_text("print('stray')\n")

    assert sorted(dataset.status(with_submodule)) == [(" M", "sub"), ("??", "fit.py")]


# ---- another git holding the index ----------------------------------------


def _hold_index(repo: Path, seconds: float) -> threading.Thread:
    """Take the index lock as another git command would, and let it go
    after *seconds*."""
    lock = repo / ".git" / "index.lock"
    lock.write_text("")
    release = threading.Timer(seconds, lock.unlink)
    release.start()
    return release


def test_a_save_waits_out_another_gits_hold_on_the_index(repo: Path) -> None:
    """A plain `git add` of a large file holds the index for as long as
    hashing it takes, and git refuses a second writer at once."""
    (repo / "results" / "fit.csv").write_text("1\n")
    _hold_index(repo, 1.0)

    assert dataset.save(repo, [repo / "results"], "a result")
    assert not dataset.status(repo)


def test_an_index_lock_that_goes_away_is_not_held(repo: Path) -> None:
    _hold_index(repo, 0.5)

    dataset.require_free_index(repo)


def test_an_index_lock_that_stays_is_named(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A git that was killed leaves the lock for good: past the patience
    a live command deserves, it is the answer."""
    monkeypatch.setattr(dataset, "_PATIENCE", 0.5)
    (repo / ".git" / "index.lock").write_text("")

    with pytest.raises(project.ProjectError, match=re.escape(str(repo / ".git" / "index.lock"))):
        dataset.require_free_index(repo)


def test_a_project_that_has_never_been_committed_reads_as_itself(
    tmp_path: Path, real_tools: None
) -> None:
    """git collapses a wholly untracked directory to its own name, which
    the prefix strip then empties. `.` is what the refusal has to say."""
    outer = tmp_path / "outer"
    (outer / "project").mkdir(parents=True)
    (outer / "project" / "astra.yaml").write_text("name: demo\n")
    dataset.init_git(outer)

    assert dataset.status(outer / "project") == [("??", ".")]


def test_a_repository_that_cannot_commit_is_refused_before_anything_runs(
    tmp_path: Path, real_tools: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh container or CI image has no git identity, which is the case
    this CLI is most often run in. Discovered at the first save, it would
    cost whatever the recipe had already computed.

    `user.useConfigOnly` is how the absence is staged, rather than an empty
    HOME: with no config git *guesses* from the username and the hostname,
    and whether the guess is one it will accept depends on the host — a
    Linux runner's `user@box.(none)` is refused and a macOS runner's is
    not. This turns the guess off, which is git's own switch for it.
    """
    root = tmp_path / "demo"
    root.mkdir()
    dataset.init_git(root)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "absent"))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(tmp_path / "absent"))
    for name in ("EMAIL", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(name, raising=False)
    dataset._git(["config", "user.useConfigOnly", "true"], cwd=root)

    with pytest.raises(project.ProjectError, match="no identity to commit with"):
        dataset.require_committer(root)

    for key, value in (("user.email", "t@example.com"), ("user.name", "Test")):
        dataset._git(["config", key, value], cwd=root)
    dataset.require_committer(root)


def test_ignore_rule_names_the_line_to_delete(repo: Path) -> None:
    """Convergence cannot repair this one, so the message has to point at
    the line rather than merely report that one exists."""
    (repo / ".gitignore").write_text("# lightcone-cli\n.venv/\nresults/*\n")

    assert dataset.ignore_rule(repo, "results/") == ".gitignore:3:results/*"
    assert dataset.ignore_rule(repo, "data/") is None


def test_ignore_rule_sees_through_a_tracked_path(repo: Path) -> None:
    """`--no-index` is load-bearing: without it git answers "not ignored"
    for anything already tracked, which is exactly the project where
    someone committed one result by hand and left the rule for the next."""
    (repo / ".gitignore").write_text("results/*\n")
    (repo / "results" / "kept.txt").write_text("added with -f\n")
    dataset._git(["add", "-f", "--", "results/kept.txt"], cwd=repo)
    dataset._git(["commit", "-q", "-m", "forced"], cwd=repo)

    assert dataset.ignore_rule(repo, "results/") == ".gitignore:1:results/*"


# ---- how git finds git-annex -----------------------------------------------


def test_git_dispatches_annex_from_the_ambient_path(repo: Path) -> None:
    """`git annex` is git finding a `git-annex` executable on PATH, not a
    builtin — and lc no longer arranges PATH for its subprocesses: every
    install channel carries the entry points beside the interpreter, so
    the environment lc inherits already resolves them. Checked by the
    spelling git itself uses rather than by resolving `git-annex`
    ourselves."""
    assert "git-annex version:" in dataset._git(["annex", "version"], cwd=repo)


def test_the_annex_executables_are_ours_to_install() -> None:
    """An installer links only the requested package's executables, and the
    researcher's own `git add` needs git-annex on the *shell's* PATH — so
    lightcone-cli re-declares the git-annex wheel's entry points verbatim.
    Mirrored, not invented: asserted against the wheel's own metadata, so
    an executable upstream adds, drops, or renames fails this test instead
    of failing `uv tool install lightcone-cli` for every user."""
    from importlib.metadata import distribution

    ours = {e.name: e.value for e in distribution("lightcone-cli").entry_points}
    theirs = {e.name: e.value for e in distribution("git-annex").entry_points}
    assert theirs  # the wheel stopped declaring entry points ⇒ redesign
    for name, value in theirs.items():
        assert ours.get(name) == value, f"{name} is not re-declared as {value}"


def test_the_worker_and_the_shim_are_never_console_scripts() -> None:
    """`python -m lightcone.engine.worker` makes an output unconditionally,
    commits nothing, and leaves the tree dirty by design; the shim is the
    sandbox's own plumbing. A `[project.scripts]` entry would put either
    on `$PATH` through `uv tool install` — a footgun `lc --help` already
    refuses to advertise. Every entry point is either the CLI or a
    mirrored git-annex executable, and nothing else."""
    from importlib.metadata import distribution

    theirs = {e.value for e in distribution("git-annex").entry_points}
    for entry in distribution("lightcone-cli").entry_points:
        assert "lightcone.engine" not in entry.value
        assert "_sandbox_exec" not in entry.value
        assert entry.value.startswith("lightcone.cli") or entry.value in theirs, entry


# ---- the annex filter, against a real shell ---------------------------------


def _researcher_shell(tmp_path: Path) -> dict[str, str] | None:
    """The environment a researcher's own `git add` runs in when nothing
    put git-annex on their PATH — the uvx case. The system directories
    carry git and the usual utilities; whether they also carry a
    git-annex is the host's business, and where they do there is nothing
    for these tests to prove. Probed by listing the directories rather
    than `shutil.which`, which the autouse `tools` fixture narrows for
    exactly the three tools this asks about."""
    dirs = [Path("/usr/bin"), Path("/bin")]
    if not any((d / "git").is_file() for d in dirs):
        return None
    if any((d / "git-annex").exists() for d in dirs):
        return None
    # `HOME` takes the global config out of play; `GIT_CONFIG_NOSYSTEM`
    # does the same for `/etc/gitconfig`, where a site-wide
    # `filter.annex.required` would otherwise decide these outcomes.
    return {
        "PATH": ":".join(str(d) for d in dirs),
        "HOME": str(tmp_path),
        "GIT_CONFIG_NOSYSTEM": "1",
    }


def _shell_git(repo: Path, env: dict[str, str], *argv: str) -> subprocess.CompletedProcess[str]:
    """Run git exactly as that shell would — not through `project._run`,
    whose environment is lc's own and always resolves the bundled annex."""
    git = next(p for d in env["PATH"].split(":") if (p := Path(d) / "git").is_file())
    return subprocess.run(
        [str(git), *argv], cwd=repo, env=env, capture_output=True, text=True, check=False
    )


@pytest.fixture
def no_annex_shell(tmp_path: Path) -> dict[str, str]:
    env = _researcher_shell(tmp_path)
    if env is None:
        pytest.skip("the system PATH itself resolves git-annex (or lacks git)")
    return env


def test_required_true_makes_the_missing_filter_loud_not_silent(
    repo: Path, no_annex_shell: dict[str, str]
) -> None:
    """The whole claim, measured: with the flag set, a shell that cannot
    resolve git-annex gets git's own hard refusal — nothing staged, exit
    nonzero — instead of raw bytes in git history."""
    assert not dataset.annex_filter_required(repo)
    dataset.set_annex_filter_required(repo)
    assert dataset.annex_filter_required(repo)
    (repo / "data" / "catalog.fits").write_bytes(b"\x00" * 4096)

    added = _shell_git(repo, no_annex_shell, "add", "data/catalog.fits")

    # The facts, not git's wording: Linux git reports the failed handshake
    # and then `fatal: <path>: clean filter 'annex' failed`, while macOS
    # git dies on the pkt-line read with `fatal: the remote end hung up
    # unexpectedly`. Both refuse, which is the whole claim.
    assert added.returncode != 0
    assert "annex" in added.stderr
    staged = _shell_git(repo, no_annex_shell, "diff", "--cached", "--name-only")
    assert staged.stdout.strip() == ""


def test_the_flag_is_read_from_the_repository_never_the_users_global_config(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The write is repository-local, so the read must be too. A user who
    once set the flag in `~/.gitconfig` would otherwise have every project
    report converged while its own `.git/config` carried nothing — and
    since this is local state a clone never receives, the protection would
    vanish the moment the repository moved to CI or another account."""
    global_config = tmp_path / "gitconfig"
    global_config.write_text('[filter "annex"]\n\trequired = true\n')
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))

    assert not dataset.annex_filter_required(repo)

    dataset.set_annex_filter_required(repo)
    assert dataset.annex_filter_required(repo)
    assert "required = true" in (repo / ".git" / "config").read_text()


@pytest.mark.parametrize("spelling", ["true", "1", "yes", "on"])
def test_any_spelling_git_reads_as_true_is_not_reported_as_drift(
    repo: Path, spelling: str
) -> None:
    """git's booleans are not one string. A repository someone configured
    by hand as `required = 1` is fully protected — reading it as drift
    would rewrite a repository that was already correct, and report every
    `lc init --check` on it as unconverged."""
    dataset._git(["config", "filter.annex.required", spelling], cwd=repo)

    assert dataset.annex_filter_required(repo)


@pytest.mark.skipif(
    sys.platform == "darwin",
    reason="macOS git aborts the filter handshake outright (exit 128, "
    "'the remote end hung up unexpectedly'), so the silent-passthrough "
    "hazard recorded here is the Linux behaviour",
)
def test_stock_plumbing_without_required_stages_raw_bytes_silently(
    repo: Path, no_annex_shell: dict[str, str]
) -> None:
    """The hazard the flag exists for, recorded as measured: as
    `git annex init` leaves a repository, a shell without git-annex
    prints an error, *exits 0*, and stages the raw bytes into git
    history — a 2 GB dataset in git proper, on every clone, forever.
    If this ever starts failing, git changed the behavior and the
    `required=true` net is worth re-examining.

    Linux-only, and that asymmetry is the point rather than a gap: macOS
    git happens to refuse the same situation on its own, so there the
    flag changes nothing — but a platform accident is not a guarantee,
    and the flag is what makes the refusal one on every host."""
    (repo / "data" / "catalog.fits").write_bytes(b"\x00" * 4096)

    added = _shell_git(repo, no_annex_shell, "add", "data/catalog.fits")

    assert added.returncode == 0
    # Not the shell's exact wording: bash says "command not found" where
    # dash — /bin/sh on Debian and Ubuntu — says only "not found".
    assert "not found" in added.stderr
    staged = _shell_git(repo, no_annex_shell, "cat-file", "-p", ":data/catalog.fits")
    assert not staged.stdout.startswith("/annex/objects/")


# ---- where the bytes go: the store, and what a clone keeps of it -------------


def test_a_clone_with_no_store_has_none(repo: Path) -> None:
    assert dataset.stores(repo) == []
    assert dataset.remote_url(repo, "origin") == ""


_LFS, _ALIAS = "8878da48-bec0-44ca-adbd-e0ae1eddc4d1", "edc5cc1a-1a90-41ea-807e-db796b960ade"


def _declares_git_lfs(
    monkeypatch: pytest.MonkeyPatch, repo: Path, name: str, alias: str, *more: str
) -> None:
    """Have *repo* declare a store in Git LFS at two addresses, enabled.

    Its `remote.log` lines are git-annex's own for such a store, as
    measured: declaring one for real asks GitHub about the address."""
    lines = (
        f"{_LFS} autoenable=true encryption=none name={name} type=git-lfs"
        " url=https://github.com/alice/analysis.git",
        f"{_ALIAS} autoenable=true sameas-name={alias} sameas-uuid={_LFS} type=git-lfs"
        " url=git@github.com:alice/analysis.git",
        *more,
    )
    declared = {line.split()[0]: line.split()[1:] for line in lines}
    monkeypatch.setattr(
        dataset, "_annex_log", lambda _directory, log: declared if log == "remote.log" else {}
    )
    for remote, url in (
        (name, "https://github.com/alice/analysis.git"),
        (alias, "git@github.com:alice/analysis.git"),
    ):
        dataset._git(["config", f"remote.{remote}.url", url], cwd=repo)


def test_a_store_is_read_from_its_declaration_under_whatever_name(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What makes a remote a store is how git-annex's branch declares it:
    enabled by itself. Here one declared by hand as `github-lfs`. Where it
    is, and whether this clone may send to it, are the clone's own
    configuration: a store it declared or confirmed is one it may send
    to; one git-annex enabled by itself is not, yet."""
    _declares_git_lfs(
        monkeypatch,
        repo,
        "github-lfs",
        "lfs-ssh",
        # Enabled by nobody but who asks.
        "2222 name=mine type=git location=/mine",
        # Declared, and not enabled in this clone.
        "3333 autoenable=true name=away type=git location=/away",
    )
    for key, value in (
        ("remote.github-lfs.annex-verified", "verified"),
        ("remote.lfs-ssh.annex-verified", "automatic"),
        ("remote.mine.url", "/mine"),
        ("remote.backup.url", "/elsewhere"),
    ):
        dataset._git(["config", key, value], cwd=repo)

    [store] = dataset.stores(repo)

    assert store == dataset.Store(
        "github-lfs",
        "https://github.com/alice/analysis.git",
        _LFS,
        {
            "github-lfs": ("https://github.com/alice/analysis.git", True),
            "lfs-ssh": ("git@github.com:alice/analysis.git", False),
        },
    )
    # A clone sends over the address its `origin` uses.
    assert (store.remote(ssh=False), store.remote(ssh=True)) == ("github-lfs", "lfs-ssh")


def test_a_store_of_a_kind_lc_does_not_publish_to_is_the_projects_all_the_same(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bucket someone declared is where the project's bytes are: read
    as its store, of no kind lc publishes to, so that nothing takes the
    project for one without a store. It is no git remote, and has no
    address a clone's configuration gives."""
    declared = {"1111": "autoenable=true name=bucket type=S3 bucket=results".split()}
    monkeypatch.setattr(
        dataset, "_annex_log", lambda _directory, log: declared if log == "remote.log" else {}
    )
    for key, value in (
        ("remote.bucket.annex-uuid", "1111"),
        ("remote.bucket.annex-verified", "automatic"),
    ):
        dataset._git(["config", key, value], cwd=repo)

    assert dataset.stores(repo) == [dataset.Store(None, "", "1111", {"bucket": ("", False)})]
    assert dataset._remotes(repo) == {"1111": "bucket"}


def test_a_store_at_one_address_is_sent_to_there_however_origin_is_reached() -> None:
    store = dataset.Store("directory", "/shared/store", "1111", {"shared": ("/shared/store", True)})

    assert (store.remote(ssh=False), store.remote(ssh=True)) == ("shared", "shared")


def test_of_two_lines_about_one_repository_the_newer_holds(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """git-annex's logs, read as it merges them, from the clone's own
    branch and from each a remote brought and no command has merged yet."""
    logs = {
        "refs/heads/git-annex:remote.log": "u1 name=old autoenable=true timestamp=10s\n\n",
        "refs/remotes/origin/git-annex:remote.log": (
            "u1 name=new autoenable=true timestamp=20.5s\nu2 name=other type=git timestamp=5s\n"
        ),
    }

    def ask(argv: list[str], *, cwd: Path) -> str | None:
        if argv[0] == "for-each-ref":
            return "refs/heads/git-annex\nrefs/remotes/origin/git-annex\n"
        return logs.get(argv[1])

    monkeypatch.setattr(dataset, "_ask", ask)

    assert dataset._annex_log(repo, "remote.log") == {
        "u1": ["name=new", "autoenable=true"],
        "u2": ["name=other", "type=git"],
    }
    assert dataset._annex_log(repo, "trust.log") == {}
    assert dataset._declared(repo) == {"new": {"name": "new", "autoenable": "true", "uuid": "u1"}}


def test_a_clone_notes_for_itself_what_no_clone_gets_from_another(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """For an `origin` on GitHub: git-annex leaves it alone, and DataLad
    sends bytes to the store first. Over ssh, the store is the one reached
    over ssh."""
    assert dataset.unsettled(repo) == {}  # no origin, nothing to note

    dataset._git(["remote", "add", "origin", "git@github.com:alice/analysis.git"], cwd=repo)
    assert dataset.unsettled(repo) == {"remote.origin.annex-ignore": "true"}

    _declares_git_lfs(monkeypatch, repo, dataset.STORE, dataset.STORE_SSH)
    dataset.settle(repo)

    assert dataset.unsettled(repo) == {}
    depends = dataset._git(["config", "remote.origin.datalad-publish-depends"], cwd=repo)
    assert depends.strip() == "origin-storage-ssh"


def test_an_origin_that_is_not_on_github_is_left_as_it_is(repo: Path, tmp_path: Path) -> None:
    dataset._git(["remote", "add", "origin", str(tmp_path / "a-colleague")], cwd=repo)

    assert dataset.unsettled(repo) == {}


def test_a_command_that_reaches_a_remote_never_waits_on_anyone(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ssh runs in batch mode, on top of whatever the repository already
    asks of it, and git's own prompts are off for every tool."""
    assert dataset.reaching(repo) == [
        "-c", "core.sshCommand=ssh -o BatchMode=yes",
        "-c", "annex.ssh-options=-o BatchMode=yes",
    ]  # fmt: skip
    assert project.child_env()["GIT_TERMINAL_PROMPT"] == "0"

    dataset._git(["config", "core.sshCommand", "ssh -i ~/.ssh/cluster"], cwd=repo)
    dataset._git(["config", "annex.ssh-options", "-o ServerAliveInterval=30"], cwd=repo)

    assert dataset.reaching(repo) == [
        "-c", "core.sshCommand=ssh -i ~/.ssh/cluster -o BatchMode=yes",
        "-c", "annex.ssh-options=-o ServerAliveInterval=30 -o BatchMode=yes",
    ]  # fmt: skip


def test_lcs_own_ssh_is_the_users_in_batch_mode(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What makes a store over ssh runs the ssh git would: the one in the
    environment before the repository's, and never one that prompts."""
    assert dataset.ssh(repo) == "ssh -o BatchMode=yes"
    dataset._git(["config", "core.sshCommand", "ssh -i ~/.ssh/cluster"], cwd=repo)
    assert dataset.ssh(repo) == "ssh -i ~/.ssh/cluster -o BatchMode=yes"
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -F /etc/ssh/site")
    assert dataset.ssh(repo) == "ssh -F /etc/ssh/site -o BatchMode=yes"


def _store_in(directory: Path) -> str:
    """Make the git-annex repository a store in *directory* is, and give its id."""
    dataset._git(["init", "-q", "--bare", str(directory)], cwd=directory.parent)
    for key, value in (("user.email", "t@example.com"), ("user.name", "Test")):
        dataset._git(["config", key, value], cwd=directory)
    dataset._git(["annex", "init", "-q"], cwd=directory)
    return dataset._git(["config", "annex.uuid"], cwd=directory).strip()


@pytest.mark.parametrize("name", [dataset.STORE, "shared"])
def test_the_store_is_known_by_its_declaration_before_it_is_ever_reached(
    repo: Path, tmp_path: Path, name: str
) -> None:
    """git-annex's branch says which repository the store is, under
    whatever name. A clone's configuration says so only once git-annex has
    looked there, and until then what the store holds must not read as
    held by nobody."""
    its_id = _store_in(tmp_path / "store")
    dataset._git(
        ["annex", "initremote", name, "type=git", f"location={tmp_path / 'store'}",
         "autoenable=true"],
        cwd=repo,
    )  # fmt: skip

    [store] = dataset.stores(repo)
    assert (store.kind, store.uuid, list(store.remotes)) == ("directory", its_id, [name])
    # git-annex has yet to look in the store, and has noted nothing of it.
    assert not dataset._git_ok(["config", f"remote.{name}.annex-uuid"], cwd=repo)
    assert dataset._remotes(repo)[its_id] == name


def test_a_declared_store_is_enabled_only_where_one_waits(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Forcing git-annex to enable what the project declares is a command
    more for every verb: asked only where a declaration has no remote in
    this clone, and never for a store marked dead, which git-annex enables
    for nobody (measured)."""
    forced: list[list[str]] = []
    run = project._run

    def spy(argv: list[str], **kwargs: Any) -> Any:
        if "--autoenable" in argv:
            forced.append(argv)
        return run(argv, **kwargs)

    monkeypatch.setattr(project, "_run", spy)

    dataset.enable_store(repo)  # nothing declared
    _store_in(tmp_path / "store")
    dataset._git(
        ["annex", "initremote", "shared", "type=git", f"location={tmp_path / 'store'}",
         "autoenable=true"],
        cwd=repo,
    )  # fmt: skip
    dataset.enable_store(repo)  # declared, and here
    assert forced == []

    dataset._git(["remote", "remove", "shared"], cwd=repo)
    dataset.enable_store(repo)
    assert forced == [["git", "annex", "init", "--autoenable", "--force"]]
    assert dataset.remote_url(repo, "shared") == str(tmp_path / "store")

    dataset._git(["annex", "dead", "shared"], cwd=repo)
    dataset._git(["remote", "remove", "shared"], cwd=repo)
    dataset.enable_store(repo)
    assert len(forced) == 1


def test_only_what_git_annex_says_it_sent_was_sent(repo: Path, tmp_path: Path) -> None:
    """For a key its log places here and its annex does not hold, git-annex
    answers with an empty line and exits 0 (measured): that is no transfer."""
    backup = tmp_path / "backup"
    dataset._git(["clone", "-q", "--bare", str(repo), str(backup)], cwd=tmp_path)
    dataset._git(["remote", "add", "backup", str(backup)], cwd=repo)
    for name in ("kept", "lost"):
        (repo / "results" / f"{name}.txt").write_text(f"{name}\n")
    dataset.save(repo, [repo / "results"], "two results")
    keys = dataset.annexed(repo, dataset.head(repo))
    kept, lost = keys["results/kept.txt"], keys["results/lost.txt"]
    held = repo / dataset._git(["annex", "contentlocation", lost], cwd=repo).strip()
    held.parent.chmod(0o700)
    held.unlink()

    unsent = dataset.send(repo, "backup", [kept, lost])

    assert list(unsent) == [lost] and "not in this clone's annex" in unsent[lost]
    assert dataset.copies(repo, [kept])[kept][1]  # the other one went


def test_a_token_in_the_environment_is_offered_to_github_alone_and_never_written(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """git asks the helper for github.com only, the helper names the
    variable rather than holding the token, and nothing is stored: the
    token is in no command line and no file."""
    monkeypatch.setenv("GITHUB_TOKEN", "s3cret")

    options = dataset.reaching(repo)

    helper = next(option for option in options if option.startswith("credential."))
    assert helper.startswith("credential.https://github.com.helper=!")
    assert "s3cret" not in " ".join(options)
    asked = subprocess.run(
        ["git", *options, "credential", "fill"],
        cwd=repo, input="protocol=https\nhost=github.com\n\n", env=project.child_env(),
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    assert "password=s3cret" in asked.stdout
    elsewhere = subprocess.run(
        ["git", *options, "credential", "fill"],
        cwd=repo, input="protocol=https\nhost=example.org\n\n", env=project.child_env(),
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert "s3cret" not in elsewhere.stdout
    assert "s3cret" not in (repo / ".git" / "config").read_text()


def test_ghs_own_credentials_are_offered_where_it_is_installed(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lightcone.engine import github

    monkeypatch.setattr(github, "has_gh", lambda: True)

    assert "credential.https://github.com.helper=!gh auth git-credential" in dataset.reaching(repo)
