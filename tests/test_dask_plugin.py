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


def test_cancel_jobs_does_not_close_client() -> None:
    """Snakemake calls cancel_jobs for partial cancellations. The Dask
    client must survive so subsequent submissions in the same run still
    work — only ``shutdown()`` is allowed to close the client.
    """
    from snakemake_executor_plugin_dask.executor import DaskExecutor

    closed = {"count": 0}

    class _FakeFuture:
        def __init__(self) -> None:
            self.cancelled = False

        def done(self) -> bool:
            return False

        def cancel(self) -> None:
            self.cancelled = True

    class _FakeClient:
        def close(self) -> None:
            closed["count"] += 1

    class _FakeLogger:
        def warning(self, _msg: str) -> None:
            pass

    executor = DaskExecutor.__new__(DaskExecutor)
    executor._client = _FakeClient()  # type: ignore[attr-defined]
    executor.logger = _FakeLogger()  # type: ignore[attr-defined]

    future = _FakeFuture()
    job = SimpleNamespace(external_jobid="x", aux={"future": future})
    executor.cancel_jobs([job])  # type: ignore[arg-type]

    assert future.cancelled is True
    assert closed["count"] == 0, "cancel_jobs must not close the dask client"
