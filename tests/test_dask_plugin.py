"""Unit tests for the dask Snakemake executor plugin.

The Snakemake executor base classes are heavy and tied to a live Workflow
instance, so we don't instantiate the plugin's `Executor` directly here.
We test the pure helpers (`_run_shell`, `_build_resources`) and the
package-level discovery contract that Snakemake uses.
"""

from __future__ import annotations

from types import SimpleNamespace

from snakemake_executor_plugin_dask.executor import (
    _build_resources,
    _run_shell,
)


def _job(threads: int = 1, **resources: float) -> SimpleNamespace:
    return SimpleNamespace(threads=threads, resources=resources)


def test_run_shell_propagates_exit_code() -> None:
    assert _run_shell("true") == 0
    assert _run_shell("false") != 0


def test_run_shell_runs_under_shell() -> None:
    """We rely on shell=True so recipes can use pipes and env expansion."""
    assert _run_shell("echo hi | grep hi >/dev/null") == 0


def test_build_resources_default_uses_threads() -> None:
    res = _build_resources(_job(threads=4))
    assert res == {"cpus": 4.0}


def test_build_resources_cpus_per_task_overrides_threads() -> None:
    res = _build_resources(_job(threads=4, cpus_per_task=8))
    assert res["cpus"] == 8.0


def test_build_resources_mem_mb_to_bytes() -> None:
    res = _build_resources(_job(threads=1, mem_mb=8000))
    assert res["memory"] == 8e9


def test_build_resources_gpus_passthrough() -> None:
    res = _build_resources(_job(threads=1, gpus=2))
    assert res["gpus"] == 2.0


def test_build_resources_gpus_per_task_takes_precedence() -> None:
    res = _build_resources(_job(threads=1, gpus=2, gpus_per_task=4))
    assert res["gpus"] == 4.0


def test_build_resources_full_set() -> None:
    res = _build_resources(_job(threads=8, mem_mb=32000, gpus=1))
    assert res == {"cpus": 8.0, "memory": 3.2e10, "gpus": 1.0}


def test_plugin_module_exposes_common_settings_and_executor() -> None:
    """Snakemake imports the plugin module to read these on discovery."""
    import snakemake_executor_plugin_dask as mod

    assert mod.common_settings.non_local_exec is True
    assert mod.Executor is not None
