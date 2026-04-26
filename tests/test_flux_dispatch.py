"""Unit tests for Flux dispatch routing in `lc run`.

The routing logic is a pure function of env vars and the user's --executor
flag, so we test it directly rather than spinning up snakemake.
"""

from __future__ import annotations

import click
import pytest

from lightcone.cli.commands import _wrap_for_flux


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLUX_URI", raising=False)
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)


def _base() -> list[str]:
    return ["snakemake", "-s", "Snakefile", "--cores", "4"]


def test_no_env_runs_locally() -> None:
    assert _wrap_for_flux(_base(), "auto", verbose=False) == _base()


def test_explicit_local_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLUX_URI", "local:///tmp/flux-X/local-0")
    assert _wrap_for_flux(_base(), "local", verbose=False) == _base()


def test_flux_uri_routes_to_lightconeflux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLUX_URI", "local:///tmp/flux-X/local-0")
    out = _wrap_for_flux(_base(), "auto", verbose=False)
    assert out[0] == "snakemake"
    assert out[-2:] == ["--executor", "lightconeflux"]


def test_slurm_wraps_in_srun_flux_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLURM_JOB_ID", "12345")
    out = _wrap_for_flux(_base(), "auto", verbose=False)
    assert out[:5] == ["srun", "--mpi=pmi2", "flux", "start", "--"]
    assert out[-2:] == ["--executor", "lightconeflux"]


def test_flux_uri_wins_over_slurm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Already inside a Flux instance — don't try to start another."""
    monkeypatch.setenv("SLURM_JOB_ID", "12345")
    monkeypatch.setenv("FLUX_URI", "local:///tmp/flux-X/local-0")
    out = _wrap_for_flux(_base(), "auto", verbose=False)
    assert "srun" not in out
    assert out[-2:] == ["--executor", "lightconeflux"]


def test_explicit_flux_without_allocation_errors() -> None:
    with pytest.raises(click.UsageError):
        _wrap_for_flux(_base(), "flux", verbose=False)


def test_plugin_module_imports_without_flux_core() -> None:
    """The package must be importable on systems without flux-core installed.

    Snakemake imports the plugin module to read `common_settings` even before
    deciding to instantiate the executor.
    """
    import snakemake_executor_plugin_lightconeflux as mod

    assert mod.common_settings.non_local_exec is True
    assert mod.Executor is not None


def test_inject_memory_into_simple_slot_tree() -> None:
    """Single-slot tree (`from_command(num_tasks=N, cores_per_task=C)`)."""
    from snakemake_executor_plugin_lightconeflux.executor import _inject_memory

    resources = [
        {"type": "slot", "label": "task", "count": 1, "with": [{"type": "core", "count": 4}]}
    ]
    _inject_memory(resources, 8000)

    children = resources[0]["with"]
    assert {"type": "core", "count": 4} in children
    assert {"type": "memory", "count": 8000, "unit": "MB"} in children


def test_inject_memory_into_multinode_tree() -> None:
    """Multi-node tree (`from_command(..., num_nodes=K)`) — slot is nested."""
    from snakemake_executor_plugin_lightconeflux.executor import _inject_memory

    resources = [
        {
            "type": "node",
            "count": 2,
            "with": [
                {
                    "type": "slot",
                    "label": "task",
                    "count": 4,
                    "with": [{"type": "core", "count": 1}],
                }
            ],
        }
    ]
    _inject_memory(resources, 16000)

    slot_children = resources[0]["with"][0]["with"]
    assert any(c["type"] == "memory" and c["count"] == 16000 for c in slot_children)


def test_inject_memory_creates_with_list_when_missing() -> None:
    """A slot without an existing `with` key is rare but should still work."""
    from snakemake_executor_plugin_lightconeflux.executor import _inject_memory

    resources = [{"type": "slot", "label": "task", "count": 1}]
    _inject_memory(resources, 4000)
    assert resources[0]["with"] == [{"type": "memory", "count": 4000, "unit": "MB"}]
