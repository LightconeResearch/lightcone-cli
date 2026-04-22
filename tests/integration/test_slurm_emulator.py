"""Integration tests for SLURM mode via the Docker emulator (ADR-0001 §5.3).

All tests require the ``slurm_emulator`` fixture (Docker daemon + compose cluster).
They are marked ``needs_slurm_docker`` and ``slow`` so they are excluded from the
default test run and collected only when explicitly requested:

    pytest -m needs_slurm_docker tests/integration/
    LIGHTCONE_USE_DAGSTER_SLURM=1 pytest -m needs_slurm_docker tests/integration/

The emulator credentials are: localhost:2223, user=submitter, password=submitter.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import dagster as dg
import pytest

from lightcone.engine.compute_adapter import (
    build_compute_resource,
    has_dagster_slurm,
    prepare_payload,
    stage_artifacts,
)

pytestmark = [
    pytest.mark.needs_slurm_docker,
    pytest.mark.slow,
    pytest.mark.skipif(
        not has_dagster_slurm(),
        reason="dagster-slurm not installed (uv sync --group slurm-next)",
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_asset(
    cr: Any,
    *,
    command: str,
    universe_id: str = "baseline",
    output_id: str = "out",
    project_root: Path,
    target_config: dict[str, Any],
    extra_sbatch_directives: list[str] | None = None,
) -> dg.AssetsDefinition:
    """Return a single Dagster asset that runs *command* via the emulator."""

    @dg.asset(name=output_id, key_prefix=[universe_id])
    def _asset(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
        payload_path, ctx = prepare_payload(
            command=command,
            universe_id=universe_id,
            output_id=output_id,
            project_root=project_root,
            extra_sbatch_directives=extra_sbatch_directives,
            target_config=target_config,
        )
        completed = cr.run(
            context=context,
            payload_path=str(payload_path),
            poll_timeout=300,
        )
        stage_artifacts(completed, ctx, run_id=context.run_id)
        return completed.get_materialize_result()

    return _asset


# ---------------------------------------------------------------------------
# §5.3 test_emulator_end_to_end_single_asset
# ---------------------------------------------------------------------------


def test_emulator_end_to_end_single_asset(
    slurm_emulator: None,
    emulator_target: dict[str, Any],
    tmp_path: Path,
) -> None:
    """A one-step recipe executes on the SLURM emulator and materialises."""
    cr = build_compute_resource(emulator_target)
    asset = _make_asset(
        cr,
        command="python3 -c \"import sys; sys.exit(0)\"",
        project_root=tmp_path,
        target_config=emulator_target,
    )
    result = dg.materialize([asset])
    assert result.success


# ---------------------------------------------------------------------------
# §5.3 test_emulator_metadata_propagation
# ---------------------------------------------------------------------------


def test_emulator_metadata_propagation(
    slurm_emulator: None,
    emulator_target: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Metadata reported by pipes.report_asset_materialization reaches Dagster."""
    cr = build_compute_resource(emulator_target)

    # The payload wrapper always reports exit_code + universe + output.
    asset = _make_asset(
        cr,
        command="python3 -c \"import sys; sys.exit(0)\"",
        project_root=tmp_path,
        target_config=emulator_target,
        output_id="meta_out",
    )
    result = dg.materialize([asset])
    assert result.success
    mat = result.get_asset_materialization_events()
    assert mat, "expected at least one materialization event"
    metadata = mat[0].step_materialization_data.materialization.metadata
    # The payload wrapper injects these keys via report_asset_materialization.
    assert "exit_code" in metadata
    assert "universe" in metadata
    assert "output" in metadata


# ---------------------------------------------------------------------------
# §5.3 test_emulator_non_zero_exit_fails_asset
# ---------------------------------------------------------------------------


def test_emulator_non_zero_exit_fails_asset(
    slurm_emulator: None,
    emulator_target: dict[str, Any],
    tmp_path: Path,
) -> None:
    """A recipe that exits non-zero causes the Dagster materialization to fail."""
    cr = build_compute_resource(emulator_target)
    asset = _make_asset(
        cr,
        command="python3 -c \"import sys; sys.exit(7)\"",
        project_root=tmp_path,
        target_config=emulator_target,
        output_id="fail_out",
    )
    # dagster raises on non-zero exit — catch the exception
    with pytest.raises(Exception):
        dg.materialize([asset])


# ---------------------------------------------------------------------------
# §5.3 test_emulator_resource_directives_respected
# ---------------------------------------------------------------------------


def test_emulator_resource_directives_respected(
    slurm_emulator: None,
    emulator_target: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Resource directives from extra_slurm_opts appear in the staged sbatch script."""
    from lightcone.engine.slurm_utils import resources_to_slurm_opts

    cr = build_compute_resource(emulator_target)
    resources = {"cpus": 1, "time_limit": "00:02:00"}

    @dg.asset(name="res_out", key_prefix=["baseline"])
    def _asset(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
        payload_path, ctx = prepare_payload(
            command="python3 -c \"import sys; sys.exit(0)\"",
            universe_id="baseline",
            output_id="res_out",
            project_root=tmp_path,
            target_config=emulator_target,
        )
        completed = cr.run(
            context=context,
            payload_path=str(payload_path),
            extra_slurm_opts=resources_to_slurm_opts(resources),
            poll_timeout=300,
        )
        stage_artifacts(completed, ctx, run_id=context.run_id)
        return completed.get_materialize_result()

    result = dg.materialize([_asset])
    assert result.success

    slurm_dir = tmp_path / "results" / ".slurm"
    sh_files = list(slurm_dir.glob("res_out_baseline.sh"))
    assert sh_files, "expected staged sbatch script"
    sbatch_content = sh_files[0].read_text()
    # dagster-slurm injects -c (cpus) and -t (time_limit) into the sbatch command
    assert "-c 1" in sbatch_content or "--cpus-per-task=1" in sbatch_content
    assert "00:02:00" in sbatch_content


# ---------------------------------------------------------------------------
# §5.3 test_emulator_sbatch_constraint_via_extra_directives
# ---------------------------------------------------------------------------


def test_emulator_sbatch_constraint_via_extra_directives(
    slurm_emulator: None,
    emulator_target: dict[str, Any],
    tmp_path: Path,
) -> None:
    """extra_sbatch_directives entries are present in the staged sbatch script."""
    cr = build_compute_resource(emulator_target)
    asset = _make_asset(
        cr,
        command="python3 -c \"import sys; sys.exit(0)\"",
        project_root=tmp_path,
        target_config=emulator_target,
        extra_sbatch_directives=["--comment=lightcone-test"],
        output_id="constraint_out",
    )
    result = dg.materialize([asset])
    assert result.success

    slurm_dir = tmp_path / "results" / ".slurm"
    sh_files = list(slurm_dir.glob("constraint_out_baseline.sh"))
    assert sh_files, "expected staged sbatch script"
    assert "--comment=lightcone-test" in sh_files[0].read_text()


# ---------------------------------------------------------------------------
# §5.3 test_emulator_log_streaming
# ---------------------------------------------------------------------------


def test_emulator_log_streaming(
    slurm_emulator: None,
    emulator_target: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Stdout lines emitted by the payload are captured in the staged .out file."""
    cr = build_compute_resource(emulator_target)

    # Emit 50 numbered lines to stdout
    py_snippet = (
        "import sys; "
        "[print(f'line {i}', flush=True) for i in range(50)]; "
        "sys.exit(0)"
    )
    asset = _make_asset(
        cr,
        command=f"python3 -c \"{py_snippet}\"",
        project_root=tmp_path,
        target_config=emulator_target,
        output_id="log_out",
    )
    result = dg.materialize([asset])
    assert result.success

    slurm_dir = tmp_path / "results" / ".slurm"
    out_files = list(slurm_dir.glob("log_out_baseline.out"))
    assert out_files, "expected staged stdout file"
    content = out_files[0].read_text()
    # All 50 lines must be present
    for i in range(50):
        assert f"line {i}" in content, f"missing 'line {i}' in stdout"


# ---------------------------------------------------------------------------
# §5.3 test_emulator_cancellation
# ---------------------------------------------------------------------------


def test_emulator_cancellation(
    slurm_emulator: None,
    emulator_target: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Terminating a Dagster run cancels the underlying SLURM job.

    Approach: run a long-sleeping recipe in a background thread and send
    SIGTERM to the pipes client's signal handler after a short delay.
    dagster-slurm registers a SIGTERM handler that cancels the job via
    ``scancel``; we assert the job no longer appears in squeue after that.
    """
    import signal
    import time

    from dagster_slurm import SSHConnectionResource
    from dagster_slurm.helpers.ssh_pool import SSHConnectionPool

    cr = build_compute_resource(emulator_target)
    errors: list[Exception] = []
    run_ids: list[str] = []

    @dg.asset(name="cancel_out", key_prefix=["baseline"])
    def _asset(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
        run_ids.append(context.run_id)
        payload_path, ctx = prepare_payload(
            command="python3 -c \"import time; time.sleep(300)\"",
            universe_id="baseline",
            output_id="cancel_out",
            project_root=tmp_path,
            target_config=emulator_target,
        )
        return cr.run(
            context=context,
            payload_path=str(payload_path),
            poll_timeout=60,
        ).get_materialize_result()

    def _run_asset() -> None:
        try:
            dg.materialize([_asset])
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    thread = threading.Thread(target=_run_asset, daemon=True)
    thread.start()

    # Give the job time to be submitted
    time.sleep(20)

    # Send SIGTERM to trigger dagster-slurm's cancellation handler
    signal.raise_signal(signal.SIGTERM)

    thread.join(timeout=60)

    # Verify via squeue in the emulator that no jobs remain
    ssh_cfg = emulator_target["ssh"]
    ssh_res = SSHConnectionResource(
        host=ssh_cfg["host"],
        port=ssh_cfg["port"],
        user=ssh_cfg["user"],
        password=ssh_cfg["password"],
    )
    with SSHConnectionPool(ssh_res) as pool:
        queue = pool.run("squeue --noheader 2>/dev/null || true").strip()

    assert queue == "", f"expected empty squeue after cancellation, got: {queue!r}"
