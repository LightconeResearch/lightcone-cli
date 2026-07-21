"""GitHub connection for ``lc init``: device-flow auth + repo bootstrap.

A lightcone project wants a GitHub remote from day one: it backs the
analysis up, makes it shareable, and on a JupyterHub deployment it is
what the image builder clones (see :mod:`lightcone.engine.binder`).
This module gives ``lc init`` a streamlined connect step:

- **Credential discovery** (:func:`discover_identity`): an ambient
  ``GH_TOKEN``/``GITHUB_TOKEN`` or an already-authenticated ``gh`` CLI
  is used silently — users who set nothing up twice are never asked
  twice.
- **Device-flow authorization** (:func:`device_flow`): when no
  credential exists, the standard OAuth device flow (the same one
  ``gh auth login`` uses) — print a one-time code, the user approves it
  at github.com/login/device in any browser, we poll for the token.
  Needs only a public OAuth *client id* (:data:`GITHUB_CLIENT_ID_ENV`;
  a lightcone-hub deployment injects its hub OAuth app's id into every
  user pod, so on the hub this works with zero configuration).
- **Token persistence** (:func:`persist_token`): the token is handed to
  ``gh`` (``gh auth login --with-token`` + ``gh auth setup-git``) so a
  single authorization also powers ``git push`` and everything an agent
  may want to do with ``gh`` (PRs, issues, releases). Without ``gh``,
  git's ``credential.helper store`` is configured instead. Either way
  the credential lives in the user's home — on the hub that is the NFS
  volume, so it survives server restarts.
- **Repo bootstrap** (:func:`resolve_repo`, :func:`create_repo`):
  create a new repository (private supported) or connect to an existing
  one, from a single free-form input (``name``, ``owner/name``, or a
  full URL).

Everything speaks to GitHub over plain HTTPS (stdlib ``urllib``): the
three endpoints involved don't justify an SDK dependency.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

#: Public OAuth-app client id used for the device flow. Injected into
#: user pods by a lightcone-hub deployment (the hub login app, with
#: device flow enabled); settable by hand anywhere else.
GITHUB_CLIENT_ID_ENV = "LIGHTCONE_GITHUB_CLIENT_ID"

#: Scopes requested by the device flow: `repo` to create/push (private
#: included), `read:org` so org-owned repos can be targeted.
_DEVICE_SCOPE = "repo read:org"

_DEVICE_CODE_URL = "https://github.com/login/device/code"
_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"  # noqa: S105
_API_URL = "https://api.github.com"


class GitHubError(RuntimeError):
    """A GitHub connection step failed (auth, API, or git plumbing)."""


@dataclass(frozen=True)
class GitHubIdentity:
    """An authenticated GitHub credential and who it belongs to."""

    token: str
    login: str
    source: str  # "env" | "gh" | "device"


@dataclass(frozen=True)
class RepoTarget:
    """Where a project should live on GitHub."""

    owner: str
    name: str
    exists: bool

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def url(self) -> str:
        return f"https://github.com/{self.full_name}"


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------


def _post_form(url: str, data: dict[str, str]) -> dict[str, object]:
    req = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(data).encode(),
        headers={"Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise GitHubError(f"Could not reach GitHub ({exc}).") from exc
    if not isinstance(payload, dict):
        raise GitHubError(f"Unexpected response from {url}.")
    return payload


def _api(
    token: str, method: str, path: str, payload: dict[str, object] | None = None
) -> tuple[int, dict[str, object]]:
    """One GitHub REST call; returns ``(status, body)``, 4xx included."""
    req = urllib.request.Request(
        f"{_API_URL}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            **({"Content-Type": "application/json"} if payload else {}),
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8") or "{}")
            return resp.status, body if isinstance(body, dict) else {}
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8") or "{}")
        except (OSError, ValueError):
            body = {}
        return exc.code, body if isinstance(body, dict) else {}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise GitHubError(f"Could not reach the GitHub API ({exc}).") from exc


def whoami(token: str) -> str | None:
    """The token's login, or ``None`` when the token doesn't authenticate."""
    status, body = _api(token, "GET", "/user")
    login = body.get("login")
    return login if status == 200 and isinstance(login, str) else None


# ---------------------------------------------------------------------------
# Credential discovery + device flow
# ---------------------------------------------------------------------------


def discover_identity() -> GitHubIdentity | None:
    """An already-available GitHub credential, validated, or ``None``.

    Precedence: explicit env (``GH_TOKEN``/``GITHUB_TOKEN``) → the gh
    CLI's stored credential. Tokens that no longer authenticate are
    skipped rather than surfaced — a revoked token should route the
    user to re-authorization, not to an API error later.
    """
    for env in ("GH_TOKEN", "GITHUB_TOKEN"):
        token = (os.environ.get(env) or "").strip()
        if token and (login := whoami(token)):
            return GitHubIdentity(token=token, login=login, source="env")

    if shutil.which("gh"):
        result = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, check=False
        )
        token = result.stdout.strip()
        if result.returncode == 0 and token and (login := whoami(token)):
            return GitHubIdentity(token=token, login=login, source="gh")

    return None


def device_flow_client_id() -> str | None:
    """The OAuth client id for the device flow, or ``None`` off-hub."""
    return (os.environ.get(GITHUB_CLIENT_ID_ENV) or "").strip() or None


def device_flow(
    on_code: Callable[[str, str], None],
    *,
    timeout: float = 900.0,
) -> GitHubIdentity:
    """Run the OAuth device flow; return the authorized identity.

    *on_code* is called once with ``(user_code, verification_uri)`` so
    the caller can render the "enter this code there" prompt however it
    likes; this function then blocks, polling at GitHub's requested
    interval, until the user approves (or *timeout*).
    """
    client_id = device_flow_client_id()
    if client_id is None:
        raise GitHubError(
            f"No GitHub OAuth client id configured ({GITHUB_CLIENT_ID_ENV} "
            "is unset), so lc cannot start a device authorization. "
            "Run `gh auth login` instead, then re-run."
        )

    grant = _post_form(
        _DEVICE_CODE_URL, {"client_id": client_id, "scope": _DEVICE_SCOPE}
    )
    if "device_code" not in grant:
        raise GitHubError(
            "GitHub refused to start a device authorization "
            f"({grant.get('error_description') or grant.get('error') or grant}). "
            "Is device flow enabled on the OAuth app?"
        )
    on_code(str(grant["user_code"]), str(grant["verification_uri"]))

    raw_interval = grant.get("interval")
    interval = (
        float(raw_interval) if isinstance(raw_interval, (int, float)) else 5.0
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(interval)
        poll = _post_form(
            _ACCESS_TOKEN_URL,
            {
                "client_id": client_id,
                "device_code": str(grant["device_code"]),
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
        )
        token = poll.get("access_token")
        if isinstance(token, str) and token:
            login = whoami(token)
            if login is None:
                raise GitHubError("GitHub issued a token that does not work.")
            return GitHubIdentity(token=token, login=login, source="device")
        error = poll.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        raise GitHubError(
            "GitHub device authorization failed "
            f"({poll.get('error_description') or error})."
        )
    raise GitHubError("Timed out waiting for the device authorization.")


def persist_token(identity: GitHubIdentity) -> str:
    """Store a freshly obtained token where git and gh will find it.

    Preferred home is the gh CLI (one credential powering both ``gh``
    and, via ``gh auth setup-git``, https ``git push``); fallback is
    git's plain ``store`` helper. Returns a short human-readable
    description of where the credential went. No-op for credentials
    that already came from storage (``env``/``gh`` sources).
    """
    if identity.source != "device":
        return ""
    if shutil.which("gh"):
        login = subprocess.run(
            ["gh", "auth", "login", "--with-token"],
            input=identity.token,
            capture_output=True,
            text=True,
            check=False,
        )
        if login.returncode == 0:
            subprocess.run(
                ["gh", "auth", "setup-git"],
                capture_output=True,
                text=True,
                check=False,
            )
            return "stored via gh (git push + gh CLI ready)"
    cred_file = Path.home() / ".git-credentials"
    with cred_file.open("a") as f:
        f.write(f"https://x-access-token:{identity.token}@github.com\n")
    cred_file.chmod(0o600)
    subprocess.run(
        ["git", "config", "--global", "credential.helper", "store"],
        capture_output=True,
        check=False,
    )
    return "stored in ~/.git-credentials (git push ready)"


# ---------------------------------------------------------------------------
# Repo bootstrap
# ---------------------------------------------------------------------------


def resolve_repo(identity: GitHubIdentity, raw: str) -> RepoTarget:
    """Interpret free-form repo input against GitHub.

    *raw* may be a bare ``name`` (owner defaults to the authenticated
    user), ``owner/name``, or a full ``https://github.com/owner/name``
    / ``git@github.com:owner/name`` URL. The returned target says
    whether the repository already exists (→ connect) or not (→ create).
    """
    raw = raw.strip().removesuffix(".git")
    if raw.startswith("git@github.com:"):
        raw = raw[len("git@github.com:"):]
    elif "://" in raw:
        parsed = urllib.parse.urlparse(raw)
        if parsed.hostname != "github.com":
            raise GitHubError(
                f"Only github.com repositories are supported (got {raw!r})."
            )
        raw = parsed.path.strip("/")
    parts = [p for p in raw.split("/") if p]
    if len(parts) == 1:
        owner, name = identity.login, parts[0]
    elif len(parts) == 2:
        owner, name = parts
    else:
        raise GitHubError(f"Cannot interpret {raw!r} as a GitHub repository.")

    status, _ = _api(identity.token, "GET", f"/repos/{owner}/{name}")
    return RepoTarget(owner=owner, name=name, exists=status == 200)


def create_repo(
    identity: GitHubIdentity, target: RepoTarget, *, private: bool
) -> None:
    """Create *target* under the user or an organization."""
    if target.owner == identity.login:
        path = "/user/repos"
    else:
        path = f"/orgs/{target.owner}/repos"
    status, body = _api(
        identity.token,
        "POST",
        path,
        {"name": target.name, "private": private},
    )
    if status not in (200, 201):
        raise GitHubError(
            f"Could not create {target.full_name} "
            f"({body.get('message') or f'HTTP {status}'})."
        )


def connect_and_push(
    project: Path, identity: GitHubIdentity, target: RepoTarget
) -> None:
    """Point ``origin`` at *target*, commit the scaffold, push upstream.

    Fresh environments (a new hub pod) usually have no git identity —
    a repo-local one is derived from the GitHub login so the initial
    commit never fails on ``user.email`` being unset.
    """

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=project, capture_output=True, text=True,
            check=False,
        )

    if git("config", "user.email").returncode != 0:
        git("config", "user.name", identity.login)
        git(
            "config",
            "user.email",
            f"{identity.login}@users.noreply.github.com",
        )

    if git("rev-parse", "HEAD").returncode != 0:
        git("add", "-A")
        commit = git("commit", "-q", "-m", "Initial lightcone project scaffold")
        if commit.returncode != 0:
            raise GitHubError(
                f"Could not create the initial commit: "
                f"{commit.stderr.strip() or commit.stdout.strip()}"
            )

    if git("remote", "get-url", "origin").returncode == 0:
        git("remote", "set-url", "origin", f"{target.url}.git")
    else:
        git("remote", "add", "origin", f"{target.url}.git")

    push = git("push", "-q", "-u", "origin", "HEAD")
    if push.returncode != 0:
        raise GitHubError(
            f"Connected origin to {target.url} but the initial push failed:\n"
            f"{push.stderr.strip()}\n"
            "Fix and push manually (`git push -u origin HEAD`)."
        )
