"""Managed venues share the existing execution path without sharing run identity."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import _Inline
from test_materialize import _SPEC, _UNIVERSE, _clone

from lightcone.engine import clusters, container, dataset, project
from lightcone.engine import materialize as engine
from lightcone.engine.project import ProjectError


@pytest.fixture
def root(analysis: Callable[..., Path]) -> Path:
    return analysis(_SPEC, universes={"baseline": _UNIVERSE})


def _record(directory: Path, backend: str = "slurm") -> clusters.Record:
    return clusters.Record(
        directory,
        {"id": "20260925-120000-test", "backend": backend, "label": "Shared compute"},
        state="running",
    )


def test_selection_is_shared_by_execution_announcement_and_report(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = _record(root)
    selections = []
    connections = []
    announcements = []

    def choose(path: Path) -> clusters.Record:
        selections.append(path)
        return selected

    @contextmanager
    def connect(path: Path, attached: clusters.Record | None) -> Iterator[_Inline]:
        connections.append((path, attached))
        yield _Inline()

    monkeypatch.setattr(clusters, "attached_cluster", choose)
    monkeypatch.setattr(engine, "cluster_for_run", connect)

    report = engine.materialize(root, ["first"], on_venue=announcements.append)

    expected = {"kind": "cluster", "backend": "slurm", "id": selected.id, "label": selected.label}
    assert selections == [root]
    assert connections == [(root, selected)]
    assert announcements == [expected]
    assert json.loads(json.dumps(report.as_dict()))["venue"] == expected
    assert report.made == ["baseline/first"]


def test_allocation_precedence_ignores_the_registry(
    root: Path, inline: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SLURM_JOB_ID", "31415926")
    monkeypatch.setenv("SLURM_JOB_NUM_NODES", "4")

    def forbidden(path: Path) -> None:
        pytest.fail("an active allocation must not inspect managed clusters")

    monkeypatch.setattr(clusters, "attached_cluster", forbidden)

    report = engine.materialize(root, ["first"])

    assert report.venue == {"kind": "allocation", "nodes": 4}


@pytest.mark.parametrize("backend", ["slurm", "gateway"])
def test_remote_cluster_allows_the_driver_on_a_login_node(
    root: Path, inline: None, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    monkeypatch.setenv("NERSC_HOST", "perlmutter")
    monkeypatch.setattr(clusters, "attached_cluster", lambda path: _record(root, backend))

    report = engine.materialize(root, ["first"])

    assert report.made == ["baseline/first"]
    assert report.venue is not None and report.venue["backend"] == backend


@pytest.mark.parametrize("backend", [None, "local"])
def test_local_execution_still_refuses_a_login_node(
    root: Path, monkeypatch: pytest.MonkeyPatch, backend: str | None
) -> None:
    monkeypatch.setenv("NERSC_HOST", "perlmutter")
    selected = None if backend is None else _record(root, backend)
    monkeypatch.setattr(clusters, "attached_cluster", lambda path: selected)

    with pytest.raises(ProjectError, match="login node"):
        engine.materialize(root, [])


def test_announcement_precedes_connection_and_image_preparation(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = []
    monkeypatch.setattr(clusters, "attached_cluster", lambda path: _record(root))

    @contextmanager
    def connect(path: Path, attached: clusters.Record | None) -> Iterator[_Inline]:
        events.append("connect")
        yield _Inline()

    def prepare(path: Path, *, build: bool) -> None:
        events.append("prepare")
        raise ProjectError("stop before image preparation")

    monkeypatch.setattr(engine, "cluster_for_run", connect)
    monkeypatch.setattr(container, "runtime_for_run", prepare)

    with pytest.raises(ProjectError, match="stop before image preparation"):
        engine.materialize(root, [], on_venue=lambda selected: events.append("announce"))

    assert events == ["announce", "connect", "prepare"]


def test_failed_attachment_never_starts_local_work(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(clusters, "attached_cluster", lambda path: _record(root))

    def unreachable(record: clusters.Record, path: Path) -> None:
        raise ProjectError("Shared compute could not be reached")

    def forbidden(path: Path, *, build: bool) -> None:
        pytest.fail("failed attachment must refuse before image preparation or local work")

    monkeypatch.setattr(clusters, "client", unreachable)
    monkeypatch.setattr(container, "runtime_for_run", forbidden)

    with pytest.raises(ProjectError, match="Shared compute could not be reached"):
        engine.materialize(root, [])

    assert not (root / "results/baseline/first.txt").exists()


@pytest.mark.parametrize(
    ("hosts", "nodes", "message"),
    [
        (("node-a", "node-b"), 2, "node-local"),
        (("node-a", "node-a"), 1, "reached image preparation"),
        (("node-a", "node-a"), 2, "node-local"),
    ],
)
def test_attached_container_guard_counts_hosts_before_building(
    root: Path, monkeypatch: pytest.MonkeyPatch, hosts: tuple[str, str], nodes: int, message: str
) -> None:
    selected = _record(root)
    selected.data["slurm"] = {"nodes": nodes}
    monkeypatch.setattr(clusters, "attached_cluster", lambda path: selected)
    monkeypatch.setattr(project, "mode", lambda path: "containerized")
    monkeypatch.setattr(container, "runtime_hint", lambda: "podman")

    @contextmanager
    def connect(record: clusters.Record, path: Path) -> Iterator[SimpleNamespace]:
        workers = {str(index): {"host": host} for index, host in enumerate(hosts)}
        yield SimpleNamespace(scheduler_info=lambda: {"workers": workers})

    def prepare(path: Path, *, build: bool) -> None:
        raise ProjectError("reached image preparation")

    monkeypatch.setattr(clusters, "client", connect)
    monkeypatch.setattr(container, "runtime_for_run", prepare)

    with pytest.raises(ProjectError, match=message):
        engine.materialize(root, [])


def test_concurrent_clones_do_not_share_tasks_or_close_the_cluster(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Identical project and output names must still execute in both clones."""
    from distributed import Client, LocalCluster

    other = tmp_path / "other"
    other.mkdir()
    clone = _clone(root, other).rename(other / root.name)
    selected = _record(tmp_path)
    monkeypatch.setattr(clusters, "attached_cluster", lambda path: selected)
    entered = threading.Barrier(2, timeout=30)
    finished = threading.Barrier(2, timeout=30)
    keys: list[str] = []
    submit = engine._Dask.submit

    def capture(self: engine._Dask, fn: Any, *args: Any, key: str) -> Any:
        keys.append(key)
        return submit(self, fn, *args, key=key)

    monkeypatch.setattr(engine._Dask, "submit", capture)

    with LocalCluster(
        n_workers=2, threads_per_worker=1, processes=False, dashboard_address=None
    ) as cluster:

        @contextmanager
        def connect(record: clusters.Record, path: Path) -> Iterator[Client]:
            with Client(cluster.scheduler_address, set_as_default=False) as client:
                entered.wait()
                try:
                    yield client
                finally:
                    # Keep both clients alive until both runs finish, so a
                    # reused scheduler key cannot escape by being forgotten.
                    finished.wait()

        monkeypatch.setattr(clusters, "client", connect)
        with Client(cluster.scheduler_address, set_as_default=False) as observer:
            unrelated = observer.submit(str, "another project", key="unrelated")
            with ThreadPoolExecutor(max_workers=2) as pool:
                runs = [pool.submit(engine.materialize, path, []) for path in (root, clone)]
                reports = [run.result(timeout=90) for run in runs]

            assert unrelated.result() == "another project"
            after = observer.submit(str, "still running", key="after-runs")
            assert after.result() == "still running"
            assert len(observer.scheduler_info()["workers"]) == 2

    assert len(keys) == len(set(keys)) == 4
    prefixes = {key.rsplit("/", 2)[0] for key in keys}
    assert len(prefixes) == 2 and all(prefix.startswith("lc/analysis/") for prefix in prefixes)
    for path, report in zip((root, clone), reports, strict=True):
        assert report.made == ["baseline/first", "baseline/second"]
        assert (path / "results/baseline/second.txt").read_text() == "alpha\n"
        assert not dataset.status(path)


@pytest.mark.parametrize("attached", [False, True])
def test_interruption_restores_only_after_owned_workers_stop(
    root: Path, monkeypatch: pytest.MonkeyPatch, attached: bool
) -> None:
    """A shared recipe can outlive disconnect; its output must not be restored underneath it."""
    selected = _record(root) if attached else None
    monkeypatch.setattr(clusters, "attached_cluster", lambda path: selected)
    events = []
    output = root / "results/baseline/first.txt"
    restore = dataset.restore

    class Interrupted(_Inline):
        def submit(self, fn: Callable[..., object], *args: object, key: str) -> object:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("recipe still writing\n")
            return object()

        def completed(self, handles: list[object]) -> Iterator[object]:
            raise RuntimeError("scheduler disconnected")

    @contextmanager
    def connect(path: Path, record: clusters.Record | None) -> Iterator[Interrupted]:
        try:
            yield Interrupted()
        finally:
            events.append("disconnected")
            output.write_text("last write before disconnect\n")

    def record_restore(path: Path, paths: Any) -> None:
        events.append("restore")
        restore(path, paths)

    monkeypatch.setattr(engine, "cluster_for_run", connect)
    monkeypatch.setattr(dataset, "restore", record_restore)

    error = ProjectError if attached else RuntimeError
    with pytest.raises(error, match="scheduler disconnected") as raised:
        engine.materialize(root, [])

    if attached:
        assert events == ["disconnected"]
        assert output.read_text() == "last write before disconnect\n"
        assert "Stop the cluster" in str(raised.value)
    else:
        assert events == ["disconnected", "restore", "restore"]
        assert not output.exists()
        assert not dataset.status(root)
