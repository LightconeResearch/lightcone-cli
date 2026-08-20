"""Tests for `lightcone.engine.venue` — the SLURM allocation, and the guard.

The venue's whole surface is ambient: environment variables and an srun
on PATH. So the suite fakes the *host* rather than the code — SLURM
variables set deliberately, and a bash stub standing in for srun that
launches the worker command locally — and the end-to-end tests run the
real graph through the real detection, bind, launch and teardown path on
any machine.
"""

from __future__ import annotations

import os
import textwrap
from collections.abc import Callable
from pathlib import Path

import pytest

from lightcone.engine import dataset, venue
from lightcone.engine import materialize as engine
from lightcone.engine.project import ProjectError

_SPEC = """
version: "0.0.13"
name: analysis

inputs:
  - id: catalog
    type: data
    source: data/catalog.fits

outputs:
  - id: first
    type: metric
    decisions: [method]
    recipe:
      command: echo {decisions.method} > {output}/value.txt

  - id: second
    type: report
    inputs: [first]
    recipe:
      command: cat {inputs.first}/value.txt > {output}/copy.txt

decisions:
  method:
    label: Method
    default: alpha
    options:
      alpha: {label: alpha}
      beta: {label: beta}
"""

_UNIVERSE = "id: baseline\ndecisions:\n  method: alpha\n"


@pytest.fixture
def root(analysis: Callable[..., Path]) -> Path:
    return analysis(_SPEC, universes={"baseline": _UNIVERSE})


def _allocation(monkeypatch: pytest.MonkeyPatch, *, nodes: int = 1) -> None:
    """The environment salloc leaves on the head compute node."""
    monkeypatch.setenv("SLURM_JOB_ID", "12345")
    monkeypatch.setenv("SLURM_JOB_NUM_NODES", str(nodes))
    monkeypatch.setenv("SLURM_CPUS_ON_NODE", "2")
    # The literal IP keeps the scheduler bind hermetic against CI DNS.
    monkeypatch.setenv("SLURMD_NODENAME", "127.0.0.1")


def _stub_srun(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    """Put a bash srun with *body* on PATH."""
    stubs = tmp_path / "stubs"
    stubs.mkdir(exist_ok=True)
    stub = stubs / "srun"
    stub.write_text(f"#!/usr/bin/env bash\n{textwrap.dedent(body)}")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{stubs}{os.pathsep}{os.environ['PATH']}")


#: What a real srun does from lc's point of view: drop the step flags,
#: honor ``--ntasks``, run the worker command.
_FAITHFUL = """
ntasks=1
while [[ $1 == --* ]]; do
  case "$1" in --ntasks=*) ntasks="${1#--ntasks=}";; esac
  shift
done
pids=()
for _ in $(seq "$ntasks"); do
  "$@" &
  pids+=($!)
done
wait "${pids[@]}"
"""


# ---- the login guard --------------------------------------------------------


def test_a_login_node_refuses_with_both_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NERSC_HOST", "perlmutter")

    with pytest.raises(ProjectError) as err:
        venue.require_compute_node()

    message = str(err.value)
    assert "salloc" in message
    assert "sbatch" in message
    assert "--wrap 'lc materialize'" in message


def test_the_guard_fires_before_anything_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even a directory that is not a project refuses as a login node — the
    allocation is the remedy with queue latency, so it comes first."""
    monkeypatch.setenv("NERSC_HOST", "perlmutter")

    with pytest.raises(ProjectError, match="login node"):
        engine.materialize(tmp_path, [])


def test_an_allocation_passes_the_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NERSC_HOST", "perlmutter")
    monkeypatch.setenv("SLURM_JOB_ID", "12345")

    venue.require_compute_node()


def test_a_machine_that_is_not_nersc_passes_the_guard() -> None:
    venue.require_compute_node()


def test_the_read_only_verbs_work_on_a_login_node(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--check` and `status` are for finding out where a project stands,
    and a login node is exactly where that question gets asked."""
    monkeypatch.setenv("NERSC_HOST", "perlmutter")

    assert set(engine.check(root, []).planned) == {"baseline/first", "baseline/second"}
    assert engine.status(root).counts["stale"] == 2


# ---- the srun invocation ----------------------------------------------------


def test_the_srun_argv_spans_the_allocation() -> None:
    import sys

    argv = venue._srun_argv("tcp://10.0.0.1:8786", 4, 128, "/scratch")

    assert argv == [
        "srun",
        "--overlap",
        "--ntasks=4",
        "--ntasks-per-node=1",
        "--cpus-per-task=128",
        sys.executable,
        "-m",
        "distributed.cli.dask_worker",
        "tcp://10.0.0.1:8786",
        "--nthreads", "128",
        "--nworkers", "1",
        "--no-dashboard",
        "--no-nanny",
        "--death-timeout", "60",
        "--memory-limit", "0",
        "--local-directory", "/scratch",
    ]  # fmt: skip


def test_slurm_without_srun_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A leaked SLURM_JOB_ID — a container, a copied environment — must be
    a loud refusal, never a silent fall back to running locally."""
    _allocation(monkeypatch)
    monkeypatch.setenv("PATH", str(tmp_path))

    with pytest.raises(ProjectError, match="srun is not on PATH"):
        with venue.slurm_client():
            pass


# ---- the allocation, end to end ---------------------------------------------


def test_a_run_spans_the_allocation(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole venue path — detection, scheduler bind, worker launch,
    a real graph in a real worker process, teardown — with only srun faked."""
    _allocation(monkeypatch)
    _stub_srun(tmp_path, monkeypatch, _FAITHFUL)

    report = engine.materialize(root, [])

    assert report.made == ["baseline/first", "baseline/second"]
    assert (root / "results/baseline/second/copy.txt").read_text() == "alpha\n"
    assert not dataset.status(root)


def test_every_allocated_node_gets_a_worker(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two declared nodes, a stub honoring ``--ntasks`` — completion proves
    the run waited for both workers and used the count it was granted."""
    _allocation(monkeypatch, nodes=2)
    _stub_srun(tmp_path, monkeypatch, _FAITHFUL)

    report = engine.materialize(root, [])

    assert report.made == ["baseline/first", "baseline/second"]
    assert not dataset.status(root)


def test_workers_that_never_connect_refuse_and_reap_srun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _allocation(monkeypatch)
    pidfile = tmp_path / "srun.pid"
    _stub_srun(tmp_path, monkeypatch, f"echo $$ > {pidfile}\nexec sleep 60\n")
    monkeypatch.setattr(venue, "_WORKER_WAIT", 2.0)
    monkeypatch.setattr(venue, "_REAP_GRACE", 0.5)

    with pytest.raises(ProjectError, match=r"expected 1 dask worker"):
        with venue.slurm_client():
            pass

    pid = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_a_dead_srun_reports_its_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """srun failing is srun's error, reported as such — not a two-minute
    wait that ends in a timeout blaming the workers."""
    _allocation(monkeypatch)
    _stub_srun(tmp_path, monkeypatch, "exit 3\n")
    monkeypatch.setattr(venue, "_REAP_GRACE", 0.5)

    with pytest.raises(ProjectError, match="exited with code 3"):
        with venue.slurm_client():
            pass


# ---- the fixes the first review asked for -----------------------------------


def test_slurm_counts_that_are_not_numbers_refuse(monkeypatch: pytest.MonkeyPatch) -> None:
    """A mangled count is the same leak class as SLURM_JOB_ID without an
    srun, and gets the same curated refusal — never a ValueError traceback."""
    monkeypatch.setenv("SLURM_CPUS_ON_NODE", "72(x2)")
    with pytest.raises(ProjectError, match="SLURM_CPUS_ON_NODE"):
        venue._int_env("SLURM_CPUS_ON_NODE", 1)

    monkeypatch.setenv("SLURM_JOB_ID", "12345")
    monkeypatch.setenv("SLURM_JOB_NUM_NODES", "four")
    with pytest.raises(ProjectError, match="SLURM_JOB_NUM_NODES"):
        venue.allocation_nodes()


def test_allocation_nodes_answers_zero_outside_an_allocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert venue.allocation_nodes() == 0
    monkeypatch.setenv("SLURM_JOB_ID", "12345")
    assert venue.allocation_nodes() == 1  # in one, count unstated
    monkeypatch.setenv("SLURM_JOB_NUM_NODES", "3")
    assert venue.allocation_nodes() == 3


def test_an_unresolvable_node_name_is_a_refusal_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SLURM's NodeName is an alias, not a promise of a hostname."""
    _allocation(monkeypatch)
    monkeypatch.setenv("SLURMD_NODENAME", "no-such-node.invalid")
    _stub_srun(tmp_path, monkeypatch, "exit 0\n")

    with pytest.raises(ProjectError, match="did not resolve"):
        with venue.slurm_client():
            pass


def test_a_multi_node_allocation_refuses_a_node_local_image_store(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """podman's and docker's stores are node-local; only podman-hpc's
    migrate makes an image visible to the allocation's other nodes.
    Mutation check: the same state under podman-hpc reaches runtime
    resolution — the gate itself is what stands between them."""
    from lightcone.engine import container

    text = (root / "pyproject.toml").read_text()
    (root / "pyproject.toml").write_text(text + '\n[tool.lightcone.image]\napt-install = ["bc"]\n')
    dataset.save(root, [root], "containerize")
    _allocation(monkeypatch, nodes=2)
    monkeypatch.setattr(container, "runtime_hint", lambda: "podman")

    with pytest.raises(ProjectError, match="node-local"):
        engine.materialize(root, [])

    monkeypatch.setattr(container, "runtime_hint", lambda: "podman-hpc")

    def reached(r: Path, *, build: bool) -> None:
        raise ProjectError("reached runtime resolution")

    monkeypatch.setattr(container, "runtime_for_run", reached)
    with pytest.raises(ProjectError, match="reached runtime resolution"):
        engine.materialize(root, [])


def test_the_rerun_entry_point_is_guarded_like_materialize(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A rerun executes a recipe, so a NERSC login node refuses it too."""
    from lightcone.engine import worker

    monkeypatch.setenv("NERSC_HOST", "perlmutter")

    assert worker.main(["baseline/first"]) == 2
    err = capsys.readouterr().err
    assert "login node" in err and "salloc" in err
