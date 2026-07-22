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
    assert _run_shell("true")[0] == 0
    assert _run_shell("false")[0] != 0


def test_run_shell_runs_under_shell() -> None:
    """We rely on shell=True so recipes can use pipes and env expansion."""
    assert _run_shell("echo hi | grep hi >/dev/null")[0] == 0


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


def test_run_shell_returns_sentinel_block() -> None:
    """Sentinel-prefixed lines come back (prefix intact) as the block;
    everything else is dropped."""
    from lightcone.engine.runner import SENTINEL

    rc, block = _run_shell(
        f"echo '{SENTINEL}hello'; echo noise; echo '{SENTINEL}world' >&2"
    )
    assert rc == 0
    assert block == f"{SENTINEL}hello\n{SENTINEL}world\n"


def test_run_shell_failure_without_sentinel_forwards_raw_tail() -> None:
    """A child snakemake that dies before the rule body (import error,
    missing package in the worker image) must not vanish into worker
    logs — its raw output comes back sentinel-framed."""
    from lightcone.engine.runner import SENTINEL

    rc, block = _run_shell("echo bootstrap-crash >&2; exit 3")
    assert rc == 3
    assert block.startswith(SENTINEL)
    assert "bootstrap-crash" in block


def test_run_shell_success_drops_noise() -> None:
    rc, block = _run_shell("echo just-noise")
    assert rc == 0
    assert block == ""


def test_unpack_result_accepts_legacy_int() -> None:
    """Workers running an older lightcone-cli release return a bare int."""
    from snakemake_executor_plugin_dask.executor import _unpack_result

    assert _unpack_result(1) == (1, "")
    assert _unpack_result((0, "block\n")) == (0, "block\n")


def test_connect_client_requires_rendezvous(
    monkeypatch: object,
) -> None:
    import pytest
    from snakemake_interface_common.exceptions import WorkflowError

    from snakemake_executor_plugin_dask.executor import _connect_client

    monkeypatch.delenv("DASK_SCHEDULER_ADDRESS", raising=False)  # type: ignore[attr-defined]
    monkeypatch.delenv("LIGHTCONE_GATEWAY_CLUSTER", raising=False)  # type: ignore[attr-defined]
    with pytest.raises(WorkflowError, match="LIGHTCONE_GATEWAY_CLUSTER"):
        _connect_client()


def test_connect_client_gateway_rendezvous_by_name(
    monkeypatch: object,
) -> None:
    """With LIGHTCONE_GATEWAY_CLUSTER set, the executor rejoins the run's
    cluster through the Gateway API — never dials gateway:// directly."""
    import sys
    from types import SimpleNamespace

    from snakemake_executor_plugin_dask.executor import _connect_client

    record: dict[str, object] = {}

    class _FakeClient:
        def close(self) -> None:
            record["client_closed"] = True

    class _FakeCluster:
        def get_client(self) -> _FakeClient:
            return _FakeClient()

        def close(self) -> None:
            record["cluster_closed"] = True

    class _FakeGateway:
        def connect(self, name: str, shutdown_on_close: bool = True):
            record["connected"] = name
            record["shutdown_on_close"] = shutdown_on_close
            return _FakeCluster()

    monkeypatch.setitem(  # type: ignore[attr-defined]
        sys.modules, "dask_gateway", SimpleNamespace(Gateway=_FakeGateway)
    )
    monkeypatch.setenv("LIGHTCONE_GATEWAY_CLUSTER", "hub.abc")  # type: ignore[attr-defined]
    monkeypatch.delenv("DASK_SCHEDULER_ADDRESS", raising=False)  # type: ignore[attr-defined]

    client, closer = _connect_client()
    assert record["connected"] == "hub.abc"
    assert record["shutdown_on_close"] is False, (
        "the executor is a guest — closing it must not cull the run's cluster"
    )
    closer()
    assert record.get("client_closed") is True
    assert record.get("cluster_closed") is True
