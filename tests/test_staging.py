"""Execution trees with real git-annex: exact keys, links, isolation and collection."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from conftest import CLUSTER_ID, _Inline, clone_of

from lightcone.engine import container, dataset, plan, project, staging
from lightcone.engine import materialize as engine

_SPEC = '''
spec_version: "0.1.0"
inputs:
  catalog: {path: data/catalog.txt}
outputs:
  first:
    type: data
    format: txt
    inputs: [catalog]
    recipe:
      command: test -L {inputs.catalog} && cat {inputs.catalog} > {output}
  second:
    type: data
    format: txt
    inputs: [first]
    recipe:
      command: test -L {inputs.first} && cat {inputs.first} > {output}
'''


@pytest.fixture
def root(analysis: Callable[..., Path]) -> Path:
    return analysis(_SPEC, {"data/catalog.txt": "original\n", "code.py": "original\n"})


def test_pinned_annex_key_is_a_symlink_even_after_the_working_file_changes(root: Path) -> None:
    commit = dataset.head(root)
    tree = staging.take(root, commit)
    try:
        source = root / "data/catalog.txt"
        source.unlink()
        source.write_text("new commit\n")
        dataset.save(root, [source], "replace catalog")
        staging.fill(root, tree.top, tree.root, commit, ["data/catalog.txt"], fetch=True)
        linked = tree.root / "data/catalog.txt"
        assert linked.is_symlink() and linked.read_text() == "original\n"
        key = dataset.keys(root, [f"{commit}:data/catalog.txt"])[f"{commit}:data/catalog.txt"]
        assert linked.resolve() == dataset.locations(root, [key])[key]
        assert (tree.top / ".git").is_file()
    finally:
        tree.close()


def test_probe_never_fetches_but_recipe_fetches_one_exact_key(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    clone = clone_of(root, tmp_path)
    tree = staging.take(clone, dataset.head(clone))
    calls: list[tuple[list[str], str]] = []
    run = project._run

    def capture(argv: list[str], **kwargs: Any) -> Any:
        calls.append((argv, kwargs.get("stdin", "")))
        return run(argv, **kwargs)

    monkeypatch.setattr(project, "_run", capture)
    try:
        staging.probe_files(tree)
        assert not (tree.root / "data/catalog.txt").exists()
        assert not any("get" in argv for argv, _ in calls)
        warnings = staging.fill(clone, tree.top, tree.root, tree.commit,
                                ["data/catalog.txt", "data/catalog.txt"], fetch=True)
        assert not warnings
        linked = tree.root / "data/catalog.txt"
        assert linked.is_symlink() and linked.read_text() == "original\n"
        requests = [stdin for argv, stdin in calls if "get" in argv]
        assert len(requests) == 1 and len(requests[0].splitlines()) == 1
        assert any("whereis" in argv and "--batch-keys" in argv for argv, _ in calls)
        assert any("contentlocation" in argv for argv, _ in calls)
    finally:
        tree.close()


def test_probe_keeps_fetched_lfs_inputs_available(analysis: Callable[..., Path]) -> None:
    import hashlib
    import shlex

    root = analysis(_SPEC)
    payload = "local content\n"
    pointer = ("version https://git-lfs.github.com/spec/v1\n"
               f"oid sha256:{hashlib.sha256(payload.encode()).hexdigest()}\n"
               f"size {len(payload)}\n")
    # Exercise git's clean-filter contract without requiring a Git LFS account
    # or executable: content in the working file, its pointer in the commit.
    canonical = root / ".git/lfs-test-pointer"
    canonical.write_text(pointer)
    dataset._git(["config", "filter.lfs.clean", f"cat {shlex.quote(str(canonical))}"], cwd=root)
    attributes = root / ".gitattributes"
    attributes.write_text(attributes.read_text() +
                          "\ndata/catalog.txt filter=lfs annex.largefiles=nothing\n")
    source = root / "data/catalog.txt"
    source.write_text(payload)
    dataset.save(root, [attributes, source], "a fetched LFS input")
    assert not dataset.status(root)
    tree = staging.take(root, dataset.head(root))
    try:
        assert (tree.root / "data/catalog.txt").read_text() == pointer
        paths = staging.probe_files(tree)
        linked = tree.root / "data/catalog.txt"
        assert linked.is_symlink() and linked.read_text() == payload
        assert source in paths
    finally:
        tree.close()


def test_readers_finish_before_collection_and_use_one_shared_tree(
    root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    stages: set[Path] = set()
    inodes: dict[Path, int] = {}
    original = dataset.head(root)
    save = dataset.save

    def save_without_copy(directory: Path, *args: Any, **kwargs: Any) -> bool:
        for path in kwargs.get("annexed", ()):
            assert path.stat().st_ino == inodes[path.relative_to(root)]
        return save(directory, *args, **kwargs)

    class Inspect(_Inline):
        def submit(self, fn: Any, *args: Any, key: str, resources: dict[str, float]) -> Any:
            tree, task = args[:2]
            stages.add(tree)
            assert not (root / "results/first.txt").exists()
            assert not (root / ".astra/records/first.json").exists()
            assert dataset.head(root) == original
            if task.id == "second":
                upstream = tree / "results/first.txt"
                assert upstream.is_symlink()
                assert upstream.resolve().is_relative_to(tree / ".lightcone/payloads")
            outcome = super().submit(fn, *args, key=key, resources=resources)
            inodes[task.path.relative_to(tree)] = task.path.stat().st_ino
            return outcome

        def completed(self, handles: list[object]) -> Iterator[object]:
            # Even a transport returning reports backwards must preserve commit order.
            yield from reversed(handles)

    @contextmanager
    def cluster(cluster_id: str) -> Iterator[_Inline]:
        yield Inspect()

    monkeypatch.setattr(engine, "cluster_for_run", cluster)
    monkeypatch.setattr(dataset, "save", save_without_copy)
    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)
    assert report.made == ["first", "second"], report.warnings
    assert len(stages) == 1 and not next(iter(stages)).exists()
    assert (root / "results/second.txt").read_text() == "original\n"
    assert not dataset.status(root)
    for name in report.made:
        record = json.loads((root / f".astra/records/{name}.json").read_text())
        assert record["generated_by"]["commit"] == original


def test_user_edits_cannot_reach_recipes_and_are_not_overwritten(
    root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Edit(_Inline):
        def submit(self, fn: Any, *args: Any, key: str, resources: dict[str, float]) -> Any:
            (root / "code.py").write_text("editing while the recipe runs\n")
            assert (args[0] / "code.py").read_text() == "original\n"
            return super().submit(fn, *args, key=key, resources=resources)

    @contextmanager
    def cluster(cluster_id: str) -> Iterator[_Inline]:
        yield Edit()

    monkeypatch.setattr(engine, "cluster_for_run", cluster)
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok
    assert (root / "code.py").read_text() == "editing while the recipe runs\n"
    assert dataset.status(root) == [(" M", "code.py")]


def test_competing_runs_have_separate_trees_and_changed_outputs_are_refused(root: Path) -> None:
    first = staging.take(root, dataset.head(root))
    second = staging.take(root, dataset.head(root))
    target = root / "results/first.txt"
    try:
        assert first.root != second.root
        target.write_text("another run\n")
        with pytest.raises(project.ProjectError, match="destinations changed"):
            with staging.collecting(first, [target]):
                pytest.fail("would overwrite uncommitted work")
        dataset.save(root, [target], "another run completed")
        with pytest.raises(project.ProjectError, match="destinations changed"):
            with staging.collecting(second, [target]):
                pytest.fail("would overwrite a completed run")
        assert target.read_text() == "another run\n"
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("overlap", [False, True])
def test_concurrent_collectors_preserve_disjoint_results_and_refuse_overlaps(
    analysis: Callable[..., Path], monkeypatch: pytest.MonkeyPatch, overlap: bool,
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    root = analysis(_SPEC.replace("inputs: [first]", "inputs: [catalog]").replace(
        "{inputs.first}", "{inputs.catalog}",
    ), {"data/catalog.txt": "original\n"})
    ready = Barrier(2, timeout=30)
    stages: set[Path] = set()

    class Concurrent(_Inline):
        def submit(self, fn: Any, *args: Any, key: str, resources: dict[str, float]) -> Any:
            stages.add(args[0])
            return super().submit(fn, *args, key=key, resources=resources)

        def completed(self, handles: list[object]) -> Iterator[object]:
            ready.wait()
            yield from handles

    @contextmanager
    def cluster(cluster_id: str) -> Iterator[_Inline]:
        yield Concurrent()

    monkeypatch.setattr(engine, "cluster_for_run", cluster)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(engine.materialize, root, [target], cluster_id=CLUSTER_ID)
                   for target in ("first", "first" if overlap else "second")]
        failures = [future.exception() for future in futures if future.exception() is not None]
    assert len(stages) == 2
    assert len(failures) == int(overlap)
    if overlap:
        assert isinstance(failures[0], project.ProjectError)
        assert "destinations changed" in str(failures[0])
        assert sum(path.exists() for path in stages) == 1
    else:
        assert not any(path.exists() for path in stages)
        assert (root / "results/second.txt").read_text() == "original\n"
    assert (root / "results/first.txt").read_text() == "original\n"
    assert not dataset.status(root)


def test_oci_mounts_the_exact_link_target_and_shared_environment_only(root: Path) -> None:
    tree = staging.take(root, dataset.head(root))
    try:
        staging.fill(root, tree.top, tree.root, tree.commit, ["data/catalog.txt"], fetch=True)
        path = tree.root / "data/catalog.txt"
        env = root / ".lightcone/envs/complete"
        env.mkdir(parents=True)
        runtime = container.Runtime(tree.root, "containerized", env, "podman", store_id="image")
        policy = container.policy_for(runtime, [path])
        try:
            argv = container.backend(runtime, workdir=tree.root).wrap(policy, ["cat", str(path)])
            mounts = [arg for arg in argv if arg.startswith("--volume=")]
            assert f"--volume={path.resolve()}:{path.resolve()}:ro" in mounts
            assert f"--volume={env}:{env}:ro" in mounts
            assert not any(arg.startswith(f"--volume={root}:") for arg in mounts)
            assert not any(arg.startswith(f"--volume={root / '.git'}:") for arg in mounts)
        finally:
            import shutil
            shutil.rmtree(policy.tmp_home)
    finally:
        tree.close()


def test_cli_probe_and_materialize_use_staging_on_a_real_cluster(
    root: Path, cluster_id: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from click.testing import CliRunner

    from lightcone.cli.commands import main

    monkeypatch.chdir(root)
    runner = CliRunner()
    probe = runner.invoke(main, ["run", cluster_id, "--", "bash", "-c",
                                 "test -L data/catalog.txt && cat data/catalog.txt && pwd"])
    assert probe.exit_code == 0, probe.output
    assert "original" in probe.output and staging.RUNS in probe.output
    assert not dataset.status(root)
    result = runner.invoke(main, ["materialize", cluster_id, "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["made"] == ["first", "second"]
    assert not dataset.status(root)
    assert not [path for path in (root / staging.RUNS).iterdir() if path.is_dir()]


def test_probe_sees_edits_and_collects_regular_outputs_even_on_failure(
    root: Path, cluster_id: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from click.testing import CliRunner

    from lightcone.cli.commands import main

    (root / "code.py").write_text("under development\n")
    monkeypatch.chdir(root)
    result = CliRunner().invoke(main, [
        "run", cluster_id, "--", "bash", "-c",
        "cat code.py > results/probe.txt; exit 7",
    ])
    assert result.exit_code == 7, result.output
    assert (root / "results/probe.txt").read_text() == "under development\n"
    assert (root / "code.py").read_text() == "under development\n"


def test_a_resubmitted_task_reuses_its_completed_attempt(
    root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = []

    class Repeat(_Inline):
        def submit(self, fn: Any, *args: Any, key: str, resources: dict[str, float]) -> Any:
            first = super().submit(fn, *args, key=key, resources=resources)
            second = super().submit(fn, *args, key=key, resources=resources)
            assert second == first
            if first.status == "made":
                assert (args[1].path).is_symlink()
            seen.append(key)
            return second

    @contextmanager
    def cluster(cluster_id: str) -> Iterator[_Inline]:
        yield Repeat()

    monkeypatch.setattr(engine, "cluster_for_run", cluster)
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok
    assert seen == ["first", "second"]


def test_a_second_attempt_never_races_an_attempt_without_a_report(root: Path) -> None:
    import hashlib

    from lightcone.engine import worker

    graph = plan.build(root)
    tree = staging.take(root, dataset.head(root))
    try:
        task = graph.tasks["first"].at(tree.root)
        claim = tree.root / ".lightcone/tasks" / hashlib.sha256(task.id.encode()).hexdigest()
        claim.mkdir(parents=True)
        runtime = container.runtime_for_run(root, build=False).at(tree.root)
        result = worker.materialize(
            tree.root, task, graph.checked.statuses[task.id], None,
            worker.RunContext("unused", tree.commit, runtime), False,
        )
        assert result.status == "failed" and "earlier attempt" in result.reason
        assert not task.path.exists()
    finally:
        tree.close()


def test_a_rejected_resubmission_cannot_clean_up_a_possibly_live_worker(
    root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hashlib

    class Resubmit(_Inline):
        def submit(self, fn: Any, *args: Any, key: str, resources: dict[str, float]) -> Any:
            tree, task = args[:2]
            if key == "first":
                claim = tree / ".lightcone/tasks" / hashlib.sha256(task.id.encode()).hexdigest()
                claim.mkdir(parents=True)
            return super().submit(fn, *args, key=key, resources=resources)

    @contextmanager
    def cluster(cluster_id: str) -> Iterator[_Inline]:
        yield Resubmit()

    monkeypatch.setattr(engine, "cluster_for_run", cluster)
    with pytest.raises(project.ProjectError, match="may still be running.*Run files retained"):
        engine.materialize(root, [], cluster_id=CLUSTER_ID)
    (retained,) = [path for path in (root / staging.RUNS).iterdir() if path.is_dir()]
    assert (retained / ".lightcone/tasks").is_dir()
    assert not dataset.status(root)


def test_a_failed_landing_keeps_user_edits_and_the_uncollected_payload(
    root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Edit(_Inline):
        def completed(self, handles: list[object]) -> Iterator[object]:
            (root / "results/first.txt").write_text("user result\n")
            yield from handles

    @contextmanager
    def cluster(cluster_id: str) -> Iterator[_Inline]:
        yield Edit()

    monkeypatch.setattr(engine, "cluster_for_run", cluster)
    with pytest.raises(project.ProjectError, match="Run files retained"):
        engine.materialize(root, [], cluster_id=CLUSTER_ID)
    assert (root / "results/first.txt").read_text() == "user result\n"
    assert not (root / ".astra/records/first.json").exists()
    (retained,) = [path for path in (root / staging.RUNS).iterdir() if path.is_dir()]
    assert (retained / "results/first.txt").read_text() == "original\n"


def test_a_failed_save_returns_the_completed_bytes_to_the_run(
    root: Path, inline: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: Any, **kwargs: Any) -> Any:
        raise project.ProjectError("save refused")

    monkeypatch.setattr(dataset, "save", fail)
    with pytest.raises(project.ProjectError, match="save refused"):
        engine.materialize(root, [], cluster_id=CLUSTER_ID)
    assert not dataset.status(root)
    (retained,) = [path for path in (root / staging.RUNS).iterdir() if path.is_dir()]
    assert (retained / "results/first.txt").read_text() == "original\n"
    assert (retained / ".astra/records/first.json").is_file()
