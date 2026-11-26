"""Publishing: the commits to GitHub, the bytes to the project's store.

Real git and git-annex throughout, since whether a clone can fetch what
was published is a question only they answer. GitHub is local: its
repositories are bare ones in a folder that git is sent to in github.com's
place, and its REST API is answered from the same folder (`conftest.GitHub`).
The store is a directory, the one kind that needs no account. What is said
of Git LFS and ssh here is what lc tells git-annex, not what GitHub or a
cluster then does.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from conftest import CLUSTER_ID, FIRST, GitHub, clone_of, since

from lightcone.engine import dataset, materialize, project, publish
from lightcone.engine import github as engine_github
from lightcone.engine.project import ProjectError, child_env

_SPEC = """
spec_version: "0.1.0"
inputs:
  catalog: {path: data/catalog.txt}
outputs:
  fit:
    type: metric
    format: txt
    inputs: [catalog]
    recipe:
      command: cat {inputs.catalog} > {output} && echo fit >> {output}
  summary:
    type: report
    format: txt
    inputs: [fit]
    recipe:
      command: cat {inputs.fit} {inputs.fit} > {output}
"""

_URL = "github.com/alice/analysis"
_HTTPS = "https://github.com/alice/analysis"
_FIT, _SUMMARY, _CATALOG = "results/fit.txt", "results/summary.txt", "data/catalog.txt"


@pytest.fixture
def made(github: GitHub, analysis: Callable[..., Path], inline: None) -> Path:
    """A project with its results made, and nowhere online."""
    root = _scaffolded(analysis(_SPEC, {_CATALOG: "catalog v1\n"}))
    assert materialize.materialize(root, [], cluster_id=CLUSTER_ID).ok
    return root


def _scaffolded(root: Path) -> Path:
    """Commit what `lc init` adds to a project, so that it adds no file to a clone."""
    project.converge(root)
    dataset.save(root, [root], "what lc init scaffolds")
    return root


@pytest.fixture
def store(tmp_path: Path) -> str:
    """A directory for the project's data, not made yet."""
    return str(tmp_path / "store")


@pytest.fixture
def published(made: Path, store: str) -> Path:
    """The project, published privately with its data in a directory."""
    assert publish.publish(made, _URL, visibility="private", data=store).ok
    return made


def _join(tmp_path: Path, name: str = "bob") -> Path:
    """Clone the project from GitHub as a collaborator does: `git clone`, then `lc init`."""
    clone = tmp_path / name
    dataset._git(["clone", "-q", _HTTPS, str(clone)], cwd=tmp_path)
    project.converge(clone)
    return clone


def _remake(root: Path, word: str = "again") -> None:
    """Change what `fit` is made of, commit, and make the results again."""
    spec = root / "astra.yaml"
    spec.write_text(spec.read_text().replace("echo fit", f"echo {word}"))
    dataset.save(root, [spec], f"fit says {word}")
    assert materialize.materialize(root, [], cluster_id=CLUSTER_ID).ok


def _config(root: Path, key: str) -> str:
    return (dataset._ask(["config", "--get", key], cwd=root) or "").strip()


def _heads(github: GitHub) -> dict[str, str]:
    listed = dataset._git(
        ["for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads"],
        cwd=github.at("alice/analysis"),
    )
    return dict(line.split() for line in listed.splitlines())


# =============================================================================
# The first publication
# =============================================================================


def test_a_fresh_project_explains_the_commit_needed_before_publication(
    tmp_path: Path, github: GitHub, store: str, real_tools: None,
) -> None:
    """An unborn branch is a normal state after init, not an opaque git
    failure or a reason to create an empty repository online."""
    root = tmp_path / "fresh"
    project.converge(root)

    with pytest.raises(ProjectError, match="no commit to publish") as refused:
        publish.publish(root, _URL, visibility="private", data=store)

    assert "git status" in str(refused.value) and "git commit" in str(refused.value)
    assert github.calls == []
    assert not github.at("alice/analysis").exists()
    assert not Path(store).exists()
    assert dataset._ask(["rev-parse", "--verify", "HEAD"], cwd=root) is None

    dataset.save(root, [root], "Set up analysis")
    report = publish.publish(root, _URL, visibility="private", data=store)
    assert report.ok and report.created and report.published


def test_the_first_publication_creates_the_repository_and_declares_the_store(
    made: Path, store: str, github: GitHub
) -> None:
    report = publish.publish(made, _URL, visibility="private", data=store)

    assert report.ok and report.published
    assert (report.target, report.visibility, report.created) == (_URL, "private", True)
    assert (report.store, report.declared) == (store, True)
    assert {name: kind for name, kind in report.sent.items() if kind != "file"} == {
        _FIT: "result",
        _SUMMARY: "result",
        _CATALOG: "input",
    }
    assert report.commits > 0 and report.commit == dataset.head(made)
    # GitHub was asked for a private repository under the token's own user.
    assert ("POST", "/user/repos", {"name": "analysis", "private": True}) in github.calls
    # It holds the branch and git-annex's log, and nothing else.
    assert _heads(github) == {
        "main": dataset.head(made),
        "git-annex": dataset._git(["rev-parse", "git-annex"], cwd=made).strip(),
    }


def test_a_repository_lc_creates_shows_the_analysis_first(
    made: Path, store: str, github: GitHub, tmp_path: Path
) -> None:
    """Whatever GitHub would choose among the branch and git-annex's log,
    and whatever it calls a new repository's first branch."""
    dataset._git(["branch", "-m", "main", "master"], cwd=made)

    report = publish.publish(made, _URL, visibility="private", data=store)

    assert report.ok and report.shown_first == "master"
    assert github.default_branch("alice/analysis") == "master"
    dataset._git(["clone", "-q", _HTTPS, str(tmp_path / "bob")], cwd=tmp_path)
    assert (tmp_path / "bob" / "astra.yaml").exists()


def test_a_repository_that_shows_git_annexs_log_first_is_set_right_by_the_next_publication(
    published: Path, github: GitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    """As GitHub left it where lc pushed `master` and `git-annex` to a new
    repository (measured), and with nothing else to send."""
    github.show_first("alice/analysis", "git-annex")

    report = publish.publish(published)

    assert report.ok and report.shown_first == "main" and not report.commits
    assert github.default_branch("alice/analysis") == "main"
    assert publish.publish(published).shown_first == ""
    # Without a token lc cannot, and says what can.
    github.show_first("alice/analysis", "git-annex")
    monkeypatch.delenv("GH_TOKEN")
    report = publish.publish(published)
    assert report.ok and not report.shown_first
    assert "make `main` its default branch in the repository's settings" in report.warnings[-1]


def test_a_publication_leaves_the_clone_set_up_to_pull_and_for_datalad(
    published: Path,
) -> None:
    """`git pull` alone takes in what others publish, git-annex leaves
    GitHub alone, and DataLad sends bytes before commits as lc does."""
    assert _config(published, "remote.origin.url") == _HTTPS
    assert _config(published, "branch.main.remote") == "origin"
    assert _config(published, "remote.origin.annex-ignore") == "true"
    assert _config(published, "remote.origin.datalad-publish-depends") == dataset.STORE


def test_the_store_is_a_shared_git_annex_repository_declared_for_every_clone(
    published: Path, store: str
) -> None:
    assert _config(Path(store), "core.bare") == "true"
    assert _config(Path(store), "core.sharedrepository") in ("1", "group")
    assert _config(Path(store), "annex.uuid")
    [declared] = dataset.stores(published)
    its_id = _config(Path(store), "annex.uuid")
    assert declared == dataset.Store("directory", store, its_id, {dataset.STORE: (store, True)})
    assert f"location={store}" in dataset._git(["show", "git-annex:remote.log"], cwd=published)
    assert "autoenable=true" in dataset._git(["show", "git-annex:remote.log"], cwd=published)


def _connections(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """What git is asked to do with GitHub from here on, one verb per connection."""
    real, made = project._run, []

    def counted(argv: list[str], **kwargs: object) -> object:
        made.extend(verb for verb in ("ls-remote", "fetch", "push") if verb in argv)
        return real(argv, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(project, "_run", counted)
    return made


def test_publishing_again_sends_nothing_and_says_so(
    published: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In one connection to GitHub, which costs seconds over ssh: what it
    holds is what this clone last fetched or pushed."""
    connections = _connections(monkeypatch)

    report = publish.publish(published)

    assert report.ok and report.published
    assert not (report.sent or report.commits or report.created or report.declared)
    assert connections == ["ls-remote"]


def test_a_log_someone_else_published_is_fetched_before_anything_is_sent(
    published: Path, store: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It says what is in the store already, and the push then goes at once."""
    assert publish.publish(_join(tmp_path), confirm=store).confirmed
    _remake(published)
    connections = _connections(monkeypatch)

    report = publish.publish(published)

    assert report.ok and report.commits == 3
    assert connections == ["ls-remote", "fetch", "push"]


def test_only_what_changed_is_sent_next(published: Path, github: GitHub) -> None:
    _remake(published)

    report = publish.publish(published)

    assert report.sent == {_FIT: "result", _SUMMARY: "result"}
    assert report.commits == 3  # the edit, and one commit per result
    assert _heads(github)["main"] == dataset.head(published)


def test_an_empty_repository_someone_made_is_published_to_without_a_token(
    made: Path, store: str, github: GitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a token lc creates nothing, and needs none to publish to a
    repository that exists: git has its own credentials. A repository git
    reaches that GitHub shows to nobody is private."""
    github.new("alice/analysis")
    monkeypatch.delenv("GH_TOKEN")

    report = publish.publish(made, _URL, data=store)

    assert report.ok and not report.created
    assert report.visibility == "private"
    assert not [call for call in github.calls if call[0] != "GET"]


def test_a_repository_is_created_public_only_with_a_license(
    made: Path, store: str, github: GitHub
) -> None:
    with pytest.raises(ProjectError, match="needs a license"):
        publish.publish(made, _URL, visibility="public", data=store)
    assert not github.at("alice/analysis").exists()

    _license(made)
    report = publish.publish(made, _URL, visibility="public", data=store)

    assert (report.created, report.visibility, report.license) == (True, "public", "CC-BY-4.0")
    assert github.visibility("alice/analysis") == "public"


def _license(root: Path) -> None:
    pyproject = root / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text().replace("[project]", '[project]\nlicense = "CC-BY-4.0"')
    )
    dataset.save(root, [pyproject], "license")


# =============================================================================
# A clone from GitHub
# =============================================================================


def test_a_clone_from_github_learns_the_store_and_fetches_from_it(
    published: Path, tmp_path: Path
) -> None:
    """Nothing is configured in a clone: `lc init` sets up git-annex, which
    enables the store the project declares, and reading needs no
    confirmation."""
    clone = _join(tmp_path)

    found = materialize.status(clone).results
    assert [(r.result, r.state, r.unfetched, r.remotes) for r in found] == [
        ("fit", "ok", True, [dataset.STORE]),
        ("summary", "ok", True, [dataset.STORE]),
    ]
    fetched = materialize.fetch(clone, ["fit", "catalog"])
    assert (fetched.fetched, fetched.inputs, fetched.ok) == (["catalog", "fit"], ["catalog"], True)
    assert (clone / _FIT).read_text() == "catalog v1\nfit\n"
    assert (clone / _CATALOG).read_text() == "catalog v1\n"
    assert _config(clone, "remote.origin.datalad-publish-depends") == dataset.STORE


def test_a_clone_that_has_yet_to_reach_the_store_takes_nothing_for_lost(
    published: Path, store: str, tmp_path: Path
) -> None:
    """git-annex notes which repository a store in a directory is only once
    it has looked there (measured). Before that, the declaration says:
    what the log places in the store is to fetch, never to make again."""
    away = Path(store).rename(tmp_path / "unmounted")
    clone = _join(tmp_path)

    found = materialize.status(clone).results
    assert [(r.state, r.unfetched, r.remotes) for r in found] == [("ok", True, [dataset.STORE])] * 2
    assert materialize.check(clone, []).up_to_date
    unreachable = materialize.fetch(clone, ["fit"])
    assert not unreachable.ok and "lc materialize" not in unreachable.failed["fit"]
    assert [r.state for r in materialize.status(clone).results] == ["ok", "ok"]

    away.rename(store)
    assert materialize.fetch(clone, ["fit"]).fetched == ["fit"]


def test_a_clone_does_not_publish_to_a_store_it_has_not_confirmed(
    published: Path, store: str, tmp_path: Path, github: GitHub
) -> None:
    """Where the bytes go is said by whoever can push to the repository:
    lc shows it, and sends there only once the user has repeated it."""
    clone = _join(tmp_path)
    _remake(clone)
    before = _heads(github)

    with pytest.raises(ProjectError) as refused:
        publish.publish(clone)
    assert f"lc publish --confirm-data {store}" in str(refused.value)
    with pytest.raises(ProjectError, match="is not where this project's data goes"):
        publish.publish(clone, confirm="/somewhere/else")

    assert _heads(github) == before
    assert _config(clone, f"remote.{dataset.STORE}.annex-verified") == "automatic"
    assert not publish.publish(clone, check=True).published


def test_a_clone_that_confirms_the_store_publishes_and_the_first_takes_it_in(
    published: Path, store: str, tmp_path: Path
) -> None:
    """The lifecycle whole: published from one clone, fetched, extended
    and republished from another, and taken back in by `git pull`."""
    clone = _join(tmp_path)
    _remake(clone)

    report = publish.publish(clone, confirm=store)

    assert report.ok and report.published and report.confirmed
    assert report.sent == {_FIT: "result", _SUMMARY: "result"}
    assert _config(clone, f"remote.{dataset.STORE}.annex-verified") == "verified"
    assert publish.publish(clone).ok  # confirmed once, for good

    dataset._git(["pull", "-q", "--ff-only"], cwd=published)
    states = [(r.result, r.state, r.unfetched) for r in materialize.status(published).results]
    assert states == [("fit", "ok", True), ("summary", "ok", True)]
    assert materialize.fetch(published, []).fetched == ["fit", "summary"]
    assert (published / _FIT).read_text() == "catalog v1\nagain\n"


def test_lc_confirms_no_declaration_but_the_one_it_writes_for_that_place(
    published: Path, tmp_path: Path
) -> None:
    """What is confirmed is what git-annex shows, not what the clone's
    configuration says: a declaration that names another place than the
    one the user repeated is not confirmed."""
    clone = _join(tmp_path)
    _remake(clone)
    dataset._git(["config", f"remote.{dataset.STORE}.url", "/somewhere/else"], cwd=clone)

    with pytest.raises(ProjectError, match="is not what lc declares for that place"):
        publish.publish(clone, confirm="/somewhere/else")

    assert _config(clone, f"remote.{dataset.STORE}.annex-verified") == "automatic"


def _declared_by_hand(root: Path, name: str, directory: Path) -> None:
    """Declare a store in *directory* as someone does with git-annex, under *name*."""
    dataset._git(["init", "-q", "--bare", "--shared=group", str(directory)], cwd=root)
    dataset._git(["annex", "init", "-q"], cwd=directory)
    dataset._git(
        ["annex", "initremote", name, "type=git", f"location={directory}", "autoenable=true"],
        cwd=root,
    )


def test_a_store_declared_by_hand_under_another_name_is_the_projects_store(
    made: Path, store: str, tmp_path: Path
) -> None:
    """A store is what git-annex's branch declares, not what it is called:
    lc asks for no `--data`, declares nothing more, and sends there. A
    clone reads from it, and confirms it as it would one lc declared."""
    _declared_by_hand(made, "shared", Path(store))

    report = publish.publish(made, _URL, visibility="private")

    assert report.ok and report.published and not report.declared
    assert (report.store, report.sent[_FIT]) == (store, "result")
    assert [list(each.remotes) for each in dataset.stores(made)] == [["shared"]]
    assert _config(made, "remote.origin.datalad-publish-depends") == "shared"

    clone = _join(tmp_path)
    found = materialize.status(clone).results
    assert [(r.state, r.unfetched, r.remotes) for r in found] == [("ok", True, ["shared"])] * 2
    assert materialize.fetch(clone, []).fetched == ["fit", "summary"]
    _remake(clone)
    with pytest.raises(ProjectError) as refused:
        publish.publish(clone)
    assert f"lc publish --confirm-data {store}" in str(refused.value)
    confirmed = publish.publish(clone, confirm=store)
    assert confirmed.ok and confirmed.published and confirmed.confirmed
    assert _config(clone, "remote.shared.annex-verified") == "verified"


def test_a_store_of_a_kind_lc_does_not_publish_to_is_not_given_a_second(
    made: Path, store: str, tmp_path: Path, github: GitHub
) -> None:
    """A project whose bytes go to a bucket someone declared has a store:
    declaring another beside it would leave every clone with two, one of
    them never confirmed. Refused before anything is created or declared."""
    (tmp_path / "bucket").mkdir()
    dataset._git(
        ["annex", "initremote", "bucket", "type=directory", f"directory={tmp_path / 'bucket'}",
         "encryption=none", "autoenable=true"],
        cwd=made,
    )  # fmt: skip

    with pytest.raises(ProjectError, match="a kind lc does not publish to") as refused:
        publish.publish(made, _URL, visibility="private", data=store)

    assert "`bucket`" in str(refused.value)
    assert not github.at("alice/analysis").exists() and not Path(store).exists()
    assert dataset.STORE not in dataset._git(["show", "git-annex:remote.log"], cwd=made)


def test_a_project_that_declares_two_stores_is_published_to_neither(
    published: Path, tmp_path: Path, github: GitHub
) -> None:
    """Which of two stores the bytes go to would be a guess. Reading takes
    from both."""
    _declared_by_hand(published, "second", tmp_path / "second")
    _remake(published)
    before = _heads(github)

    with pytest.raises(ProjectError, match="more than one data store") as refused:
        publish.publish(published)
    assert dataset.STORE in str(refused.value) and "second" in str(refused.value)
    with pytest.raises(ProjectError, match="more than one data store"):
        publish.publish(published, check=True)

    assert _heads(github) == before
    assert len(dataset.stores(published)) == 2


def test_a_store_a_pull_brings_is_enabled_before_anything_is_read(
    made: Path, store: str, tmp_path: Path
) -> None:
    """git-annex enables a declared store when it is set up in a clone, and
    not for one a pull brings later (measured): left so, what the store
    holds would read as held nowhere."""
    colleague = clone_of(made, tmp_path)
    assert publish.publish(made, _URL, visibility="private", data=store).ok
    dataset._git(["pull", "-q"], cwd=colleague)
    assert dataset.stores(colleague) == []

    remotes = [r.remotes for r in materialize.status(colleague).results]

    assert remotes == [["origin", dataset.STORE]] * 2
    assert _config(colleague, f"remote.{dataset.STORE}.annex-verified") == "automatic"


# =============================================================================
# Refusals, each before anything is created or sent
# =============================================================================


def test_a_project_with_nowhere_to_publish_to_is_told_how(made: Path) -> None:
    with pytest.raises(ProjectError) as refused:
        publish.publish(made)

    assert "lc publish github.com/OWNER/analysis --private" in str(refused.value)


def test_only_a_repository_on_github_is_published_to(made: Path) -> None:
    with pytest.raises(ProjectError, match="not a repository on GitHub"):
        publish.publish(made, "https://gitlab.com/alice/analysis", visibility="private")


def test_the_choices_no_tool_makes_are_asked_before_a_repository_is_created(
    made: Path, store: str, github: GitHub
) -> None:
    with pytest.raises(ProjectError) as both:
        publish.publish(made, _URL)
    with pytest.raises(ProjectError) as where:
        publish.publish(made, _URL, visibility="private")
    with pytest.raises(ProjectError) as who:
        publish.publish(made, _URL, data=store)

    assert "who can read it and where its data lives" in str(both.value)
    assert "--private" in str(both.value) and "--data github-lfs" in str(both.value)
    assert "--private" not in str(where.value) and "--data github-lfs" in str(where.value)
    assert "--private" in str(who.value) and "--data github-lfs" not in str(who.value)
    assert not github.at("alice/analysis").exists()
    assert not dataset.remote_url(made, "origin") and not Path(store).exists()


def test_without_a_token_lc_creates_nothing_and_says_where_to(
    made: Path, store: str, github: GitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GH_TOKEN")

    with pytest.raises(ProjectError) as refused:
        publish.publish(made, _URL, visibility="private", data=store)

    assert "https://github.com/new" in str(refused.value)
    assert "GH_TOKEN" in str(refused.value)
    assert not github.calls


@pytest.mark.parametrize("data", ["store", "./store", "s3://bucket/prefix", "ssh://host", "/"])
def test_data_names_git_lfs_or_a_directory_and_nothing_else(
    made: Path, github: GitHub, data: str
) -> None:
    with pytest.raises(ProjectError, match="names no place lc keeps a project's data in"):
        publish.publish(made, _URL, visibility="private", data=data)

    assert not github.at("alice/analysis").exists()


def test_a_store_is_not_made_where_something_else_is(
    made: Path, tmp_path: Path, github: GitHub
) -> None:
    """The store is made before the repository is, so a directory that
    cannot hold it is refused with nothing created online."""
    (tmp_path / "taken").mkdir()
    (tmp_path / "taken" / "notes.txt").write_text("mine\n")

    with pytest.raises(ProjectError, match="is not empty, and is not a store lc made"):
        publish.publish(made, _URL, visibility="private", data=str(tmp_path / "taken"))

    assert not github.at("alice/analysis").exists()
    assert dataset.stores(made) == [] and not dataset.remote_url(made, "origin")
    assert sorted(path.name for path in (tmp_path / "taken").iterdir()) == ["notes.txt"]


def test_the_store_is_chosen_once(published: Path, tmp_path: Path, store: str) -> None:
    with pytest.raises(ProjectError, match="lc does not move it"):
        publish.publish(published, data=str(tmp_path / "another"))
    with pytest.raises(ProjectError, match="lc does not move it"):
        publish.publish(published, data="github-lfs")

    assert publish.publish(published, data=store).ok  # naming it again changes nothing


def test_a_project_has_one_repository(published: Path) -> None:
    with pytest.raises(ProjectError, match="a project has one repository"):
        publish.publish(published, "github.com/alice/another")

    assert publish.publish(published, _HTTPS).ok


def test_a_clone_of_a_colleagues_directory_has_nowhere_to_publish_to(
    made: Path, tmp_path: Path
) -> None:
    """Its `origin` is that directory, and renaming it is a step in plain git."""
    colleague = clone_of(made, tmp_path)

    with pytest.raises(ProjectError) as refused:
        publish.publish(colleague, _URL, visibility="private")

    assert "git remote rename origin <name>" in str(refused.value)


def test_an_origin_elsewhere_is_named_without_the_token_its_address_carries(
    made: Path,
) -> None:
    dataset._git(["remote", "add", "origin", "https://bob:s3cret@gitlab.com/lab/a.git"], cwd=made)

    with pytest.raises(ProjectError) as refused:
        publish.publish(made, _URL, visibility="private")

    assert "https://gitlab.com/lab/a.git" in str(refused.value)
    assert "s3cret" not in str(refused.value)


def test_a_branch_is_published_not_a_commit(published: Path) -> None:
    dataset._git(["checkout", "-q", "--detach"], cwd=published)

    with pytest.raises(ProjectError, match="lc publishes a branch"):
        publish.publish(published)


def test_commits_this_clone_lacks_are_pulled_first_never_merged(
    published: Path, store: str, tmp_path: Path, github: GitHub
) -> None:
    clone = _join(tmp_path)
    _remake(clone)
    assert publish.publish(clone, confirm=store).ok
    _remake(published, "otherwise")
    theirs = _heads(github)["main"]

    with pytest.raises(ProjectError, match="`git pull`, then `lc publish`"):
        publish.publish(published)

    assert _heads(github)["main"] == theirs


def test_a_repository_holding_another_history_is_not_published_to(
    made: Path, store: str, github: GitHub, tmp_path: Path
) -> None:
    """As one made with a README at github.com/new is."""
    other = tmp_path / "other"
    other.mkdir()
    for argv in (
        ["init", "-q"],
        ["commit", "-q", "--allow-empty", "-m", "Initial commit"],
        ["push", "-q", str(github.new("alice/analysis")), "main"],
    ):
        dataset._git(argv, cwd=other)

    with pytest.raises(ProjectError, match="publish to an empty repository"):
        publish.publish(made, _URL, data=store)

    assert not dataset.remote_url(made, "origin")


def test_confirming_is_for_a_store_the_project_declares(made: Path, github: GitHub) -> None:
    github.new("alice/analysis")

    with pytest.raises(ProjectError, match="it declares none yet"):
        publish.publish(made, _URL, confirm="/a/store")


def test_a_clone_git_annex_was_never_set_up_in_is_sent_to_lc_init(
    published: Path, tmp_path: Path
) -> None:
    clone = tmp_path / "bare-clone"
    dataset._git(["clone", "-q", _HTTPS, str(clone)], cwd=tmp_path)

    with pytest.raises(ProjectError, match="`lc init` sets it up"):
        publish.publish(clone)


def test_an_older_git_annex_which_would_send_unconfirmed_is_refused(
    published: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = project._run

    def older(argv: list[str], **kwargs: object) -> object:
        if argv == ["git", "annex", "version", "--raw"]:
            return MagicMock(returncode=0, stdout="10.20260717-g698698a3c")
        return real(argv, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(project, "_run", older)

    with pytest.raises(ProjectError, match="10.20261026 or later"):
        publish.publish(published)


# =============================================================================
# Going public
# =============================================================================


def test_going_public_takes_a_license_and_is_never_undone(
    published: Path, github: GitHub
) -> None:
    with pytest.raises(ProjectError, match="needs a license"):
        publish.publish(published, visibility="public")
    assert github.visibility("alice/analysis") == "private"

    _license(published)
    report = publish.publish(published, visibility="public")

    assert (report.made_public, report.visibility, report.license) == (True, "public", "CC-BY-4.0")
    assert github.visibility("alice/analysis") == "public"
    assert github.calls[-1] == ("PATCH", "/repos/alice/analysis", {"private": False})
    with pytest.raises(ProjectError, match="lc offers no way back"):
        publish.publish(published, visibility="private")
    assert not publish.publish(published, visibility="public").made_public


def test_a_license_that_is_not_committed_is_not_one_the_public_gets(published: Path) -> None:
    pyproject = published / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text().replace("[project]", '[project]\nlicense = "CC-BY-4.0"')
    )

    with pytest.raises(ProjectError, match="needs a license"):
        publish.publish(published, visibility="public")


def test_going_public_takes_a_token(
    published: Path, monkeypatch: pytest.MonkeyPatch, github: GitHub
) -> None:
    _license(published)
    monkeypatch.delenv("GH_TOKEN")
    (github.at("alice/analysis") / "visibility").write_text("private")

    with pytest.raises(ProjectError):
        publish.publish(published, visibility="public")

    assert github.visibility("alice/analysis") == "private"


# =============================================================================
# --check
# =============================================================================


def test_check_says_a_project_is_not_online(made: Path, github: GitHub) -> None:
    report = publish.publish(made, check=True)

    assert not report.target and not report.published
    assert not github.calls


def test_check_lists_what_is_not_published_and_sends_nothing(
    published: Path, github: GitHub
) -> None:
    assert publish.publish(published, check=True).published
    _remake(published)
    before = _heads(github)

    report = publish.publish(published, check=True)

    assert not report.published and report.ok
    assert report.unsent == {_FIT: "result", _SUMMARY: "result"}
    assert (report.unpushed, report.commits, report.sent) == (3, 0, {})
    assert _heads(github) == before


def test_check_needs_no_confirmation(published: Path, tmp_path: Path) -> None:
    clone = _join(tmp_path)

    assert publish.publish(clone, check=True).published


def test_uncommitted_changes_are_counted_and_left_out(published: Path, github: GitHub) -> None:
    (published / "notes.md").write_text("to do\n")

    report = publish.publish(published)

    assert report.uncommitted == 1 and report.published
    assert _heads(github)["main"] == dataset.head(published)


# =============================================================================
# What a publication sends
# =============================================================================


def test_a_plain_git_push_breaks_nothing_and_the_next_publication_sends_the_bytes(
    published: Path, tmp_path: Path
) -> None:
    """Commits without their bytes: every clone sees those results missing,
    until whoever holds the bytes publishes."""
    _remake(published)
    dataset._git(["push", "-q", "origin", "main", "git-annex"], cwd=published)
    clone = _join(tmp_path)
    assert [r.state for r in materialize.status(clone).results] == ["missing", "missing"]
    # A clone that does not hold them cannot send them, and says which.
    answer = publish.publish(clone, check=True)
    assert sorted(answer.nowhere) == [_FIT, _SUMMARY] and not answer.published

    assert publish.publish(published).sent == {_FIT: "result", _SUMMARY: "result"}

    dataset._git(["pull", "-q"], cwd=clone)
    states = [(r.state, r.unfetched) for r in materialize.status(clone).results]
    assert states == [("ok", True), ("ok", True)]


def test_bytes_that_were_not_sent_keep_the_commits_back(
    published: Path, github: GitHub
) -> None:
    """A commit must not reach a clone before the bytes it names. Only what
    git-annex says it sent was sent: for a key its log places here and its
    annex does not hold, it answers nothing and exits 0 (measured)."""
    _remake(published)
    before = _heads(github)
    key = dataset.annexed(published, dataset.head(published))[_FIT]
    kept = Path(dataset._git(["annex", "contentlocation", key], cwd=published).strip())
    (published / kept).parent.chmod(0o700)
    (published / kept).unlink()

    report = publish.publish(published)

    assert not report.ok and not report.published
    assert list(report.failed) == [_FIT] and report.sent == {_SUMMARY: "result"}
    assert report.commits == 0 and _heads(github) == before
    # Publishing again sends only what is still to send, and still no commit.
    again = publish.publish(published)
    assert list(again.failed) == [_FIT] and not again.sent and _heads(github) == before


def test_git_lfs_is_not_sent_a_file_it_takes_on_no_plan() -> None:
    """Refused before anything is created or sent, naming the file."""
    files = {"results/cube.h5": "SHA256E-s6442450944--0f3a.h5", _FIT: "SHA256E-s9--1c.txt"}

    publish._require_lfs_takes({_FIT: "result"}, files)
    with pytest.raises(ProjectError) as refused:
        publish._require_lfs_takes({"results/cube.h5": "result", _FIT: "result"}, files)

    assert "results/cube.h5 (6.0 GB)" in str(refused.value)
    assert _FIT not in str(refused.value)


def test_what_the_analysis_reads_at_a_commit_it_declares_is_sent_too(
    github: GitHub,
    analysis: Callable[..., Path],
    inline: None,
    store: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A result read at an earlier commit is no branch's current version:
    sent with the rest, or no clone could make what reads it."""
    root = _scaffolded(analysis(FIRST))
    at = since(root)
    assert materialize.materialize(root, [], cluster_id=CLUSTER_ID).ok

    report = publish.publish(root, _URL, visibility="private", data=store)

    assert report.sent[f"{at}:results/fit.txt"] == "result"
    clone = _join(tmp_path)
    spec = clone / "astra.yaml"
    spec.write_text(spec.read_text().replace("cat {inputs}", "cat {inputs} {inputs}"))
    dataset.save(clone, [spec], "compare twice")
    real, ran = project._run, []

    def recording(argv: list[str], **kwargs: object) -> object:
        ran.append(argv)
        return real(argv, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(project, "_run", recording)
    assert materialize.materialize(clone, ["compare"], cluster_id=CLUSTER_ID).made == ["compare"]
    assert (clone / "results/compare.txt").read_text() == "second\nfirst\n" * 2
    # Local history uses a detached worktree and the project's own annex.
    assert any("worktree" in argv and "add" in argv for argv in ran)
    assert not any("clone" in argv for argv in ran)
    fetched = [argv for argv in ran if "get" in argv and "--batch-keys" in argv]
    assert fetched
    assert all("core.sshCommand=ssh -o BatchMode=yes" in argv for argv in fetched)


def test_an_analysis_that_cannot_be_read_does_not_keep_the_commit_offline(
    published: Path, github: GitHub
) -> None:
    spec = published / "astra.yaml"
    spec.write_text(spec.read_text() + "  broken: {type: nothing}\n")
    dataset.save(published, [spec], "work in progress")

    report = publish.publish(published)

    assert report.ok and report.commits == 1
    assert any("the analysis could not be read" in warning for warning in report.warnings)
    assert _heads(github)["main"] == dataset.head(published)


def test_results_still_to_make_are_said_and_the_commit_published(
    published: Path,
) -> None:
    spec = published / "astra.yaml"
    spec.write_text(spec.read_text().replace("echo fit", "echo later"))
    dataset.save(published, [spec], "change, not made yet")

    report = publish.publish(published)

    assert report.ok and report.commits == 1
    assert any("2 result(s)" in warning and "stale or missing" in warning
               for warning in report.warnings)  # fmt: skip


def test_someone_publishing_in_between_only_costs_a_second_push(
    published: Path, store: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GitHub refuses git-annex's log when another clone sent its own
    since: theirs is taken in, never overwritten, and the push tried again."""
    clone = _join(tmp_path)
    assert publish.publish(clone, confirm=store).confirmed
    _remake(published)
    real, raced = project._run, []

    def racing(argv: list[str], **kwargs: object) -> object:
        if "push" in argv and not raced:
            raced.append(True)
            # The other clone's log reaches GitHub between this one's fetch and push.
            dataset._git(["annex", "describe", "here", "bob"], cwd=clone)
            dataset.record_copies(clone)
            dataset._git(["push", "-q", "origin", "git-annex"], cwd=clone)
        return real(argv, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(project, "_run", racing)

    report = publish.publish(published)

    assert report.ok and raced
    monkeypatch.setattr(project, "_run", real)
    dataset._git(["fetch", "-q", "origin"], cwd=clone)
    assert "bob" in dataset._git(["show", "origin/git-annex:uuid.log"], cwd=clone)
    assert dataset._git(["rev-parse", "origin/main"], cwd=clone).strip() == dataset.head(published)


def test_a_push_github_refuses_is_said_in_its_words_with_what_publishing_takes(
    published: Path, github: GitHub
) -> None:
    """As for someone with no write access: what was sent to the store
    stays, and nothing of the branch moves."""
    hook = github.at("alice/analysis") / "hooks" / "pre-receive"
    hook.write_text(
        "#!/bin/sh\necho 'Permission to alice/analysis.git denied to bob.' >&2\nexit 1\n"
    )
    hook.chmod(0o755)
    _remake(published)
    before = _heads(github)

    with pytest.raises(ProjectError) as refused:
        publish.publish(published)

    assert "Permission to alice/analysis.git denied to bob." in str(refused.value)
    assert "Publishing takes write access to it" in str(refused.value)
    assert _heads(github) == before
    hook.unlink()
    assert publish.publish(published).sent == {}  # the bytes were already in the store


# =============================================================================
# What lc tells git-annex of a store it cannot reach from here
# =============================================================================


def _recorded(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Record what lc would run, in place of running it."""
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: object) -> MagicMock:
        calls.append(argv)
        said = "Test <t@example.com> 0 +0000" if argv[1:2] == ["var"] else ""
        return MagicMock(returncode=0, stdout=said, stderr="")

    monkeypatch.setattr(project, "_run", run)
    return calls


def test_git_lfs_is_declared_once_at_the_repositorys_two_addresses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One store, reached over https or ssh, so each clone uses the
    credentials it has; enabled by git-annex in every clone."""
    calls = _recorded(monkeypatch)
    target = engine_github.Repository("alice", "analysis")
    wanted = publish._wanted("github-lfs", target)

    publish._make_store(tmp_path, wanted)  # GitHub keeps it: nothing to make
    publish._declare(tmp_path, wanted, target)

    assert [argv[argv.index("annex") :] for argv in calls if "initremote" in argv] == [
        ["annex", "initremote", "origin-storage", "type=git-lfs",
         "url=https://github.com/alice/analysis", "encryption=none", "autoenable=true"],
        ["annex", "initremote", "origin-storage-ssh", "--sameas=origin-storage",
         "type=git-lfs", "url=git@github.com:alice/analysis.git", "autoenable=true"],
    ]  # fmt: skip
    assert not [argv for argv in calls if argv[0] != "git"]


def test_a_directory_reached_by_ssh_gets_its_store_made_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the ssh the user reaches remotes with, in batch mode."""
    calls = _recorded(monkeypatch)
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -i /keys/cluster")
    target = engine_github.Repository("alice", "analysis")
    where = "ssh://alice@dtn.example.org:2222/global/cfs/lcr/store"
    wanted = publish._wanted(where, target)

    publish._make_store(tmp_path, wanted)
    publish._declare(tmp_path, wanted, target)

    (ssh,) = [argv for argv in calls if argv[0] == "ssh"]
    assert ssh[:-1] == [
        "ssh", "-i", "/keys/cluster", "-o", "BatchMode=yes", "-p", "2222",
        "alice@dtn.example.org",
    ]  # fmt: skip
    assert ssh[-1].endswith("sh /global/cfs/lcr/store Test t@example.com")
    assert "git init -q --bare --shared=group" in ssh[-1]
    (declared,) = [argv for argv in calls if "initremote" in argv]
    assert declared[declared.index("annex") :] == [
        "annex", "initremote", "origin-storage", "type=git", f"location={where}",
        "autoenable=true",
    ]  # fmt: skip


def test_git_lfs_is_confirmed_address_by_address_as_lc_declares_it() -> None:
    store = dataset.Store(
        "github-lfs",
        "https://github.com/alice/analysis",
        "1111",
        {
            dataset.STORE: ("https://github.com/alice/analysis", False),
            dataset.STORE_SSH: ("git@github.com:alice/analysis.git", False),
        },
    )
    over_https = engine_github.Repository("alice", "analysis")

    assert publish._settings(store, "git@github.com:alice/analysis.git") == {
        "type": "git-lfs",
        "url": "git@github.com:alice/analysis.git",
        "autoenable": "true",
        "encryption": "none",
    }
    assert publish._described(store, over_https) == "GitHub LFS of github.com/alice/analysis"
    with pytest.raises(ProjectError) as refused:
        publish._require_same_store(store, None, "", over_https, check=False)
    assert "lc publish --confirm-data https://github.com/alice/analysis" in str(refused.value)
    publish._require_same_store(store, None, "github.com/alice/analysis", over_https, check=False)


def test_git_lfs_of_another_repository_is_not_this_projects_store() -> None:
    """A declaration that sends the bytes to someone else's repository, at
    either address, is refused whatever is confirmed."""
    store = dataset.Store(
        "github-lfs",
        "https://github.com/alice/analysis",
        "1111",
        {
            dataset.STORE: ("https://github.com/alice/analysis", True),
            dataset.STORE_SSH: ("git@github.com:mallory/analysis.git", True),
        },
    )

    with pytest.raises(ProjectError, match="one repository"):
        publish._require_same_store(
            store, None, "", engine_github.Repository("alice", "analysis"), check=True
        )


def test_a_key_gives_the_size_of_its_bytes() -> None:
    assert publish._size("SHA256E-s6442450944--0f3a.h5") == 6 * 1024**3
    assert publish._size("SHA256E-s6442450944--0f3a.h5") > publish._LFS_LIMIT
    assert publish._size("URL--http&c%%example.org") == 0


# =============================================================================
# DataLad, without lc
# =============================================================================


def _datalad(*argv: str, cwd: Path) -> None:
    done = subprocess.run(
        ["datalad", *argv], cwd=cwd, env=child_env(), capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr + done.stdout


def test_a_datalad_clone_gets_a_published_result(published: Path, tmp_path: Path) -> None:
    """The store is where DataLad looks: nothing of lc's is needed to read
    a project."""
    _datalad("clone", _HTTPS, "with-datalad", cwd=tmp_path)

    _datalad("get", _FIT, cwd=tmp_path / "with-datalad")

    assert (tmp_path / "with-datalad" / _FIT).read_text() == "catalog v1\nfit\n"


def test_datalad_push_sends_bytes_before_commits_as_lc_publish_does(
    published: Path, tmp_path: Path, github: GitHub
) -> None:
    _remake(published)

    _datalad("push", "--to", "origin", cwd=published)

    assert _heads(github)["main"] == dataset.head(published)
    assert publish.publish(published, check=True).published
    clone = _join(tmp_path)
    assert materialize.fetch(clone, ["fit"]).fetched == ["fit"]
    assert (clone / _FIT).read_text() == "catalog v1\nagain\n"
