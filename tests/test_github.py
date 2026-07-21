"""Unit tests for the `lc init` GitHub connection (`lightcone.engine.github`).

All GitHub HTTP traffic is faked at the urllib boundary; the git side of
`connect_and_push` runs against real throwaway repos with subprocess
intercepted only where a network push would happen.
"""

from __future__ import annotations

import io
import json
import urllib.request
from typing import Any

import pytest

from lightcone.engine.github import (
    GITHUB_CLIENT_ID_ENV,
    GitHubError,
    GitHubIdentity,
    RepoTarget,
    create_repo,
    device_flow,
    device_flow_client_id,
    discover_identity,
    resolve_repo,
)

_ID = GitHubIdentity(token="tok", login="eiffl", source="env")


class _FakeResponse:
    def __init__(self, payload: object, status: int = 200) -> None:
        self._body = json.dumps(payload).encode()
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


def _install_fake_http(
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[tuple[str, str], list[object]],
    calls: list[tuple[str, str, object]],
) -> None:
    """Route (method, url) → successive responses; record request bodies.

    A response entry that is an int becomes an HTTPError with that code
    (body `{}`); anything else is served as JSON.
    """

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0):  # noqa: ANN202
        url = req.full_url.split("?")[0]
        method = req.get_method()
        body = req.data.decode() if req.data else ""
        calls.append((method, url, body))
        queue = routes[(method, url)]
        entry = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(entry, int):
            raise urllib.error.HTTPError(
                url, entry, "err", hdrs=None, fp=io.BytesIO(b"{}")  # type: ignore[arg-type]
            )
        return _FakeResponse(entry)

    import urllib.error

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)


# ---------------------------------------------------------------------------
# Device flow
# ---------------------------------------------------------------------------


def test_device_flow_needs_client_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(GITHUB_CLIENT_ID_ENV, raising=False)
    assert device_flow_client_id() is None
    with pytest.raises(GitHubError, match="gh auth login"):
        device_flow(lambda c, u: None)


def test_device_flow_polls_until_authorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(GITHUB_CLIENT_ID_ENV, "cid123")
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls: list[tuple[str, str, object]] = []
    _install_fake_http(
        monkeypatch,
        {
            ("POST", "https://github.com/login/device/code"): [
                {
                    "device_code": "dev1",
                    "user_code": "ABCD-1234",
                    "verification_uri": "https://github.com/login/device",
                    "interval": 0,
                }
            ],
            ("POST", "https://github.com/login/oauth/access_token"): [
                {"error": "authorization_pending"},
                {"access_token": "tok99", "token_type": "bearer"},
            ],
            ("GET", "https://api.github.com/user"): [{"login": "eiffl"}],
        },
        calls,
    )

    shown: list[tuple[str, str]] = []
    identity = device_flow(lambda code, uri: shown.append((code, uri)))
    assert identity == GitHubIdentity(
        token="tok99", login="eiffl", source="device"
    )
    assert shown == [("ABCD-1234", "https://github.com/login/device")]
    # The device-code request carried our client id and the repo scope.
    first_body = str(calls[0][2])
    assert "cid123" in first_body and "repo" in first_body


def test_device_flow_denied_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GITHUB_CLIENT_ID_ENV, "cid123")
    monkeypatch.setattr("time.sleep", lambda s: None)
    _install_fake_http(
        monkeypatch,
        {
            ("POST", "https://github.com/login/device/code"): [
                {
                    "device_code": "dev1",
                    "user_code": "ABCD-1234",
                    "verification_uri": "https://github.com/login/device",
                    "interval": 0,
                }
            ],
            ("POST", "https://github.com/login/oauth/access_token"): [
                {"error": "access_denied", "error_description": "user said no"}
            ],
        },
        [],
    )
    with pytest.raises(GitHubError, match="user said no"):
        device_flow(lambda c, u: None)


def test_device_flow_disabled_app_raises_helpfully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(GITHUB_CLIENT_ID_ENV, "cid123")
    _install_fake_http(
        monkeypatch,
        {
            ("POST", "https://github.com/login/device/code"): [
                {"error": "unauthorized_client"}
            ]
        },
        [],
    )
    with pytest.raises(GitHubError, match="device flow enabled"):
        device_flow(lambda c, u: None)


# ---------------------------------------------------------------------------
# Credential discovery
# ---------------------------------------------------------------------------


def test_discover_prefers_env_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "envtok")
    _install_fake_http(
        monkeypatch,
        {("GET", "https://api.github.com/user"): [{"login": "eiffl"}]},
        [],
    )
    identity = discover_identity()
    assert identity is not None
    assert (identity.token, identity.source) == ("envtok", "env")


def test_discover_skips_dead_env_token_and_uses_gh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GH_TOKEN", "revoked")
    responses = {("GET", "https://api.github.com/user"): [401]}

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0):  # noqa: ANN202
        import urllib.error

        auth = req.get_header("Authorization") or ""
        if "goodtok" in auth:
            return _FakeResponse({"login": "eiffl"})
        raise urllib.error.HTTPError(
            req.full_url, 401, "bad", hdrs=None, fp=io.BytesIO(b"{}")  # type: ignore[arg-type]
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/gh")

    class _P:
        returncode = 0
        stdout = "goodtok\n"
        stderr = ""

    monkeypatch.setattr(
        "lightcone.engine.github.subprocess.run", lambda *a, **k: _P()
    )
    identity = discover_identity()
    assert identity is not None
    assert (identity.token, identity.source) == ("goodtok", "gh")
    del responses  # (documented intent: env token was rejected first)


def test_discover_none_when_nothing_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert discover_identity() is None


# ---------------------------------------------------------------------------
# Repo resolution + creation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "owner", "name"),
    [
        ("my-analysis", "eiffl", "my-analysis"),
        ("LightconeResearch/demo", "LightconeResearch", "demo"),
        ("https://github.com/Org/repo", "Org", "repo"),
        ("https://github.com/Org/repo.git", "Org", "repo"),
        ("git@github.com:Org/repo.git", "Org", "repo"),
    ],
)
def test_resolve_repo_forms(
    monkeypatch: pytest.MonkeyPatch, raw: str, owner: str, name: str
) -> None:
    _install_fake_http(
        monkeypatch,
        {("GET", f"https://api.github.com/repos/{owner}/{name}"): [404]},
        [],
    )
    target = resolve_repo(_ID, raw)
    assert (target.owner, target.name, target.exists) == (owner, name, False)


def test_resolve_repo_detects_existing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_http(
        monkeypatch,
        {
            ("GET", "https://api.github.com/repos/eiffl/exists"): [
                {"full_name": "eiffl/exists"}
            ]
        },
        [],
    )
    assert resolve_repo(_ID, "exists").exists is True


def test_resolve_repo_rejects_non_github(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(GitHubError, match="github.com"):
        resolve_repo(_ID, "https://gitlab.com/org/repo")


def test_create_repo_user_vs_org_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, Any]] = []
    _install_fake_http(
        monkeypatch,
        {
            ("POST", "https://api.github.com/user/repos"): [{"ok": True}],
            ("POST", "https://api.github.com/orgs/Lightcone/repos"): [
                {"ok": True}
            ],
        },
        calls,
    )
    create_repo(
        _ID, RepoTarget("eiffl", "mine", exists=False), private=True
    )
    create_repo(
        _ID, RepoTarget("Lightcone", "ours", exists=False), private=False
    )
    assert calls[0][1].endswith("/user/repos")
    assert json.loads(str(calls[0][2])) == {"name": "mine", "private": True}
    assert calls[1][1].endswith("/orgs/Lightcone/repos")
    assert json.loads(str(calls[1][2])) == {"name": "ours", "private": False}


def test_create_repo_surfaces_api_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(req: urllib.request.Request, timeout: float = 0):  # noqa: ANN202
        import urllib.error

        raise urllib.error.HTTPError(
            req.full_url,
            422,
            "unprocessable",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(b'{"message": "name already exists"}'),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(GitHubError, match="name already exists"):
        create_repo(
            _ID, RepoTarget("eiffl", "dupe", exists=False), private=True
        )
