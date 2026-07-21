"""Shared test fixtures for lightcone-cli tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_ambient_hub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralize lightcone-hub env-marker site detection for every test.

    ``detect_current_site`` checks env markers *before* hostnames, so a
    suite running where JUPYTERHUB_USER and DASK_GATEWAY__ADDRESS are
    ambient (e.g. inside a lightcone-hub singleuser pod — exactly the
    environment this codebase targets) would otherwise resolve every
    site-dependent expectation (runtime detection, scratch roots,
    hostname-mocked site tests) to the hub site. Tests that want the
    markers set them explicitly via monkeypatch.setenv, which composes
    fine with this fixture.
    """
    monkeypatch.delenv("JUPYTERHUB_USER", raising=False)
    monkeypatch.delenv("DASK_GATEWAY__ADDRESS", raising=False)
    # Same reasoning for the BinderHub build seam: an ambient JupyterHub
    # API token (or an explicit binder URL) would make binder_available()
    # true and route `lc build`/`lc run` image resolution through a real
    # HTTP endpoint mid-suite.
    monkeypatch.delenv("JUPYTERHUB_API_TOKEN", raising=False)
    monkeypatch.delenv("LIGHTCONE_BINDER_URL", raising=False)
    monkeypatch.delenv("LIGHTCONE_BUILD_BUCKET", raising=False)
    monkeypatch.delenv("LIGHTCONE_REGISTRY", raising=False)
