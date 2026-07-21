"""Unit tests for the Cloud Build backend (`lightcone.engine.cloudbuild`).

Context staging and tarring run for real against throwaway projects;
GCP (metadata, GCS, Cloud Build API) is faked at the urllib boundary.
"""

from __future__ import annotations

import io
import json
import tarfile
import urllib.request
from pathlib import Path

import pytest

from lightcone.engine.cloudbuild import (
    BUCKET_ENV,
    CloudBuildError,
    cloudbuild_available,
    ensure_worker_image,
)

_REGISTRY = "us-central1-docker.pkg.dev/testproj/binder"


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "Containerfile").write_text(
        "FROM python:3.12-slim\nCOPY requirements.txt .\n"
        "RUN pip install -r requirements.txt\n"
    )
    (proj / "requirements.txt").write_text("numpy\n")
    (proj / "unrelated.py").write_text("print('not part of the context')\n")
    return proj


@pytest.fixture()
def hub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIGHTCONE_REGISTRY", _REGISTRY)
    monkeypatch.setenv(BUCKET_ENV, "test-build-bucket")
    monkeypatch.setattr(
        "lightcone.engine.cloudbuild._metadata_access_token", lambda: "tok"
    )


class _FakeResponse:
    def __init__(self, payload: object, status: int = 200) -> None:
        self._body = (
            payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        )
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


def _install_fake_gcp(
    monkeypatch: pytest.MonkeyPatch,
    *,
    statuses: list[str],
    log_tail: str = "",
) -> list[tuple[str, str, bytes | None]]:
    """Fake GCS upload + Cloud Build create/poll; record requests."""
    calls: list[tuple[str, str, bytes | None]] = []
    polls = iter(statuses)

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0):  # noqa: ANN202
        url = req.full_url
        calls.append((req.get_method(), url, req.data))
        if "upload/storage" in url:
            return _FakeResponse({"name": "sources/x.tar.gz"})
        if url.endswith("/builds"):
            return _FakeResponse({"metadata": {"build": {"id": "build-123"}}})
        if "/builds/build-123" in url:
            return _FakeResponse({"status": next(polls)})
        if "alt=media" in url:
            return _FakeResponse(log_tail.encode())
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("lightcone.engine.cloudbuild._POLL_INTERVAL_S", 0.0)
    return calls


def test_available_needs_bucket_and_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert not cloudbuild_available()
    monkeypatch.setenv(BUCKET_ENV, "b")
    assert not cloudbuild_available()
    monkeypatch.setenv("LIGHTCONE_REGISTRY", _REGISTRY)
    assert cloudbuild_available()


def test_ensure_builds_and_returns_content_addressed_ref(
    monkeypatch: pytest.MonkeyPatch, project: Path, hub_env: None
) -> None:
    monkeypatch.setattr(
        "lightcone.engine.cloudbuild.registry_image_exists", lambda ref: False
    )
    calls = _install_fake_gcp(monkeypatch, statuses=["QUEUED", "WORKING", "SUCCESS"])

    phases: list[str] = []
    ref = ensure_worker_image(
        project,
        "Containerfile",
        project_name="myproj",
        on_progress=lambda p, m: phases.append(p),
    )

    assert ref.startswith(f"{_REGISTRY}/lc-myproj:")
    # Build submission targeted the right project and Containerfile.
    method, url, body = next(c for c in calls if c[1].endswith("/builds"))
    assert "projects/testproj/builds" in url
    build = json.loads(body.decode())
    step_args = build["steps"][0]["args"]
    assert step_args[:2] == ["build", "-t"] and ref in step_args
    assert "-f" in step_args and "Containerfile" in step_args
    assert build["images"] == [ref]
    assert build["logsBucket"] == "gs://test-build-bucket/logs"
    assert phases == ["staging", "queued", "working", "success"]

    # The uploaded tarball is the *staged* context: hashed files only.
    _, _, tar_bytes = next(c for c in calls if "upload/storage" in c[1])
    assert tar_bytes is not None
    names = tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz").getnames()
    assert "Containerfile" in names and "requirements.txt" in names
    assert "unrelated.py" not in names


def test_ensure_cached_image_skips_everything(
    monkeypatch: pytest.MonkeyPatch, project: Path, hub_env: None
) -> None:
    monkeypatch.setattr(
        "lightcone.engine.cloudbuild.registry_image_exists", lambda ref: True
    )

    def boom(*a: object, **k: object) -> None:
        raise AssertionError("no HTTP expected on the cached path")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    ref = ensure_worker_image(project, "Containerfile", project_name="myproj")
    assert ref.startswith(f"{_REGISTRY}/lc-myproj:")


def test_ensure_failure_surfaces_log_tail(
    monkeypatch: pytest.MonkeyPatch, project: Path, hub_env: None
) -> None:
    monkeypatch.setattr(
        "lightcone.engine.cloudbuild.registry_image_exists", lambda ref: False
    )
    _install_fake_gcp(
        monkeypatch,
        statuses=["WORKING", "FAILURE"],
        log_tail="Step 2/3: pip install\nERROR: no matching distribution",
    )
    with pytest.raises(CloudBuildError, match="FAILURE") as exc:
        ensure_worker_image(project, "Containerfile", project_name="myproj")
    assert "no matching distribution" in str(exc.value)


def test_ensure_custom_service_account(
    monkeypatch: pytest.MonkeyPatch, project: Path, hub_env: None
) -> None:
    monkeypatch.setenv(
        "LIGHTCONE_BUILD_SERVICE_ACCOUNT", "builder@testproj.iam.gserviceaccount.com"
    )
    monkeypatch.setattr(
        "lightcone.engine.cloudbuild.registry_image_exists", lambda ref: False
    )
    calls = _install_fake_gcp(monkeypatch, statuses=["SUCCESS"])
    ensure_worker_image(project, "Containerfile", project_name="myproj")
    _, _, body = next(c for c in calls if c[1].endswith("/builds"))
    assert (
        json.loads(body.decode())["serviceAccount"]
        == "projects/testproj/serviceAccounts/builder@testproj.iam.gserviceaccount.com"
    )


def test_ensure_requires_registry(
    monkeypatch: pytest.MonkeyPatch, project: Path
) -> None:
    monkeypatch.setenv(BUCKET_ENV, "b")
    with pytest.raises(CloudBuildError, match="LIGHTCONE_REGISTRY"):
        ensure_worker_image(project, "Containerfile", project_name="p")


def test_non_artifact_registry_rejected(
    monkeypatch: pytest.MonkeyPatch, project: Path, hub_env: None
) -> None:
    monkeypatch.setenv("LIGHTCONE_REGISTRY", "ghcr.io/org")
    monkeypatch.setattr(
        "lightcone.engine.cloudbuild.registry_image_exists", lambda ref: False
    )
    with pytest.raises(CloudBuildError, match="Artifact Registry"):
        ensure_worker_image(project, "Containerfile", project_name="p")
