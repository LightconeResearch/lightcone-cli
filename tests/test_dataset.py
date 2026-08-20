"""Tests for `lightcone.engine.dataset` — how a project stores what it made.

Most of this file runs against a **real** git + git-annex repository, via
the `real_tools` fixture. That is deliberate and it is the exception in
this suite: the question these tests ask is whether bytes land in the
annex or as a blob in git, and a fake that answered it would only be
restating what the code already believes.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from lightcone.engine import assets, dataset, project, templates


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


def test_save_puts_result_bytes_in_the_annex_and_the_manifest_in_git(repo: Path) -> None:
    """The whole storage policy, exercised end to end. The manifest has to
    stay a plain git blob: `lc` reads it on clones that have fetched no
    annex content at all."""
    output = repo / "results" / "baseline" / "best_fit"
    output.mkdir(parents=True)
    (output / "fit.csv").write_text("a,b\n1,2\n")
    (output / ".lightcone-manifest.json").write_text('{"data_version": "abc"}\n')

    assert dataset.save(repo, [output], "materialize best_fit")

    assert _annexed(repo, output / "fit.csv")
    assert not _annexed(repo, output / ".lightcone-manifest.json")
    assert not dataset.status(repo)


def test_a_plain_git_add_annexes_content_by_itself(repo: Path) -> None:
    """`filter=annex` is what makes git's own add route content, which is
    what lets lc — and everyone else — never run a git-annex command."""
    output = repo / "results" / "baseline" / "best_fit"
    output.mkdir(parents=True)
    (output / "fit.csv").write_text("a,b\n1,2\n")

    dataset._git(["add", "-A", "--", "results"], cwd=repo)
    dataset._git(["commit", "-q", "-m", "plain git"], cwd=repo)

    assert _annexed(repo, output / "fit.csv")


def test_dot_paths_follow_the_storage_policy_not_annex_defaults(repo: Path) -> None:
    """git-annex routes any file under a dot-directory to git whatever
    `annex.largefiles` says, unless the add opts in — so the image
    archive under `.datalad/environments/`, or a `.cache.h5` a recipe
    writes into results/, would land as a full blob in git, silently,
    and every clone would carry the bytes forever. `save` opts in
    unconditionally, so the attributes alone decide: archives and dot
    outputs reach the annex, dot-named manifests keep their exemption.
    The mutation check is a *plain* `git add` of the same shape, which
    keeps git-annex's stock dotfile behavior."""
    archive = repo / ".datalad" / "environments" / "lc-env-abc" / "image"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"pretend image bytes\n" * 64)
    output = repo / "results" / "baseline" / "fit"
    output.mkdir(parents=True)
    (output / ".cache.h5").write_bytes(b"intermediate\n" * 64)
    (output / ".lightcone-manifest.json").write_text("{}\n")

    dataset.save(repo, [archive.parent, output], "routed")

    assert _annexed(repo, archive)
    assert _annexed(repo, output / ".cache.h5")
    assert not _annexed(repo, output / ".lightcone-manifest.json")

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
        with pytest.raises(assets.ContentNotFetchedError, match="git annex get"):
            assets.data_version(output / name)

    with pytest.raises(assets.ContentNotFetchedError):
        assets.data_version(output)


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
    with pytest.raises(assets.ContentNotFetchedError, match="git annex get"):
        assets.data_version(output)


# ---- committing ------------------------------------------------------------


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


def test_restore_of_a_never_committed_output_is_not_an_error(repo: Path) -> None:
    """A first materialization has nothing in HEAD to go back to, and the
    naive `git checkout HEAD -- <dir>` exits nonzero on the pathspec."""
    output = repo / "results" / "baseline" / "best_fit"
    output.mkdir(parents=True)
    (output / "fit.csv").write_text("never committed\n")

    dataset.restore(repo, [output])

    assert not dataset.status(repo)
    assert not (output / "fit.csv").exists()


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


