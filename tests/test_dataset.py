"""Tests for `lightcone.engine.dataset` — how a project stores what it made.

Most of this file runs against a **real** git + git-annex repository, via
the `real_tools` fixture. That is deliberate and it is the exception in
this suite: the question these tests ask is whether bytes land in the
annex or as a blob in git, and a fake that answered it would only be
restating what the code already believes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from lightcone.engine import dataset, templates
from lightcone.engine.project import ProjectError


@pytest.fixture
def repo(tmp_path: Path, real_tools: None) -> Path:
    """A converged-enough dataset: a repository, an annex, a storage policy.

    Identity is set locally rather than relied on from the host, because
    `git annex init` and every `save` make a commit.
    """
    root = tmp_path / "demo"
    (root / "results").mkdir(parents=True)
    (root / "data").mkdir()
    (root / ".gitattributes").write_text(templates.gitattributes())
    dataset.init_git(root)
    for key, value in (("user.email", "t@example.com"), ("user.name", "Test")):
        dataset._git(["config", key, value], cwd=root)
    dataset.init_annex(root)
    dataset.save(root, [root], "scaffold")
    return root


def _rebuild(output: Path) -> None:
    """What a worker does before it runs a recipe: the recipe owns the
    directory, and a stale file must not survive into the next hash.

    It is also the only way to overwrite an output — annexed files are
    read-only symlinks into the object store.
    """
    shutil.rmtree(output, ignore_errors=True)
    output.mkdir(parents=True)


def _annexed(path: Path) -> bool:
    """Whether git-annex holds the bytes rather than git.

    An annexed file is a symlink into the object store; a file git carries
    is an ordinary file.
    """
    return path.is_symlink() and ".git/annex/objects" in os.readlink(path)


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

    assert _annexed(output / "fit.csv")
    assert not _annexed(output / ".lightcone-manifest.json")
    assert dataset.is_clean(repo)


def test_a_plain_git_add_would_not_have_annexed_it(repo: Path) -> None:
    """The trap `save`'s ordering exists to avoid: `.gitattributes` alone
    does not route anything — without the `git annex add` first, git
    commits the bytes itself, silently."""
    output = repo / "results" / "baseline" / "best_fit"
    output.mkdir(parents=True)
    (output / "fit.csv").write_text("a,b\n1,2\n")

    dataset._git(["add", "-A", "--", "results"], cwd=repo)
    dataset._git(["commit", "-q", "-m", "the wrong way"], cwd=repo)

    assert not _annexed(output / "fit.csv")


def test_analysis_code_stays_in_git_and_stays_writable(repo: Path) -> None:
    """`git annex add` annexes whatever it is handed, so the whole tree
    goes through it on the documented save. Without the default line in
    `.gitattributes`, `src/fit.py` comes back a read-only symlink into the
    object store and the next edit fails with EACCES."""
    (repo / "src").mkdir()
    (repo / "src" / "fit.py").write_text("print('hi')\n")

    dataset.save(repo, [repo], "the analysis")

    assert not _annexed(repo / "src" / "fit.py")
    (repo / "src" / "fit.py").write_text("print('edited')\n")


def test_data_is_annexed_too(repo: Path) -> None:
    """Declared inputs are content as much as outputs are; the repository
    is the complete record of what produced what."""
    (repo / "data" / "catalog.fits").write_bytes(b"\x00" * 64)
    dataset.save(repo, [repo / "data"], "input data")

    assert _annexed(repo / "data" / "catalog.fits")


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

    assert dataset.is_clean(repo)
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

    assert dataset.is_clean(repo)
    assert not (output / "junk.tmp").exists()
    assert (output / "fit.csv").read_text() == "good\n"


def test_restore_of_a_never_committed_output_is_not_an_error(repo: Path) -> None:
    """A first materialization has nothing in HEAD to go back to, and the
    naive `git checkout HEAD -- <dir>` exits nonzero on the pathspec."""
    output = repo / "results" / "baseline" / "best_fit"
    output.mkdir(parents=True)
    (output / "fit.csv").write_text("never committed\n")

    dataset.restore(repo, [output])

    assert dataset.is_clean(repo)
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
    assert dataset.is_clean(repo)

    (repo / "src").mkdir()
    (repo / "src" / "fit.py").write_text("print('hi')\n")

    assert not dataset.is_clean(repo)
    assert ("??", "src/") in dataset.status(repo)


def test_status_honours_gitignore(repo: Path) -> None:
    """`.venv/` must never dirty a tree — a materialize would refuse to run
    in any project that had been synced."""
    (repo / ".gitignore").write_text(templates.gitignore())
    dataset.save(repo, [repo], "ignores")
    (repo / ".venv" / "bin").mkdir(parents=True)
    (repo / ".venv" / "bin" / "python").write_text("")

    assert dataset.is_clean(repo)


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


def test_the_save_the_data_readme_documents_actually_runs(repo: Path) -> None:
    """We tell researchers to type this, so run it. `git annex add` takes no
    `-A` — the flag `git add` needs and the one that reads like it belongs
    on both — and the whole line fails on a spelling nothing else checks."""
    (repo / "data" / "catalog.fits").write_bytes(b"\x00" * 64)
    command = next(
        line.strip()
        for line in templates.data_readme().splitlines()
        if "git annex add" in line
    )

    dataset._put_our_bin_first()
    assert subprocess.run(command, shell=True, cwd=repo, capture_output=True).returncode == 0

    assert _annexed(repo / "data" / "catalog.fits")
    assert dataset.is_clean(repo)


# ---- how git finds git-annex -----------------------------------------------


def test_our_bin_goes_to_the_front_of_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prepend, never append. `git annex` dispatches by searching PATH for
    a `git-annex` executable — a system copy winning would make the version
    the project's lock records a fiction."""
    ours = str(Path(sys.executable).parent)
    monkeypatch.setenv("PATH", "/somewhere/else")

    dataset._put_our_bin_first()
    assert os.environ["PATH"] == f"{ours}{os.pathsep}/somewhere/else"

    dataset._put_our_bin_first()
    assert os.environ["PATH"].count(ours) == 1


def test_git_dispatches_annex_after_the_prepend(repo: Path) -> None:
    """The claim the prepend makes, checked by the spelling git itself uses
    rather than by resolving `git-annex` ourselves."""
    assert "git-annex version:" in dataset._git(["annex", "version"], cwd=repo)


def test_git_annex_missing_is_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dataset.shutil, "which", lambda name, path=None: None)
    with pytest.raises(ProjectError, match="git-annex is required"):
        dataset.require_git_annex()
