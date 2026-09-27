"""Site placement is checked on actual workers, never inferred from a driver."""

from __future__ import annotations

import socket
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import CLUSTER_ID

from lightcone.engine import materialize as engine
from lightcone.engine import venue
from lightcone.engine.project import ProjectError


def test_a_login_node_refuses_with_compute_remedy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NERSC_HOST", "perlmutter")
    with pytest.raises(ProjectError, match="login node") as raised:
        venue.require_compute_node("lc compute launch")
    assert "lc compute resources" in str(raised.value)
    assert "lc compute launch" in str(raised.value)
    assert "lc materialize --check" in str(raised.value)


def test_a_job_id_on_a_login_host_is_not_compute_placement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NERSC_HOST", "perlmutter")
    monkeypatch.setenv("SLURM_JOB_ID", "12345")
    with pytest.raises(ProjectError, match="alone does not prove"):
        venue.require_compute_node()
    monkeypatch.setenv("SLURMD_NODENAME", "another-compute-node")
    with pytest.raises(ProjectError, match="unverified"):
        venue.require_compute_node()


def test_a_matching_compute_host_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NERSC_HOST", "perlmutter")
    monkeypatch.setenv("SLURM_JOB_ID", "12345")
    monkeypatch.setenv("SLURMD_NODENAME", socket.gethostname().split(".")[0])
    venue.require_compute_node()


def test_a_workstation_needs_no_slurm_environment() -> None:
    venue.require_compute_node()


def test_another_site_uses_the_same_placement_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(venue, "_SITES", (venue._Site("Other center", "OTHER_SITE"),))
    monkeypatch.setenv("OTHER_SITE", "cluster")
    with pytest.raises(ProjectError, match="Other center"):
        venue.require_compute_node()


def test_project_inspection_needs_no_cluster_on_a_login_node(
    analysis: Callable[..., Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = analysis('version: "0.0.13"\nname: empty\ninputs: []\noutputs: []\n')
    monkeypatch.setenv("NERSC_HOST", "perlmutter")
    assert engine.check(root, []).ok
    assert engine.status(root).outputs == []


def test_submission_driver_is_not_mistaken_for_the_selected_worker(
    analysis: Callable[..., Path], inline: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = analysis('version: "0.0.13"\nname: empty\ninputs: []\noutputs: []\n')
    monkeypatch.setenv("NERSC_HOST", "perlmutter")
    # The inline fixture supplies an already validated execution seam.
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok


def test_rerun_entry_point_still_checks_actual_placement(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from lightcone.engine import worker

    monkeypatch.setenv("NERSC_HOST", "perlmutter")
    assert worker.main(["baseline/first"]) == 2
    assert "login node" in capsys.readouterr().err
