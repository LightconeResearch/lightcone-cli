"""Shared test fixtures for lightcone-cli tests."""

from __future__ import annotations

import dagster as dg
import pytest


def materialize_via_dagster(
    instance: dg.DagsterInstance, universe_id: str, output_id: str
) -> None:
    """Create a Dagster materialization event for the given output."""

    @dg.asset(name=output_id, key_prefix=[universe_id])
    def _trivial_asset():
        return dg.MaterializeResult()

    dg.materialize([_trivial_asset], instance=instance)


@pytest.fixture(autouse=True)
def _isolate_lightcone_home(tmp_path, monkeypatch):
    """Redirect ``~/.lightcone/`` reads/writes to a per-test temp dir.

    Pilot CRUD, cluster cache, and worker-env paths all derive from
    ``Path.home()``.  Pinning ``HOME`` to a tmp dir keeps tests from
    touching the developer's real config.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
