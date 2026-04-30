"""Tests for the site registry — site detection and the HostSite wrapper."""
from __future__ import annotations

from collections.abc import Callable

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
