"""Unit tests for the GCP Cloud Build backend.

All GCP surfaces (metadata server, GCS, Cloud Build API, registry) are
mocked at the module's HTTP seams — ``_metadata_access_token`` and
``_request`` — so the tests exercise the real control flow: freshness
probe, staging, submission, polling, failure-tail reporting.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lightcone.engine import cloudbuild
from lightcone.engine.cloudbuild import (
    BUCKET_ENV,
    SERVICE_ACCOUNT_ENV,
    CloudBuildError,
    cloudbuild_available,
    ensure_image,
)
from lightcone.engine.container import REGISTRY_ENV, registry_image_ref

REGISTRY = "europe-west1-docker.pkg.dev/lightconehub/lightcone-images"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (REGISTRY_ENV, BUCKET_ENV, SERVICE_ACCOUNT_ENV):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def deployment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REGISTRY_ENV, REGISTRY)
    monkeypatch.setenv(BUCKET_ENV, "lightconehub-lightcone-lc-build")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "Containerfile").write_text("FROM python:3.12-slim\n")
    (tmp_path / "requirements.txt").write_text("numpy\n")
    return tmp_path


# ---- backend selection ----------------------------------------------------


def test_unavailable_without_env() -> None:
    assert cloudbuild_available() is False


def test_available_with_full_contract(deployment: None) -> None:
    assert cloudbuild_available() is True


def test_unavailable_with_bucket_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(BUCKET_ENV, "some-bucket")
    assert cloudbuild_available() is False


# ---- registry ref / project parsing ---------------------------------------


def test_registry_ref_shape(project: Path) -> None:
    ref = registry_image_ref("My Proj", project / "Containerfile", project,
                             registry=REGISTRY + "/")
    repo, _, tag = ref.rpartition(":")
    assert repo == f"{REGISTRY}/lc-my-proj"
    assert len(tag) == 12


def test_gcp_project_from_registry() -> None:
    assert cloudbuild._gcp_project(REGISTRY) == "lightconehub"


def test_gcp_project_rejects_non_artifact_registry() -> None:
    with pytest.raises(CloudBuildError, match="Artifact Registry"):
        cloudbuild._gcp_project("ghcr.io/someorg")


# ---- ensure_image control flow --------------------------------------------


def _fresh_probe(monkeypatch: pytest.MonkeyPatch, exists: bool | None) -> None:
    monkeypatch.setattr(cloudbuild, "registry_image_exists", lambda ref: exists)


def test_ensure_image_cached_is_probe_only(
    deployment: None, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fresh_probe(monkeypatch, True)
    monkeypatch.setattr(
        cloudbuild, "_request", lambda *a, **k: pytest.fail("no HTTP beyond probe")
    )
    phases: list[str] = []
    ref = ensure_image(
        project, "Containerfile", project_name="proj",
        on_progress=lambda p, _d: phases.append(p),
    )
    assert ref.startswith(f"{REGISTRY}/lc-proj:")
    assert phases == ["cached"]


def test_ensure_image_builds_when_absent(
    deployment: None, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fresh_probe(monkeypatch, False)
    monkeypatch.setattr(cloudbuild, "_metadata_access_token", lambda: "tok")
    monkeypatch.setenv(SERVICE_ACCOUNT_ENV, "builder@lightconehub.iam.gserviceaccount.com")

    calls: list[tuple[str, str]] = []
    poll_status = iter(["WORKING", "SUCCESS"])

    def fake_request(method: str, url: str, token: str, *, body=None, content_type=""):
        calls.append((method, url))
        if "storage.googleapis.com/upload" in url:
            return 200, b"{}"
        if url.endswith("/builds") and method == "POST":
            payload = json.loads(body.decode())
            assert payload["images"][0].startswith(f"{REGISTRY}/lc-proj:")
            assert payload["serviceAccount"] == (
                "projects/lightconehub/serviceAccounts/"
                "builder@lightconehub.iam.gserviceaccount.com"
            )
            assert payload["source"]["storageSource"]["bucket"] == (
                "lightconehub-lightcone-lc-build"
            )
            return 200, json.dumps(
                {"metadata": {"build": {"id": "build-123"}}}
            ).encode()
        if "/builds/build-123" in url:
            return 200, json.dumps({"status": next(poll_status)}).encode()
        raise AssertionError(f"unexpected request {method} {url}")

    monkeypatch.setattr(cloudbuild, "_request", fake_request)
    monkeypatch.setattr(cloudbuild.time, "sleep", lambda _s: None)

    phases: list[str] = []
    ref = ensure_image(
        project, "Containerfile", project_name="proj",
        on_progress=lambda p, _d: phases.append(p),
    )
    assert ref.startswith(f"{REGISTRY}/lc-proj:")
    assert phases[0] == "staging"
    assert "working" in phases and "success" in phases
    # Upload happened before submission.
    assert "upload" in calls[0][1]


def test_ensure_image_failure_surfaces_log_tail(
    deployment: None, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fresh_probe(monkeypatch, False)
    monkeypatch.setattr(cloudbuild, "_metadata_access_token", lambda: "tok")

    def fake_request(method: str, url: str, token: str, *, body=None, content_type=""):
        if "storage.googleapis.com/upload" in url:
            return 200, b"{}"
        if url.endswith("/builds") and method == "POST":
            return 200, json.dumps(
                {"metadata": {"build": {"id": "build-9"}}}
            ).encode()
        if "/builds/build-9" in url:
            return 200, json.dumps({"status": "FAILURE"}).encode()
        if "alt=media" in url:
            return 200, b"step1 ok\nERROR: pip failed\n"
        raise AssertionError(f"unexpected request {method} {url}")

    monkeypatch.setattr(cloudbuild, "_request", fake_request)
    monkeypatch.setattr(cloudbuild.time, "sleep", lambda _s: None)

    with pytest.raises(CloudBuildError, match="ERROR: pip failed"):
        ensure_image(project, "Containerfile", project_name="proj")


def test_ensure_image_force_skips_probe(
    deployment: None, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cloudbuild,
        "registry_image_exists",
        lambda ref: pytest.fail("force must skip the freshness probe"),
    )
    monkeypatch.setattr(cloudbuild, "_metadata_access_token", lambda: "tok")

    def fake_request(method: str, url: str, token: str, *, body=None, content_type=""):
        if "storage.googleapis.com/upload" in url:
            return 200, b"{}"
        if url.endswith("/builds") and method == "POST":
            return 200, json.dumps(
                {"metadata": {"build": {"id": "b"}}}
            ).encode()
        return 200, json.dumps({"status": "SUCCESS"}).encode()

    monkeypatch.setattr(cloudbuild, "_request", fake_request)
    ensure_image(project, "Containerfile", project_name="proj", force=True)


def test_ensure_image_requires_credentials(
    deployment: None, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fresh_probe(monkeypatch, None)
    monkeypatch.setattr(cloudbuild, "_metadata_access_token", lambda: None)
    with pytest.raises(CloudBuildError, match="Workload Identity"):
        ensure_image(project, "Containerfile", project_name="proj")


def test_ensure_image_missing_containerfile(
    deployment: None, tmp_path: Path
) -> None:
    with pytest.raises(CloudBuildError, match="not found"):
        ensure_image(tmp_path, "Containerfile", project_name="proj")


def test_ensure_image_off_deployment(project: Path) -> None:
    with pytest.raises(CloudBuildError, match="not configured for Cloud Build"):
        ensure_image(project, "Containerfile", project_name="proj")


# ---- staged tarball --------------------------------------------------------


def test_staged_tarball_matches_hashed_context(project: Path) -> None:
    """The tarball must contain exactly the staged (= hashed) file set."""
    import io
    import tarfile

    (project / "results").mkdir()
    (project / "results" / "big.bin").write_text("x" * 10)
    data = cloudbuild._staged_context_tarball(project, project / "Containerfile")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        names = set(tar.getnames())
    assert "Containerfile" in names
    assert "requirements.txt" in names
    assert not any("results" in n for n in names)


# ---- registry probe --------------------------------------------------------


def test_registry_image_exists_parses_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cloudbuild, "_metadata_access_token", lambda: "tok")
    seen: dict[str, str] = {}

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a: object) -> None:
            pass

    def fake_urlopen(req, timeout: int = 0):
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        return _Resp()

    monkeypatch.setattr(cloudbuild.urllib.request, "urlopen", fake_urlopen)
    assert cloudbuild.registry_image_exists(f"{REGISTRY}/lc-proj:abc123") is True
    assert seen["method"] == "HEAD"
    assert seen["url"] == (
        "https://europe-west1-docker.pkg.dev/v2/"
        "lightconehub/lightcone-images/lc-proj/manifests/abc123"
    )


def test_registry_image_exists_none_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cloudbuild, "_metadata_access_token", lambda: None)
    assert cloudbuild.registry_image_exists(f"{REGISTRY}/lc-proj:abc") is None
