"""Tests for the site registry — site detection and the HostSite wrapper."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from lightcone.engine import site_registry
from lightcone.engine.site_registry import (
    HostSite,
    detect_current_site,
    detect_site,
)


@pytest.fixture
def fake_hostname(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    """Return a setter that pins ``socket.gethostname`` for the test."""

    def _set(name: str) -> None:
        monkeypatch.delenv("LIGHTCONE_SITE", raising=False)
        monkeypatch.delenv("NERSC_HOST", raising=False)
        monkeypatch.setattr(site_registry.socket, "gethostname", lambda: name)

    return _set


class TestDetectSite:
    def test_matches_perlmutter_substring(self) -> None:
        assert detect_site("login29.chn.perlmutter.nersc.gov") == "perlmutter"

    def test_matches_saul_pattern(self) -> None:
        assert detect_site("saul01") == "perlmutter"

    def test_unknown_host(self) -> None:
        assert detect_site("generic-laptop") is None

    def test_local_site_skipped(self) -> None:
        # "local" has backend=local and is excluded from auto-detection.
        assert detect_site("local") is None


class TestHostSite:
    def test_matched_site_is_truthy(self) -> None:
        site = HostSite(key="perlmutter", defaults={"display_name": "NERSC Perlmutter"})
        assert bool(site) is True

    def test_unmatched_site_is_falsy(self) -> None:
        assert bool(HostSite(key=None)) is False

    def test_get_returns_field(self) -> None:
        site = HostSite(key="perlmutter", defaults={"container_runtime": "podman-hpc"})
        assert site.get("container_runtime") == "podman-hpc"

    def test_get_missing_field_returns_default(self) -> None:
        site = HostSite(key="perlmutter", defaults={})
        assert site.get("missing", "fallback") == "fallback"
        assert site.get("missing") is None

    def test_display_name_from_defaults(self) -> None:
        site = HostSite(key="perlmutter", defaults={"display_name": "NERSC Perlmutter"})
        assert site.display_name == "NERSC Perlmutter"

    def test_display_name_falls_back_to_key(self) -> None:
        site = HostSite(key="perlmutter", defaults={})
        assert site.display_name == "perlmutter"

    def test_display_name_for_unknown_site(self) -> None:
        assert HostSite(key=None).display_name == "unknown"


class TestDetectCurrentSite:
    def test_nersc_host_detects_site_on_compute_node(
        self, fake_hostname: Callable[[str], None], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_hostname("nid001234")
        monkeypatch.setenv("NERSC_HOST", "perlmutter")
        site = detect_current_site()
        assert site.key == "perlmutter"

    def test_known_host_returns_populated_site(
        self, fake_hostname: Callable[[str], None]
    ) -> None:
        fake_hostname("login29.chn.perlmutter.nersc.gov")
        site = detect_current_site()
        assert site
        assert site.key == "perlmutter"
        assert site.get("container_runtime") == "podman-hpc"
        assert site.display_name == "NERSC Perlmutter"

    def test_unknown_host_returns_empty_site(
        self, fake_hostname: Callable[[str], None]
    ) -> None:
        fake_hostname("generic-laptop")
        site = detect_current_site()
        assert not site
        assert site.key is None
        assert site.get("container_runtime") is None

    def test_unknown_host_get_returns_default(
        self, fake_hostname: Callable[[str], None]
    ) -> None:
        # Field access on an unmatched site shouldn't require an explicit
        # truthiness guard at every call site — that's the whole point of
        # returning an empty HostSite rather than None.
        fake_hostname("generic-laptop")
        assert detect_current_site().get("scratch_root", "/tmp") == "/tmp"


# ---- env-marker detection (JupyterHub deployments) ------------------------


def test_detect_site_from_env_matches_jupyterhub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lightcone.engine.site_registry import detect_site_from_env

    monkeypatch.delenv("DASK_GATEWAY__ADDRESS", raising=False)
    assert detect_site_from_env() is None
    monkeypatch.setenv("DASK_GATEWAY__ADDRESS", "http://proxy/services/dask-gateway")
    assert detect_site_from_env() == "jupyterhub"


def test_env_markers_win_over_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pod's hostname is noise; the injected env is the signal."""
    from lightcone.engine.site_registry import detect_current_site

    monkeypatch.setenv("DASK_GATEWAY__ADDRESS", "http://proxy/services/dask-gateway")
    monkeypatch.setattr(
        "lightcone.engine.site_registry.socket.gethostname",
        lambda: "login29.chn.perlmutter.nersc.gov",
    )
    site = detect_current_site()
    assert site.key == "jupyterhub"
    assert site.get("container_runtime") == "kubernetes"
    assert site.get("scratch_root") == "$HOME"


def test_no_markers_falls_back_to_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lightcone.engine.site_registry import detect_current_site

    monkeypatch.delenv("DASK_GATEWAY__ADDRESS", raising=False)
    monkeypatch.setattr(
        "lightcone.engine.site_registry.socket.gethostname",
        lambda: "login29.chn.perlmutter.nersc.gov",
    )
    assert detect_current_site().key == "perlmutter"


def test_hub_scratch_resolves_to_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No separate scratch space on the hub — home IS the shared volume."""
    import os

    from lightcone.engine.scratch import resolve_scratch_root

    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.delenv("LIGHTCONE_SCRATCH", raising=False)
    monkeypatch.setenv("DASK_GATEWAY__ADDRESS", "http://proxy/services/dask-gateway")
    assert resolve_scratch_root(project) == Path(os.environ["HOME"])
