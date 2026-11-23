"""Putting a project online: its commits on GitHub, its bytes in a store.

``lc publish`` converges one statement: the repository on GitHub holds the
current branch, and the project's data store holds the bytes of every
file the commit it is at names. Rerunning it is always safe.

GitHub holds git, not the bytes git-annex keeps, so those have a home of
their own, the *store*: the repository's own Git LFS storage, or a
git-annex repository in a directory on the group's cluster. It is declared
once, at the first publication, in git-annex's branch, as a special remote
git-annex enables by itself, so every clone learns it with nothing to
configure, and DataLad reads it as it reads any dataset's. That
declaration is what makes it the project's store, under whatever name: one
declared by hand, or by another tool, is published to like one lc declared.
Nothing about a publication is kept in a file or a setting of lc's own.

Two things are never decided here. Who may read the project: a repository
is created private or public as asked, with no default, and made public
only when asked to. And whether a store someone else declared is where
this clone's bytes should go: git-annex sends nothing to a store a clone
has not confirmed, and lc confirms one only where the user names it
(``--confirm-data``).

One publication sends, in this order, the bytes, git-annex's log of where
they are, then the commits, so a commit never reaches a clone before the
bytes it names can be fetched: where some bytes could not be sent,
neither the log nor the commits go, and publishing again picks up from
what the store already took. It sends what the commit names, the current
versions, with the results the analysis reads at commits it declares in
this repository. It sends no tag and no other branch, deletes nothing
anywhere, and merges nothing: where GitHub's branch has commits this
clone lacks, it refuses.
"""

from __future__ import annotations

import re
import shlex
import tomllib
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from lightcone.engine import dataset, github, materialize, plan, project
from lightcone.engine.project import ProjectError

#: The first git-annex that sends nothing to a store a clone has not
#: confirmed. With an older one, a clone would publish to wherever the
#: repository's history says.
_ANNEX = (10, 20261026)

#: What GitHub's Git LFS takes in one file on its largest plan. A smaller
#: plan's limit is GitHub's to say, in its own words.
_LFS_LIMIT = 5 * 1024**3

_WHO = """\
    --private                      only people you invite; public later with
                                   `lc publish --public`
    --public                       anyone, permanently; needs a license in pyproject.toml"""

_WHERE = """\
    --data github-lfs              this repository's Git LFS storage: no other account.
                                   2 GB per file, 10 GiB free then billed to the owner,
                                   and nothing in it can be deleted
    --data /DIRECTORY              a new directory on your cluster, which collaborators
                                   reach by filesystem (or ssh://HOST/DIRECTORY, by ssh);
                                   never readable by the public"""


@dataclass
class PublishReport:
    """What a publication sent, or — in check mode — what one would."""

    #: The repository on GitHub the project is published to, as
    #: ``github.com/<owner>/<name>``; empty for a project that is not online.
    target: str = ""
    #: Who can read it, ``private`` or ``public``; empty where GitHub did
    #: not say.
    visibility: str = ""
    #: Whether this call created the repository.
    created: bool = False
    #: Whether this call made it public, and the license it found to.
    made_public: bool = False
    license: str = ""
    #: Where the project's bytes go; empty while it declares no store.
    store: str = ""
    #: Whether this call declared the store, or confirmed it for this clone.
    declared: bool = False
    confirmed: bool = False
    #: Each file whose bytes this call sent to the store → what it is: a
    #: ``result``, an ``input`` or another ``file``. A file at a commit the
    #: analysis declares is named ``<commit>:<path>``.
    sent: dict[str, str] = field(default_factory=dict)
    #: Check mode: each file whose bytes are in this clone and not in the
    #: store.
    unsent: dict[str, str] = field(default_factory=dict)
    #: Each file whose bytes could not be sent → why.
    failed: dict[str, str] = field(default_factory=dict)
    #: Files the commit names whose bytes are neither in this clone nor in
    #: the store: every other clone sees those results as missing.
    nowhere: list[str] = field(default_factory=list)
    #: The branch published, and the commit it is at.
    branch: str = ""
    commit: str = ""
    #: How many commits this call sent.
    commits: int = 0
    #: Check mode: how many commits GitHub lacks.
    unpushed: int = 0
    #: Changes that are not committed, which no publication sends.
    uncommitted: int = 0
    #: lc's own prose: what the lock scan found, results still to make.
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether everything this call set out to send was sent."""
        return not self.failed

    @property
    def published(self) -> bool:
        """Whether GitHub and the store hold all of the commit."""
        return bool(self.target and self.store) and not (
            self.unsent or self.unpushed or self.failed or self.nowhere
        )

    def as_dict(self) -> dict[str, Any]:
        """Return the report as JSON-ready data.

        Returns:
            Every field, with ``ok`` and ``published`` first.
        """
        return {"ok": self.ok, "published": self.published, **asdict(self)}


def publish(
    root: Path,
    url: str = "",
    *,
    visibility: str = "",
    data: str = "",
    confirm: str = "",
    check: bool = False,
) -> PublishReport:
    """Put the project online, or send what GitHub and its store still lack.

    Whatever lc can tell beforehand is refused before anything is created
    or sent.

    Args:
        root: The project root.
        url: The repository on GitHub to publish to, needed only while
            the project has none.
        visibility: ``private`` or ``public``: who can read a repository
            this call creates; ``public`` also makes a private one public.
        data: Where the project's data lives, needed only while it declares
            no store: ``github-lfs``, a directory, or ``ssh://host/directory``.
        confirm: The store's place, repeated by the user to confirm, for
            this clone, a store another clone declared.
        check: Report what is not published, and send nothing.

    Returns:
        What was sent, or in check mode what would be.

    Raises:
        ProjectError: Whatever stands in the way, with what resolves it.
    """
    project.require_git()
    project.require_git_annex()
    require_annex()
    if not dataset.is_annexed(root):
        raise ProjectError("git-annex is not set up in this clone — `lc init` sets it up.")
    report = PublishReport()
    origin = dataset.remote_url(root, "origin")
    target = _target(origin, url)
    if target is None or (check and not origin):
        if check:
            return report
        raise ProjectError(
            "this project is not online yet. Name the repository on GitHub to publish it "
            f"to, and who can read it:\n  lc publish github.com/OWNER/{root.name} --private\n"
            f"  lc publish github.com/OWNER/{root.name} --public"
        )
    branch = (dataset._ask(["symbolic-ref", "--short", "-q", "HEAD"], cwd=root) or "").strip()
    if not branch:
        raise ProjectError(
            "HEAD is not on a branch, and lc publishes a branch: `git switch` to one."
        )
    report.target, report.branch, report.commit = target.slug, branch, dataset.head(root)
    dataset.populate(root)
    dataset.enable_store(root)
    token = github.token(root)

    # --- What stands, before anything is done ------------------------------
    heads, said = _heads(root, origin or target.url)
    creating = heads is None
    if creating:
        if check:
            raise ProjectError(f"{target.slug} could not be reached:\n{said}")
        _require_creatable(target, token, said)
    else:
        report.visibility = _visibility(target, token, asked=bool(visibility))
        if visibility == "private" and report.visibility == "public":
            raise ProjectError(
                f"{target.slug} is public, and lc offers no way back: copies made while it "
                "was public remain."
            )
    declared = _store(root)
    wanted = _wanted(data, target) if data else None
    homeless = declared is None and wanted is None
    if creating and (not visibility or homeless):
        asked = [
            *(["who can read it"] if not visibility else []),
            *(["where its data lives"] if homeless else []),
        ]
        raise ProjectError(
            f"{target.slug} does not exist. lc creates it once you choose "
            f"{' and '.join(asked)}:\n"
            + "\n".join([*([_WHO] if not visibility else []), *([_WHERE] if homeless else [])])
        )
    going_public = visibility == "public" and (creating or report.visibility == "private")
    if going_public:
        report.license = _license(root)
        if token is None:
            raise ProjectError(
                f"making {target.slug} public takes a GitHub token: GH_TOKEN or GITHUB_TOKEN "
                "in the environment, or `gh auth login`."
            )
    if declared is None:
        if confirm:
            raise ProjectError(
                "`--confirm-data` confirms a store the project already declares, and it "
                "declares none yet: `--data` declares one."
            )
        if homeless and not check:
            raise ProjectError(
                f"this project's data has no home yet. Choose where it lives:\n{_WHERE}"
            )
    else:
        _require_same_store(declared, wanted, confirm, target, check=check)
    theirs = _require_history(root, target, heads or {}, branch, report.commit, first=not origin)
    report.uncommitted = len(dataset.status(root))
    files, kinds = _files(root, report)
    unsent, report.nowhere = _lacking(root, declared, files, kinds)
    home = declared or wanted
    if home is not None and home.kind == "github-lfs":
        _require_lfs_takes(unsent, files)
    if check:
        report.unsent, report.unpushed = unsent, _commits(root, theirs, report.commit)
        report.store = _described(declared, target) if declared is not None else ""
        return report

    # --- Doing it ---------------------------------------------------------
    if declared is None and wanted is not None:
        # Before the repository, so that a directory which cannot hold a
        # store is refused with nothing created online.
        _make_store(root, wanted)
    if creating:
        assert token is not None  # `_require_creatable` refuses without one
        github.create(target, token, private=visibility == "private")
        report.created, report.visibility = True, visibility
    if not origin:
        dataset._git(["remote", "add", "origin", target.url], cwd=root)
    dataset.settle(root)
    if declared is None and wanted is not None:
        _declare(root, wanted, target)
        declared, report.declared = _store(root), True
        dataset.settle(root)
    if declared is None:
        raise ProjectError(
            "git-annex took the store's declaration, and this clone does not have it as a "
            "store: nothing was sent."
        )
    for remote, (address, sure) in declared.remotes.items():
        if not sure:
            dataset.confirm(root, remote, _settings(declared, address))
            report.confirmed = True
    report.store = _described(declared, target)
    # GitHub's copy of git-annex's log says what others already sent, unless
    # it is the very one this clone last fetched or pushed. Each connection
    # to GitHub costs seconds over ssh (measured).
    fetched = dataset._ask(["rev-parse", "-q", "--verify", "origin/git-annex"], cwd=root) or ""
    if heads and heads.get("git-annex", "") != fetched.strip():
        dataset._git([*dataset.reaching(root), "fetch", "-q", "origin"], cwd=root)
        unsent, report.nowhere = _lacking(root, declared, files, kinds)

    refused = dataset.send(
        root,
        declared.remote(ssh=target.ssh),
        list(dict.fromkeys(files[name] for name in unsent)),
    )
    for name, kind in unsent.items():
        if (why := refused.get(files[name])) is None:
            report.sent[name] = kind
        else:
            report.failed[name] = why
    if report.failed:
        # Neither the log nor the commits: a commit whose bytes are not in
        # the store would reach clones as results to make again.
        return report
    report.commits = _commits(root, theirs, report.commit)
    _push(root, branch, target, heads or {})
    if going_public and not creating:
        assert token is not None
        github.make_public(target, token)
        report.made_public, report.visibility = True, "public"
    return report


# =============================================================================
# The repository on GitHub
# =============================================================================


def _target(origin: str, url: str) -> github.Repository | None:
    """The repository the project is published to: ``origin``'s, or the one
    *url* names while the project has no ``origin``.

    Raises:
        ProjectError: If *url* names no repository on GitHub, ``origin``
            is not one, or the two differ.
    """
    named = None
    if url:
        named = github.parse(url)
        if named is None:
            raise ProjectError(
                f"`{url}` is not a repository on GitHub, the one forge lc publishes to. "
                "Name it as github.com/OWNER/NAME."
            )
    if not origin:
        return named
    current = github.parse(origin)
    if current is None:
        raise ProjectError(
            f"`origin` is {github.without_credentials(origin)}, which is not a repository "
            "on GitHub, so there is "
            "nowhere to publish to. Keep it under another name, then name the repository:\n"
            "  git remote rename origin <name>\n  lc publish github.com/OWNER/NAME"
        )
    if named is not None and not named.same(current):
        raise ProjectError(
            f"this project is published to {current.slug}, and a project has one "
            "repository: `lc publish`, with no URL, publishes there."
        )
    return current


def _heads(root: Path, address: str) -> tuple[dict[str, str] | None, str]:
    """The branches the repository at *address* holds, each with its commit;
    or None, with git's own words, where git cannot reach it."""
    asked = project._run(
        ["git", *dataset.reaching(root), "ls-remote", "--heads", address], cwd=root
    )
    if asked.returncode != 0:
        return None, (asked.stderr or "").strip()
    heads = {}
    for line in (asked.stdout or "").splitlines():
        commit, _, ref = line.partition("\t")
        heads[ref.removeprefix("refs/heads/")] = commit
    return heads, ""


def _require_creatable(target: github.Repository, token: str | None, said: str) -> None:
    """Refuse a repository git cannot reach that lc cannot create either."""
    if token is None:
        raise ProjectError(
            f"git cannot reach {target.slug}: it does not exist yet, or git has no "
            f"credentials for it.\n{said}\n"
            "lc creates it given a GitHub token: GH_TOKEN or GITHUB_TOKEN in the "
            "environment, or `gh auth login`. Without one, create it yourself, empty, at "
            f"{github.NEW} and publish again; git then pushes with an ssh key or a "
            "credential helper."
        )
    if github.visibility(target, token) is not None:
        raise ProjectError(
            f"{target.slug} exists, and git cannot reach it:\n{said}\n"
            "Give git credentials for it: `gh auth login`, an ssh key, or GH_TOKEN in the "
            "environment."
        )


def _visibility(target: github.Repository, token: str | None, *, asked: bool) -> str:
    """Who can read a repository git reaches: ``private``, ``public``, or
    empty where GitHub's API does not answer and nothing depends on it.

    A repository git reaches that GitHub shows to nobody without the right
    token is private.
    """
    try:
        return github.visibility(target, token) or "private"
    except ProjectError:
        if asked:
            raise
        return ""


def _license(root: Path) -> str:
    """The license the commit being published declares.

    Raises:
        ProjectError: If it declares none.
    """
    committed = dataset._ask(["show", "HEAD:./pyproject.toml"], cwd=root) or ""
    try:
        declared = tomllib.loads(committed).get("project", {}).get("license")
    except tomllib.TOMLDecodeError:
        declared = None
    if not isinstance(declared, str) or not declared.strip():
        raise ProjectError(
            "A public project needs a license, or nobody may legally reuse it. Add an SPDX "
            "identifier under [project] in pyproject.toml (license = \"CC-BY-4.0\", for "
            "example), commit, and publish again."
        )
    return declared.strip()


def _require_history(
    root: Path,
    target: github.Repository,
    heads: dict[str, str],
    branch: str,
    commit: str,
    *,
    first: bool,
) -> str:
    """Refuse to publish where GitHub holds commits this clone lacks.

    lc merges nothing: commits others published are pulled first, and a
    repository published to for the first time is empty, or already holds
    this project's history.

    Returns:
        The commit GitHub's *branch* is at, or empty where it has none.

    Raises:
        ProjectError: If that commit is not one *commit* descends from,
            or a first publication finds other branches and not this one.
    """
    theirs = heads.get(branch, "")
    if theirs and not dataset._git_ok(["merge-base", "--is-ancestor", theirs, commit], cwd=root):
        if first:
            raise ProjectError(
                f"{target.slug} is not empty, and its `{branch}` is not this project's "
                "history: publish to an empty repository."
            )
        raise ProjectError(
            f"`{branch}` on {target.slug} has commits this clone lacks: `git pull`, then "
            "`lc publish`."
        )
    if first and heads and not theirs:
        raise ProjectError(
            f"{target.slug} is not empty, and holds no `{branch}`: publish to an empty "
            "repository."
        )
    return theirs


def _commits(root: Path, theirs: str, commit: str) -> int:
    """How many commits of the branch GitHub lacks."""
    span = f"{theirs}..{commit}" if theirs else commit
    return int(dataset._git(["rev-list", "--count", span], cwd=root).strip())


def _push(
    root: Path, branch: str, target: github.Repository, heads: dict[str, str]
) -> None:
    """Send git-annex's log, then the branch, to GitHub.

    The log first has to hold what this clone noted and what GitHub's copy
    says (``dataset.record_copies``). Both refs go or neither does; where
    GitHub's *heads* are both already, and the branch is set up to pull
    from it, nothing goes. Where GitHub refuses, someone published in
    between: their log is taken in and the push tried once more, unless
    the branch itself moved, which is theirs to pull.

    Raises:
        ProjectError: If the branch moved, or GitHub refuses again, in
            git's own words.
    """
    refs = [f"refs/heads/{branch}", "refs/heads/git-annex"]
    push = [
        "git", *dataset.reaching(root), "push", "-q", "--atomic", "origin",
        *(f"{ref}:{ref}" for ref in refs),
    ]  # fmt: skip
    dataset.record_copies(root)
    tracked = (dataset._ask(["config", "--get", f"branch.{branch}.remote"], cwd=root) or "").strip()
    held = [heads.get(ref.removeprefix("refs/heads/"), "") for ref in refs]
    if tracked and dataset._git(["rev-parse", *refs], cwd=root).split() == held:
        return
    if project._run(push, cwd=root).returncode != 0:
        dataset._git([*dataset.reaching(root), "fetch", "-q", "origin"], cwd=root)
        moved = dataset._ask(
            ["rev-parse", "-q", "--verify", f"refs/remotes/origin/{branch}"], cwd=root
        )
        if moved and not dataset._git_ok(
            ["merge-base", "--is-ancestor", moved.strip(), "HEAD"], cwd=root
        ):
            raise ProjectError(
                f"`{branch}` on {target.slug} has commits this clone lacks: `git pull`, then "
                "`lc publish`. The bytes are in the store already."
            )
        dataset.record_copies(root)
        again = project._run(push, cwd=root)
        if again.returncode != 0:
            raise ProjectError(
                f"git could not push to {target.slug}:\n{(again.stderr or '').strip()}\n"
                "Publishing takes write access to it, with credentials git can use: `gh "
                "auth login`, an ssh key, or GH_TOKEN in the environment. What was sent to "
                "the store stays there."
            )
    # So that `git pull`, with nothing more, takes in what others publish.
    if not tracked:
        dataset._git(["branch", "-q", f"--set-upstream-to=origin/{branch}", branch], cwd=root)


# =============================================================================
# The store
# =============================================================================


def _store(root: Path) -> dataset.Store | None:
    """The store the project declares, under whatever name, as this clone
    has it; None where it declares none.

    Raises:
        ProjectError: If it declares several, since a project's bytes go
            to one place, or one of a kind lc publishes nothing to: a
            second store beside it would be two.
    """
    declared = dataset.stores(root)
    if len(declared) > 1:
        raise ProjectError(
            "this project declares more than one data store, and lc publishes to one:\n"
            + "\n".join(
                f"    {', '.join(store.remotes)}: "
                + (github.without_credentials(store.location) or "no git remote")
                for store in declared
            )
            + "\nAll but one have to be retired, with git-annex, by whoever declared them."
        )
    if declared and declared[0].kind is None:
        raise ProjectError(
            f"this project's data store is `{', '.join(declared[0].remotes)}`, of a kind lc "
            "does not publish to. lc publishes to a repository's Git LFS or to a directory, "
            "and declares no second store beside the one a project has."
        )
    return declared[0] if declared else None


@dataclass(frozen=True)
class _Wanted:
    """A store ``--data`` names, before it is declared."""

    kind: str
    #: Where it is, as git-annex is told: the directory, or its ssh URL;
    #: the repository's https address for Git LFS.
    location: str


def _wanted(data: str, target: github.Repository) -> _Wanted:
    """Read what ``--data`` names.

    Raises:
        ProjectError: If it names nothing lc declares a store in.
    """
    if data == "github-lfs":
        return _Wanted("github-lfs", target.https)
    place = data.rstrip("/") or "/"
    if re.search(r"\s", place):
        raise ProjectError(f"`--data {data}`: a store's place cannot hold a space.")
    if place.startswith("ssh://"):
        parts = urlsplit(place)
        if parts.hostname and parts.path.strip("/"):
            return _Wanted("directory", place)
    elif place.startswith("/") and place != "/":
        return _Wanted("directory", place)
    raise ProjectError(
        f"`--data {data}` names no place lc keeps a project's data in. The choices:\n{_WHERE}"
    )


def _require_same_store(
    declared: dataset.Store,
    wanted: _Wanted | None,
    confirm: str,
    target: github.Repository,
    *,
    check: bool,
) -> None:
    """Refuse a publication that would send bytes anywhere but to the store
    the project declares, confirmed in this clone.

    Raises:
        ProjectError: If ``--data`` names another store, the store is the
            Git LFS of another repository, or this clone has not confirmed
            it and ``--confirm-data`` does not name it.
    """
    described = _described(declared, target)
    if declared.kind == "github-lfs":
        addresses = [github.parse(address) for address, _ in declared.remotes.values()]
        if any(each is None or not each.same(target) for each in addresses):
            raise ProjectError(
                f"this project's data store is the Git LFS of {declared.location}, which is "
                f"not {target.slug}, the repository it is published to. lc publishes a "
                "project and its data to one repository."
            )
        place = target.https
        named = github.parse(confirm) if confirm else None
        confirmed = named is not None and named.same(target)
    else:
        place = declared.location
        confirmed = confirm.rstrip("/") == place
    if wanted is not None and (wanted.kind, wanted.location) != (declared.kind, place):
        raise ProjectError(
            f"this project's data lives in {described}, and lc does not move it: publish "
            "without `--data`."
        )
    if confirm and not confirmed:
        raise ProjectError(
            f"`--confirm-data {confirm}` is not where this project's data goes, which is:\n"
            f"    {described}\nNothing was confirmed."
        )
    if check or confirmed or all(sure for _, sure in declared.remotes.values()):
        return
    raise ProjectError(
        "This clone has not confirmed where the project's data goes:\n"
        f"    {described}\n"
        "Publishing writes your results there. If that is where they belong, confirm it "
        f"once for this clone:\n    lc publish --confirm-data {place}"
    )


def _described(store: dataset.Store, target: github.Repository) -> str:
    """The store, as people read it."""
    return f"GitHub LFS of {target.slug}" if store.kind == "github-lfs" else store.location


def _settings(store: dataset.Store, address: str) -> dict[str, str]:
    """What lc declares for a store at *address*, setting for setting: what
    git-annex has to show for lc to confirm it."""
    if store.kind == "github-lfs":
        return {"type": "git-lfs", "url": address, "autoenable": "true", "encryption": "none"}
    return {"type": "git", "location": address, "autoenable": "true"}


#: Makes the store a directory holds: a bare git-annex repository, shared
#: with the directory's group. Run where the directory is, with it and the
#: committer's name and address as its three arguments. An existing
#: directory is taken only empty, or as such a repository already.
_MAKE_STORE = """\
set -e
if [ -n "$(ls -A -- "$1" 2>/dev/null)" ]; then
  if [ "$(git -C "$1" rev-parse --is-bare-repository 2>/dev/null)" = true ] \\
      && git -C "$1" config --get annex.uuid >/dev/null 2>&1; then
    exit 0
  fi
  echo "$1 is not empty, and is not a store lc made: name a new directory" >&2
  exit 3
fi
git init -q --bare --shared=group -- "$1"
git -C "$1" -c user.name="$2" -c user.email="$3" annex init -q
"""


def _make_store(root: Path, wanted: _Wanted) -> None:
    """Make the store a directory holds, where it is not there yet.

    A bare git-annex repository, the one kind of store in a directory
    whose address a clone can enable by itself (measured), made where the
    directory is: here, or over ssh. Git LFS has nothing to make: GitHub
    keeps it with the repository.

    Raises:
        ProjectError: If the directory cannot hold a store, in the words
            of whatever refused.
    """
    if wanted.kind != "directory":
        return
    ident = re.match(r"(.*) <(.*)>", dataset._git(["var", "GIT_COMMITTER_IDENT"], cwd=root))
    name, address = (ident[1], ident[2]) if ident else ("", "")
    far = urlsplit(wanted.location) if wanted.location.startswith("ssh://") else None
    make = ["sh", "-c", _MAKE_STORE, "sh", far.path if far else wanted.location, name, address]
    if far:
        make = [
            *shlex.split(dataset.ssh(root)),
            *(["-p", str(far.port)] if far.port else []),
            f"{far.username}@{far.hostname}" if far.username else str(far.hostname),
            shlex.join(make),
        ]
    made = project._run(make, cwd=root)
    if made.returncode != 0:
        raise ProjectError(
            f"no store could be made in {wanted.location}:\n{(made.stderr or '').strip()}"
        )


def _declare(root: Path, wanted: _Wanted, target: github.Repository) -> None:
    """Declare the project's store, once and for every clone.

    A special remote git-annex enables by itself, recorded in its branch.
    Git LFS is declared at the repository's https address and again at its
    ssh address, as one store, so each clone reaches it with the
    credentials it has. A directory is declared as the git-annex
    repository :func:`_make_store` made in it.

    Raises:
        ProjectError: If git-annex refuses the declaration, in its own
            words.
    """
    declare = [*dataset.reaching(root), "annex", "initremote"]
    if wanted.kind == "github-lfs":
        dataset._git(
            [*declare, dataset.STORE, "type=git-lfs", f"url={target.https}", "encryption=none",
             "autoenable=true"],
            cwd=root,
        )  # fmt: skip
        dataset._git(
            [*declare, dataset.STORE_SSH, f"--sameas={dataset.STORE}", "type=git-lfs",
             f"url={target.over_ssh}", "autoenable=true"],
            cwd=root,
        )  # fmt: skip
    else:
        dataset._git(
            [*declare, dataset.STORE, "type=git", f"location={wanted.location}",
             "autoenable=true"],
            cwd=root,
        )  # fmt: skip


# =============================================================================
# What a publication sends
# =============================================================================


def _files(root: Path, report: PublishReport) -> tuple[dict[str, str], dict[str, str]]:
    """Every file a publication answers for, and what each is.

    The files of the commit that git-annex keeps: results at each of their
    places, declared inputs, the image. With them, the results and inputs
    the analysis reads at commits it declares in this repository: no
    branch's current version, and what a clone needs to make what reads
    them. An analysis that cannot be read is no reason to keep the commit
    offline: what it reads at other commits is then left out, and said.

    Returns:
        Each file → its key, and each file → ``result``, ``input`` or
        ``file``. A file of the commit is named from the top of the
        repository, one at another commit ``<commit>:<path>``.
    """
    files = dataset.annexed(root, report.commit)
    kinds = dict.fromkeys(files, "file")
    scan = materialize.MaterializeReport()
    try:
        graph, environment = materialize._graph(root, [], scan)
        states = Counter(
            found.verdict.state
            for found in materialize._classify(root, graph, environment, refresh=False)
        )
    except ProjectError as unread:
        report.warnings.append(
            "the analysis could not be read, so what it reads at other commits was not "
            f"looked for: {unread}"
        )
        return files, kinds
    report.warnings.extend(scan.warnings)
    if unmade := states["stale"] + states["missing"]:
        report.warnings.append(
            f"{unmade} result(s) of the working tree are stale or missing, and are "
            "published as the commit has them — `lc materialize` makes them"
        )
    prefix = dataset._git(["rev-parse", "--show-prefix"], cwd=root).strip()
    for _, declared in graph.inputs.values():
        if prefix + declared in kinds:
            kinds[prefix + declared] = "input"
    elsewhere = {}
    for task in graph.tasks.values():
        for path in task.paths:
            if (name := prefix + plan.declared_path(root, path)) in kinds:
                kinds[name] = "result"
        for source in task.result.inputs:
            at = plan.at_commit(root, task.inputs[source.entry])
            if at is not None and graph.commits.get(at[0]) is None:
                elsewhere[f"{at[0]}:{at[1]}"] = "result" if source.result else "input"
    for name, key in dataset.keys(root, sorted(elsewhere)).items():
        files[name], kinds[name] = key, elsewhere[name]
    return files, kinds


def _lacking(
    root: Path, store: dataset.Store | None, files: dict[str, str], kinds: dict[str, str]
) -> tuple[dict[str, str], list[str]]:
    """Sort the files the store lacks by whether this clone can send them.

    By git-annex's log, read without the network: what it says the store
    holds is not sent again.

    Returns:
        Each file whose bytes are here and not in the store → what it is,
        and the files whose bytes are in neither.
    """
    held = dataset.copies(root, dict.fromkeys(files.values()))
    there = store.uuid if store is not None else ""
    unsent, nowhere = {}, []
    for name, key in files.items():
        here, others = held.get(key, (False, frozenset()))
        if there in others:
            continue
        if here:
            unsent[name] = kinds[name]
        else:
            nowhere.append(name)
    return unsent, nowhere


def _require_lfs_takes(unsent: dict[str, str], files: dict[str, str]) -> None:
    """Refuse a publication to Git LFS that holds a file it takes on no plan.

    Raises:
        ProjectError: Naming each file above :data:`_LFS_LIMIT`.
    """
    over = {name: _size(files[name]) for name in unsent if _size(files[name]) > _LFS_LIMIT}
    if over:
        raise ProjectError(
            "GitHub's Git LFS takes no file above 5 GB, on any plan, and this commit "
            "names:\n  "
            + "\n  ".join(f"{name} ({size / 1024**3:.1f} GB)" for name, size in over.items())
            + "\nNothing was sent."
        )


def _size(key: str) -> int:
    """The size git-annex's key gives for its bytes, or 0 where it gives none."""
    found = re.search(r"-s(\d+)-", key)
    return int(found[1]) if found else 0


def require_annex() -> None:
    """Refuse a git-annex that would send to a store nobody confirmed.

    Raises:
        ProjectError: If the git-annex on ``PATH`` is older than the first
            that keeps that gate.
    """
    raw = project._run(["git", "annex", "version", "--raw"], cwd=Path.cwd()).stdout or ""
    found = re.match(r"(\d+)\.(\d+)", raw.strip())
    if found is None or (int(found[1]), int(found[2])) < _ANNEX:
        raise ProjectError(
            f"lc publishes with git-annex {_ANNEX[0]}.{_ANNEX[1]} or later, the first that "
            f"sends nothing to a store a clone has not confirmed, and found {raw.strip() or 'none'}"
            ". It installs with lc itself: `uv tool install --force lightcone-cli` repairs "
            "the install."
        )
