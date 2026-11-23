"""GitHub, as far as publishing needs it: which repository a URL names, and
what lc asks of the REST API. Nothing here reaches the network: the API is
answered by the test."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lightcone.engine import github
from lightcone.engine.project import ProjectError


@pytest.mark.parametrize(
    ("url", "ssh"),
    [
        ("github.com/lcr/analysis", False),
        ("https://github.com/lcr/analysis", False),
        ("https://github.com/lcr/analysis.git", False),
        ("https://github.com/lcr/analysis/", False),
        ("git@github.com:lcr/analysis.git", True),
        ("git@github.com:lcr/analysis", True),
        ("ssh://git@github.com/lcr/analysis.git", True),
    ],
)
def test_a_repository_is_read_from_each_way_people_write_it(url: str, ssh: bool) -> None:
    found = github.parse(url)

    assert found == github.Repository("lcr", "analysis", ssh=ssh)
    assert found.slug == "github.com/lcr/analysis"
    assert found.https == "https://github.com/lcr/analysis"
    assert found.over_ssh == "git@github.com:lcr/analysis.git"
    assert found.url == (found.over_ssh if ssh else found.https)


@pytest.mark.parametrize(
    "url",
    [
        "/global/cfs/cdirs/lcr/alice/analysis",
        "../analysis",
        "https://gitlab.com/lcr/analysis",
        "https://github.com/lcr",
        "https://github.com/lcr/analysis/tree/main",
        "http://github.com/lcr/analysis",
        "git@github.com/lcr/analysis",
        "",
    ],
)
def test_anything_else_is_no_repository_on_github(url: str) -> None:
    assert github.parse(url) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://alice@github.com/lcr/analysis.git",
        "https://x-access-token:s3cret@github.com/lcr/analysis",
    ],
)
def test_an_https_address_may_carry_who_it_is_fetched_as(url: str) -> None:
    """As a clone made with a user or a token in its URL has for `origin`.
    What it carries is never part of what lc declares or shows."""
    found = github.parse(url)

    assert found == github.Repository("lcr", "analysis")
    assert found.https == "https://github.com/lcr/analysis"
    assert github.without_credentials(url) in (found.https, found.https + ".git")


def test_an_owner_may_be_a_managed_account() -> None:
    found = github.parse("git@github.com:jdoe_acme/analysis.git")

    assert found is not None and found.owner == "jdoe_acme"


def test_an_address_is_shown_without_what_it_carries() -> None:
    assert github.without_credentials("https://u:s3cret@gitlab.com/o/n") == "https://gitlab.com/o/n"
    assert github.without_credentials("git@github.com:o/n.git") == "git@github.com:o/n.git"
    assert github.without_credentials("/a/colleague/analysis") == "/a/colleague/analysis"


def test_a_dot_in_a_name_is_kept_and_only_a_trailing_dot_git_dropped() -> None:
    found = github.parse("https://github.com/lcr/my.analysis.git")

    assert found is not None and found.name == "my.analysis"


def test_the_same_repository_is_one_whatever_its_address_or_case() -> None:
    https = github.parse("github.com/LCR/Analysis")
    ssh = github.parse("git@github.com:lcr/analysis")

    assert https is not None and ssh is not None
    assert https.same(ssh)
    assert not https.same(github.Repository("lcr", "other"))


def test_a_token_comes_from_the_environment_in_a_set_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert github.token(tmp_path) is None
    monkeypatch.setenv("GITHUB_TOKEN", "second")
    assert github.token(tmp_path) == "second"
    monkeypatch.setenv("GH_TOKEN", "first")
    assert github.token(tmp_path) == "first"


def test_gh_is_asked_only_where_it_is_installed_and_gives_its_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tools: list[list[str]]
) -> None:
    """`gh` is never needed: without it, and without a token, there is none."""
    from unittest.mock import MagicMock

    from lightcone.engine import project

    assert github.token(tmp_path) is None
    assert ["gh", "auth", "token"] not in tools

    monkeypatch.setattr(github, "has_gh", lambda: True)
    monkeypatch.setattr(
        project, "_run", lambda argv, *, cwd: MagicMock(returncode=0, stdout="from-gh\n")
    )
    assert github.token(tmp_path) == "from-gh"
    monkeypatch.setattr(project, "_run", lambda argv, *, cwd: MagicMock(returncode=1, stdout=""))
    assert github.token(tmp_path) is None


class _Api:
    """GitHub's API, answering from a script and keeping what it was asked."""

    def __init__(self, *answers: tuple[int, dict[str, Any]]) -> None:
        self.answers = list(answers)
        self.asked: list[tuple[str, str, str | None, Any]] = []

    def __call__(
        self, method: str, path: str, token: str | None, body: Any = None
    ) -> tuple[int, dict[str, Any]]:
        self.asked.append((method, path, token, body))
        return self.answers.pop(0)


_REPO = github.Repository("alice", "analysis")


@pytest.mark.parametrize(
    ("answer", "seen"),
    [
        ((200, {"private": True}), "private"),
        ((200, {"private": False}), "public"),
        ((404, {"message": "Not Found"}), None),
    ],
)
def test_who_can_read_a_repository_is_githubs_answer(
    monkeypatch: pytest.MonkeyPatch, answer: tuple[int, dict[str, Any]], seen: str | None
) -> None:
    api = _Api(answer)
    monkeypatch.setattr(github, "_call", api)

    assert github.visibility(_REPO, "t") == seen
    assert api.asked == [("GET", "/repos/alice/analysis", "t", None)]


def test_the_branch_a_repository_shows_first_is_set_or_githubs_refusal_quoted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _Api((200, {}), (403, {"message": "Must have admin rights to Repository."}))
    monkeypatch.setattr(github, "_call", api)

    github.show_first(_REPO, "t", "master")
    with pytest.raises(ProjectError, match="Must have admin rights"):
        github.show_first(_REPO, "t", "master")

    assert api.asked[0] == ("PATCH", "/repos/alice/analysis", "t", {"default_branch": "master"})


def test_a_repository_is_created_under_the_user_the_token_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _Api((200, {"login": "Alice"}), (201, {}))
    monkeypatch.setattr(github, "_call", api)

    github.create(_REPO, "t", private=True)

    assert api.asked[1] == ("POST", "/user/repos", "t", {"name": "analysis", "private": True})


def test_a_repository_is_created_under_an_organization_the_url_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _Api((200, {"login": "alice"}), (201, {}))
    monkeypatch.setattr(github, "_call", api)

    github.create(github.Repository("lab", "analysis"), "t", private=False)

    assert api.asked[1] == (
        "POST", "/orgs/lab/repos", "t", {"name": "analysis", "private": False},
    )  # fmt: skip


def test_githubs_refusal_to_create_is_quoted_with_the_way_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refusal = {"message": "Repository creation failed.", "errors": [{"message": "name taken"}]}
    monkeypatch.setattr(github, "_call", _Api((200, {"login": "alice"}), (422, refusal)))

    with pytest.raises(ProjectError) as refused:
        github.create(_REPO, "t", private=True)

    assert "Repository creation failed. (name taken)" in str(refused.value)
    assert "https://github.com/new" in str(refused.value)


def test_a_token_github_does_not_accept_is_said_before_anything_is_created(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _Api((401, {"message": "Bad credentials"}))
    monkeypatch.setattr(github, "_call", api)

    with pytest.raises(ProjectError, match="Bad credentials"):
        github.create(_REPO, "stale", private=True)

    assert [method for method, *_ in api.asked] == ["GET"]


def test_a_repository_is_made_public_and_a_refusal_quoted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _Api((200, {}), (403, {"message": "Must have admin rights to Repository."}))
    monkeypatch.setattr(github, "_call", api)

    github.make_public(_REPO, "t")
    with pytest.raises(ProjectError, match="Must have admin rights"):
        github.make_public(_REPO, "t")

    assert api.asked[0] == ("PATCH", "/repos/alice/analysis", "t", {"private": False})
