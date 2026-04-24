"""Shared test fixtures for lightcone-cli tests."""

from __future__ import annotations

import dagster as dg
import pytest


@pytest.fixture
def parsl_local_pilot():
    """Yield with a Parsl DFK loaded with a single LocalProvider-backed
    WorkQueueExecutor labelled 'cpu'. Cleaned up on test exit.

    Use for integration tests that need to actually run bash_app tasks
    without booting a real SLURM allocation.

    Skips the test cleanly if ndcctools (WorkQueue) isn't installed.
    """
    try:
        import parsl
        from parsl.config import Config
        from parsl.executors import WorkQueueExecutor
        from parsl.providers import LocalProvider
    except ImportError as e:
        pytest.skip(f"Parsl not available: {e}")

    try:
        import work_queue  # noqa: F401
    except ImportError:
        pytest.skip("ndcctools (WorkQueue) not installed")

    config = Config(
        executors=[
            WorkQueueExecutor(
                label="cpu",
                provider=LocalProvider(init_blocks=1, min_blocks=1, max_blocks=1),
                autolabel=False,
                autocategory=False,
            ),
        ],
        strategy="none",
    )
    parsl.load(config)
    try:
        yield
    finally:
        parsl.dfk().cleanup()
        parsl.clear()


def materialize_via_dagster(
    instance: dg.DagsterInstance, universe_id: str, output_id: str
) -> None:
    """Create a Dagster materialization event for the given output."""

    @dg.asset(name=output_id, key_prefix=[universe_id])
    def _trivial_asset():
        return dg.MaterializeResult()

    dg.materialize([_trivial_asset], instance=instance)


@pytest.fixture(autouse=True)
def _fake_config(tmp_path, monkeypatch):
    """Ensure a config.yaml exists so the CLI auto-trigger doesn't fire.

    The ``main`` group callback checks for ``~/.lightcone/config.yaml`` and
    launches the setup wizard when it is missing.  This fixture creates a
    temporary config so that tests which invoke CLI commands are not
    interrupted by the wizard.

    Individual tests that want to exercise the auto-trigger behaviour
    should override ``get_config_path`` themselves.
    """
    config_path = tmp_path / "lightcone_cfg" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("default_target: fake\n")
    monkeypatch.setattr(
        "lightcone.engine.targets.get_config_path", lambda: config_path
    )
