"""GitHub, as far as publishing a project needs it.

GitHub is where a project is published: its repository holds the commits
and git-annex's log of which store holds what. git itself moves both, with
whatever credentials git already has. This module is the rest: reading
which repository a URL names, and what only GitHub's REST API does —
saying who can read a repository, creating one, making one public,
and choosing its default branch.

The API is called with Python's standard library and a token found in the
environment, or from ``gh`` where that happens to be installed and logged
in; nothing here needs ``gh``. Every call goes through :func:`_call`, the
one place a test replaces.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from lightcone.engine import project

_API = "https://api.github.com"

#: A repository on github.com, as git reaches it over https or ssh, or bare
#: as people write it (``github.com/owner/name``). An https address may
#: carry who it is fetched as; an owner may hold the underscore of a
#: managed account.
_URL = re.compile(
    r"^(?:(?:https://(?:[^/@\s]+@)?)?github\.com/|(?P<ssh>ssh://git@github\.com/|git@github\.com:))"
    r"(?P<owner>[A-Za-z0-9][A-Za-z0-9_-]*)/(?P<name>[A-Za-z0-9._-]+?)(?:\.git)?/?$"
)

#: Where someone without a token creates the repository themselves.
NEW = "https://github.com/new"


@dataclass(frozen=True)
class Repository:
    """One repository on github.com."""

    owner: str
    name: str
    #: Whether the URL it was read from reaches it over ssh.
    ssh: bool = False

    @property
    def slug(self) -> str:
        """How lc names it to people: ``github.com/<owner>/<name>``."""
        return f"github.com/{self.owner}/{self.name}"

    @property
    def https(self) -> str:
        """Its address over https."""
        return f"https://github.com/{self.owner}/{self.name}"

    @property
    def over_ssh(self) -> str:
        """Its address over ssh."""
        return f"git@github.com:{self.owner}/{self.name}.git"

    @property
    def url(self) -> str:
        """Its address the way the URL it was read from reaches it."""
        return self.over_ssh if self.ssh else self.https

    def same(self, other: Repository) -> bool:
        """Whether *other* is this repository, at whichever address.

        GitHub compares owners and names without case.
        """
        return (self.owner.lower(), self.name.lower()) == (other.owner.lower(), other.name.lower())


def parse(url: str) -> Repository | None:
    """Read which repository on github.com *url* names.

    Args:
        url: ``github.com/owner/name``, or the repository's https or ssh
            address, with or without ``.git``.

    Returns:
        The repository, or None for anything else: a path, another forge.
    """
    found = _URL.match(url.strip())
    if found is None:
        return None
    return Repository(found["owner"], found["name"], ssh=found["ssh"] is not None)


def without_credentials(url: str) -> str:
    """Give *url* as it can be shown: without the user or token it carries.

    Args:
        url: A remote's address.

    Returns:
        The address, with nothing before the host.
    """
    return re.sub(r"(?<=://)[^/@\s]+@", "", url)


def token(root: Path) -> str | None:
    """Find a token to call GitHub's API with.

    Args:
        root: The project root, where ``gh`` is asked.

    Returns:
        ``GH_TOKEN``, else ``GITHUB_TOKEN``, else what ``gh auth token``
        prints where ``gh`` is installed and logged in; None otherwise.
    """
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        if value := os.environ.get(name, "").strip():
            return value
    if not has_gh():
        return None
    asked = project._run(["gh", "auth", "token"], cwd=root)
    return (asked.stdout or "").strip() or None if asked.returncode == 0 else None


def has_gh() -> bool:
    """Whether ``gh`` is installed: never needed, used where it is there."""
    return shutil.which("gh") is not None


def visibility(repository: Repository, token: str | None) -> Literal["private", "public"] | None:
    """Ask GitHub who can read *repository*.

    Args:
        repository: The repository.
        token: A token to ask with, or None to ask as anyone.

    Returns:
        ``public`` or ``private``; None where GitHub shows no such
        repository to whoever asks, which is also its answer for a private
        one asked about without a token that can read it.

    Raises:
        ProjectError: If GitHub cannot be reached, or refuses to answer.
    """
    code, answer = _call("GET", f"/repos/{repository.owner}/{repository.name}", token)
    if code == 404:
        return None
    if code != 200:
        raise project.ProjectError(
            f"GitHub would not say who can read {repository.slug}: {_said(answer)}"
        )
    return "private" if answer.get("private") else "public"


def create(repository: Repository, token: str, *, private: bool) -> None:
    """Create *repository*, empty, under the user or organization its URL names.

    Args:
        repository: The repository to create.
        token: A token allowed to create it.
        private: Whether only people invited to it can read it.

    Raises:
        ProjectError: In GitHub's own words, if it refuses.
    """
    code, me = _call("GET", "/user", token)
    if code != 200:
        raise project.ProjectError(f"GitHub does not accept the token lc found: {_said(me)}")
    personal = str(me.get("login", "")).lower() == repository.owner.lower()
    code, answer = _call(
        "POST",
        "/user/repos" if personal else f"/orgs/{repository.owner}/repos",
        token,
        {"name": repository.name, "private": private},
    )
    if code != 201:
        raise project.ProjectError(
            f"GitHub would not create {repository.slug}: {_said(answer)}\n"
            f"Create it yourself, empty, at {NEW} and publish again."
        )


def make_public(repository: Repository, token: str) -> None:
    """Make *repository* readable by anyone.

    Args:
        repository: The repository.
        token: A token allowed to administer it.

    Raises:
        ProjectError: In GitHub's own words, if it refuses.
    """
    code, answer = _call(
        "PATCH", f"/repos/{repository.owner}/{repository.name}", token, {"private": False}
    )
    if code != 200:
        raise project.ProjectError(
            f"GitHub would not make {repository.slug} public: {_said(answer)}"
        )


def show_first(repository: Repository, token: str, branch: str) -> None:
    """Make *branch* the one *repository* shows first, and a clone checks out.

    Args:
        repository: The repository.
        token: A token allowed to administer it.
        branch: A branch it holds.

    Raises:
        ProjectError: In GitHub's own words, if it refuses.
    """
    code, answer = _call(
        "PATCH", f"/repos/{repository.owner}/{repository.name}", token, {"default_branch": branch}
    )
    if code != 200:
        raise project.ProjectError(
            f"GitHub would not make `{branch}` the default branch of {repository.slug}: "
            f"{_said(answer)}"
        )


def _said(answer: dict[str, Any]) -> str:
    """GitHub's own message, with each error it lists."""
    errors = "; ".join(
        str(error.get("message", error)) if isinstance(error, dict) else str(error)
        for error in answer.get("errors", [])
    )
    said = str(answer.get("message", "no reason given"))
    return f"{said} ({errors})" if errors else said


def _call(
    method: str, path: str, token: str | None, body: dict[str, Any] | None = None
) -> tuple[int, dict[str, Any]]:
    """Call GitHub's REST API once.

    Returns:
        The HTTP status, and the JSON object GitHub answered with, empty
        where it answered with none.

    Raises:
        ProjectError: If GitHub cannot be reached at all.
    """
    request = urllib.request.Request(
        _API + path,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "lightcone-cli",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as answered:
            code, text = answered.status, answered.read()
    except urllib.error.HTTPError as refused:
        code, text = refused.code, refused.read()
    except (urllib.error.URLError, OSError) as failed:
        raise project.ProjectError(f"GitHub's API could not be reached: {failed}") from failed
    try:
        answer = json.loads(text or b"{}")
    except ValueError:
        answer = {}
    return code, answer if isinstance(answer, dict) else {}
