"""Unit tests for the BinderHub build seam (`lightcone.engine.binder`).

The git plumbing runs against real throwaway repos (with a local bare
"remote" — pushes are exercised for real); the BinderHub HTTP side is a
fake SSE stream injected at the ``urllib`` boundary.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from lightcone.engine.binder import (
    BINDER_URL_ENV,
    BinderBuildError,
    binder_available,
    binder_service_url,
    build_via_binder,
    ensure_worker_image,
    repo_provider_spec,
)

# ---------------------------------------------------------------------------
# Service discovery
# ---------------------------------------------------------------------------


def test_binder_url_absent_off_hub() -> None:
    assert binder_service_url() is None
    assert not binder_available()


def test_binder_url_defaults_on_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JUPYTERHUB_API_TOKEN", "tok")
    assert binder_service_url() == "http://proxy-public/services/binder"
    assert binder_available()


def test_binder_url_env_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JUPYTERHUB_API_TOKEN", "tok")
    monkeypatch.setenv(BINDER_URL_ENV, "http://elsewhere/binder/")
    assert binder_service_url() == "http://elsewhere/binder"


def test_binder_url_override_without_token_is_not_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A binder URL alone is unusable — the service authenticates via the
    JupyterHub token, so availability requires both."""
    monkeypatch.setenv(BINDER_URL_ENV, "http://elsewhere/binder")
    assert binder_service_url() == "http://elsewhere/binder"
    assert not binder_available()


# ---------------------------------------------------------------------------
# Provider specs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:LightconeResearch/demo.git",
        "https://github.com/LightconeResearch/demo",
        "https://github.com/LightconeResearch/demo.git",
        "ssh://git@github.com/LightconeResearch/demo.git",
    ],
)
def test_github_remotes_use_gh_provider(url: str) -> None:
    assert (
        repo_provider_spec(url, "abc123")
        == "gh/LightconeResearch/demo/abc123"
    )


def test_non_github_remote_uses_git_provider() -> None:
    spec = repo_provider_spec("https://gitlab.com/org/repo.git", "abc123")
    assert spec == "git/https%3A%2F%2Fgitlab.com%2Forg%2Frepo.git/abc123"


# ---------------------------------------------------------------------------
# SSE build stream
# ---------------------------------------------------------------------------


class _FakeSSE:
    def __init__(self, events: list[dict[str, object] | str]) -> None:
        self._lines: list[bytes] = []
        for e in events:
            if isinstance(e, str):
                self._lines.append(e.encode())
            else:
                self._lines.append(b"data: " + json.dumps(e).encode() + b"\n")

    def __enter__(self) -> _FakeSSE:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def __iter__(self):  # noqa: ANN204
        return iter(self._lines)


def _install_fake_binder(
    monkeypatch: pytest.MonkeyPatch, events: list[dict[str, object] | str]
) -> dict[str, str]:
    monkeypatch.setenv("JUPYTERHUB_API_TOKEN", "tok")
    monkeypatch.setenv(BINDER_URL_ENV, "http://binder.test")
    seen: dict[str, str] = {}

    def fake_urlopen(req, timeout=None):  # noqa: ANN001, ANN202
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization") or ""
        return _FakeSSE(events)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return seen


def test_build_returns_image_on_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _install_fake_binder(
        monkeypatch,
        [
            {"phase": "waiting", "message": "queued"},
            ": keepalive\n",
            {"phase": "building", "message": "Step 1/5 ..."},
            {"phase": "ready", "imageName": "reg/binder/x:sha", "message": "done"},
        ],
    )
    image = build_via_binder("gh/org/repo/sha")
    assert image == "reg/binder/x:sha"
    assert seen["url"] == "http://binder.test/build/gh/org/repo/sha?build_only=true"
    assert seen["auth"] == "token tok"


def test_build_cached_image_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BinderHub answers a single `built` event when the registry already
    has the image — the everyday `lc run` fast path."""
    _install_fake_binder(
        monkeypatch,
        [{"phase": "built", "imageName": "reg/binder/x:sha", "message": "found"}],
    )
    assert build_via_binder("gh/org/repo/sha") == "reg/binder/x:sha"


def test_build_failure_raises_with_log_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_binder(
        monkeypatch,
        [
            {"phase": "building", "message": "Step 1/5 ..."},
            {"phase": "building", "message": "error: nope"},
            {"phase": "failed", "message": "Build failed"},
        ],
    )
    with pytest.raises(BinderBuildError, match="could not build") as exc:
        build_via_binder("gh/org/repo/sha")
    assert "error: nope" in str(exc.value)


def test_build_stream_without_image_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_binder(monkeypatch, [{"phase": "waiting", "message": "queued"}])
    with pytest.raises(BinderBuildError, match="could not build"):
        build_via_binder("gh/org/repo/sha")


def test_build_reports_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_binder(
        monkeypatch,
        [
            {"phase": "building", "message": "Step 1/5 ..."},
            {"phase": "ready", "imageName": "reg/x:sha"},
        ],
    )
    phases: list[str] = []
    build_via_binder("gh/org/repo/sha", on_progress=lambda p, m: phases.append(p))
    assert phases == ["building", "ready"]


# ---------------------------------------------------------------------------
# ensure_worker_image: the git flow against real repos
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A committed project with a Containerfile and a local bare remote."""
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-q", str(remote)], check=True
    )
    proj = tmp_path / "proj"
    proj.mkdir()
    _git(proj, "init", "-q", "-b", "main")
    _git(proj, "config", "user.email", "test@example.com")
    _git(proj, "config", "user.name", "Test")
    (proj / "Containerfile").write_text(
        "FROM python:3.12-slim\nCOPY requirements.txt .\n"
        "RUN pip install -r requirements.txt\n"
    )
    (proj / "requirements.txt").write_text("numpy\n")
    (proj / "analysis.py").write_text("print('hi')\n")
    _git(proj, "add", "-A")
    _git(proj, "commit", "-q", "-m", "initial")
    _git(proj, "remote", "add", "origin", str(remote))
    _git(proj, "push", "-q", "-u", "origin", "main")
    return proj


def _capture_build(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    built: dict[str, str] = {}

    def fake_build(spec: str, *, on_progress=None):  # noqa: ANN001, ANN202
        built["spec"] = spec
        return "reg/binder/proj:" + spec.rsplit("/", 1)[-1]

    monkeypatch.setattr(
        "lightcone.engine.binder.build_via_binder", fake_build
    )
    return built


def test_ensure_builds_env_ref_and_links_dockerfile(
    monkeypatch: pytest.MonkeyPatch, project: Path
) -> None:
    built = _capture_build(monkeypatch)
    image = ensure_worker_image(project, "Containerfile")

    # The Dockerfile symlink was created (repo2docker only reads
    # `Dockerfile`), committed, and pushed.
    assert (project / "Dockerfile").is_symlink()
    assert _git(project, "status", "--porcelain") == ""
    sha = _git(project, "log", "-1", "--format=%H")
    remote_sha = _git(project, "ls-remote", "origin", "main").split()[0]
    assert remote_sha == sha

    # Non-GitHub remote → generic git provider, ref = the env sha.
    assert built["spec"].startswith("git/")
    assert built["spec"].endswith(f"/{sha}")
    assert image.endswith(sha)


def test_ensure_reuses_env_ref_across_code_commits(
    monkeypatch: pytest.MonkeyPatch, project: Path
) -> None:
    """Code-only commits must not change the environment ref — no new
    image build for edits that don't touch env-defining files."""
    built = _capture_build(monkeypatch)
    ensure_worker_image(project, "Containerfile")
    env_sha = built["spec"].rsplit("/", 1)[-1]

    (project / "analysis.py").write_text("print('changed')\n")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "code only")
    _git(project, "push", "-q", "origin", "main")

    ensure_worker_image(project, "Containerfile")
    assert built["spec"].rsplit("/", 1)[-1] == env_sha
    assert env_sha != _git(project, "log", "-1", "--format=%H")


def test_ensure_dirty_env_files_are_committed_and_pushed(
    monkeypatch: pytest.MonkeyPatch, project: Path
) -> None:
    built = _capture_build(monkeypatch)
    (project / "requirements.txt").write_text("numpy\nscipy\n")

    ensure_worker_image(project, "Containerfile")

    assert _git(project, "status", "--porcelain", "--", "requirements.txt") == ""
    head = _git(project, "log", "-1", "--format=%H")
    assert built["spec"].endswith(f"/{head}")
    assert _git(project, "ls-remote", "origin", "main").split()[0] == head
    msg = _git(project, "log", "-1", "--format=%s")
    assert "environment" in msg


def test_ensure_dirty_code_files_are_left_alone(
    monkeypatch: pytest.MonkeyPatch, project: Path
) -> None:
    """Only env-defining files are auto-committed — a dirty analysis
    script is none of lc build's business."""
    _capture_build(monkeypatch)
    (project / "analysis.py").write_text("print('wip')\n")

    ensure_worker_image(project, "Containerfile")

    dirty = _git(project, "status", "--porcelain")
    assert "analysis.py" in dirty


def test_ensure_no_commit_flag_refuses_dirty_env(
    monkeypatch: pytest.MonkeyPatch, project: Path
) -> None:
    _capture_build(monkeypatch)
    (project / "requirements.txt").write_text("numpy\nscipy\n")

    with pytest.raises(BinderBuildError, match="uncommitted"):
        ensure_worker_image(project, "Containerfile", commit=False)


def test_ensure_without_remote_raises_with_guidance(
    monkeypatch: pytest.MonkeyPatch, project: Path
) -> None:
    _capture_build(monkeypatch)
    _git(project, "remote", "remove", "origin")

    with pytest.raises(BinderBuildError, match="no git remote"):
        ensure_worker_image(project, "Containerfile")


def test_ensure_conflicting_dockerfile_raises(
    monkeypatch: pytest.MonkeyPatch, project: Path
) -> None:
    _capture_build(monkeypatch)
    (project / "Dockerfile").write_text("FROM something:else\n")

    with pytest.raises(BinderBuildError, match="Dockerfile"):
        ensure_worker_image(project, "Containerfile")


def test_ensure_github_remote_uses_gh_provider(
    monkeypatch: pytest.MonkeyPatch, project: Path
) -> None:
    built = _capture_build(monkeypatch)
    # First ensure pushes the Dockerfile-symlink commit to the real bare
    # remote; the remote-tracking ref that records it survives the URL
    # swap below, so the second ensure needs no push (which would fail
    # against the fake GitHub URL).
    ensure_worker_image(project, "Containerfile")
    _git(
        project,
        "remote",
        "set-url",
        "origin",
        "git@github.com:LightconeResearch/proj.git",
    )
    image = ensure_worker_image(project, "Containerfile")
    assert built["spec"].startswith("gh/LightconeResearch/proj/")
    assert image


def test_ensure_declared_container_missing(
    monkeypatch: pytest.MonkeyPatch, project: Path
) -> None:
    _capture_build(monkeypatch)
    with pytest.raises(BinderBuildError, match="not found"):
        ensure_worker_image(project, "envs/Containerfile")
