"""Shared test fixtures for lightcone-cli tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_site_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep host-site tests independent of the machine running pytest."""
    monkeypatch.delenv("LIGHTCONE_SITE", raising=False)
    monkeypatch.delenv("NERSC_HOST", raising=False)
