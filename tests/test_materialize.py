"""Tests for `lightcone.engine.materialize` — running a whole analysis.

Everything here runs against a real project with a real git repository and
a real annex, because what is being pinned is what ends up committed: one
commit per output, a run record datalad can read, and a tree exactly as
clean afterwards as it was before.

Most tests replace the Dask cluster with an inline scheduler — the one new
monkeypatch point — so they cost nothing to start. One does not, because
the seam is only worth having if the real thing still fits through it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from lightcone.engine import assets, dataset
from lightcone.engine import materialize as engine
from lightcone.engine.project import ProjectError, child_env
from lightcone.engine.worker import TaskResult

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


class _Inline:
    """Run the graph in this thread, in the order it was submitted.

    Submission is topological, so a dependent is submitted only after its
    upstreams have already run — which means the "handles" it is passed
    are the upstream results themselves, exactly what the worker expects.
    """

    def submit(self, fn: Any, *args: Any, key: str) -> Any:
        return fn(*args)

    def completed(self, handles: list[Any]) -> Iterator[TaskResult]:
        yield from handles


@pytest.fixture
def inline(monkeypatch: pytest.MonkeyPatch) -> None:
    @contextmanager
    def fake() -> Iterator[_Inline]:
        yield _Inline()

    monkeypatch.setattr(engine, "cluster_for_run", fake)


def _commits(root: Path) -> int:
    return len(dataset._git(["log", "--oneline"], cwd=root).splitlines())


# ---- a run, end to end -----------------------------------------------------


def test_every_output_is_made_and_committed(root: Path, inline: None) -> None:
    before = _commits(root)

    report = engine.materialize(root, [])

    assert report.made == ["baseline/first", "baseline/second"]
    assert report.ok and not report.up_to_date
    assert (root / "results/baseline/second/copy.txt").read_text() == "alpha\n"
    assert _commits(root) == before + 2
    assert not dataset.status(root)


def test_an_output_and_its_manifest_land_in_one_commit(root: Path, inline: None) -> None:
    """One commit is one complete, self-describing materialization — the
    manifest can never come to describe different bytes."""
    engine.materialize(root, ["first"])

    committed = dataset._git(
        ["show", "--name-only", "--format=", "HEAD"], cwd=root
    ).split()
    assert sorted(committed) == [
        "results/baseline/first/.lightcone-manifest.json",
        "results/baseline/first/value.txt",
    ]


def test_the_bytes_go_to_the_annex_and_the_manifest_to_git(root: Path, inline: None) -> None:
    """What git records is the test: a pointer for content, the real thing
    for a manifest. The working tree looks the same either way now."""
    engine.materialize(root, ["first"])

    def blob(rel: str) -> str:
        return dataset._git(["cat-file", "-p", f"HEAD:{rel}"], cwd=root)

    assert blob("results/baseline/first/value.txt").startswith("/annex/objects/")
    assert blob("results/baseline/first/.lightcone-manifest.json").startswith("{")


def test_a_second_run_does_nothing_and_commits_nothing(root: Path, inline: None) -> None:
    engine.materialize(root, [])
    after_first = _commits(root)

    report = engine.materialize(root, [])

    assert report.made == []
    assert report.skipped == ["baseline/first", "baseline/second"]
    assert report.up_to_date
    assert _commits(root) == after_first


def test_asking_for_an_output_makes_what_it_is_made_of(root: Path, inline: None) -> None:
    report = engine.materialize(root, ["second"])

    assert report.made == ["baseline/first", "baseline/second"]


def test_a_changed_decision_remakes_the_output_and_its_dependents(
    root: Path, inline: None
) -> None:
    engine.materialize(root, [])
    (root / "universes" / "baseline.yaml").write_text("id: baseline\ndecisions:\n  method: beta\n")
    dataset.save(root, [root], "switch method")

    report = engine.materialize(root, [])

    assert report.made == ["baseline/first", "baseline/second"]
    assert (root / "results/baseline/second/copy.txt").read_text() == "beta\n"


def test_the_previous_bytes_are_still_there_at_the_previous_commit(
    root: Path, inline: None
) -> None:
    """The property the whole layer exists for: given a commit, recover the
    exact bytes it produced."""
    engine.materialize(root, ["first"])
    original = dataset._git(["rev-parse", "HEAD"], cwd=root).strip()
    (root / "universes" / "baseline.yaml").write_text("id: baseline\ndecisions:\n  method: beta\n")
    dataset.save(root, [root], "switch method")
    engine.materialize(root, ["first"])
    assert (root / "results/baseline/first/value.txt").read_text() == "beta\n"

    dataset._git(["checkout", original, "--", "results/baseline/first"], cwd=root)

    assert (root / "results/baseline/first/value.txt").read_text() == "alpha\n"


# ---- check mode ------------------------------------------------------------


def test_check_says_what_would_run_and_why(root: Path) -> None:
    report = engine.check(root, [])

    assert set(report.planned) == {"baseline/first", "baseline/second"}
    assert "never been materialized" in report.planned["baseline/first"]
    assert not report.up_to_date


def test_check_writes_nothing_and_commits_nothing(root: Path) -> None:
    before = _commits(root)

    engine.check(root, [])

    assert not (root / "results/baseline/first").exists()
    assert _commits(root) == before


def test_check_cascades_through_an_output_it_already_decided_to_rebuild(
    root: Path, inline: None
) -> None:
    """The `None` sentinel. Check mode cannot know whether a rebuild comes
    out byte-identical, so it assumes it will not — the one place it is
    deliberately more pessimistic than a worker."""
    engine.materialize(root, [])
    (root / "universes" / "baseline.yaml").write_text("id: baseline\ndecisions:\n  method: beta\n")
    dataset.save(root, [root], "switch method")

    report = engine.check(root, [])

    assert "the recipe, its decisions, or the environment" in report.planned["baseline/first"]
    assert report.planned["baseline/second"] == "the input `first` changed"


def test_check_does_not_refuse_a_dirty_tree(root: Path) -> None:
    """Reading the state of a project before deciding what to commit is
    exactly what check mode is for."""
    (root / "notes.md").write_text("in progress\n")

    assert engine.check(root, []).planned


# ---- the refusals ----------------------------------------------------------


def test_a_dirty_tree_refuses_and_says_what_to_do_about_each_path(
    root: Path, inline: None
) -> None:
    """Two path classes, two opposite remedies: work the researcher owns is
    committed, and anything under `results/` is lc's to write."""
    engine.materialize(root, ["first"])
    (root / "notes.md").write_text("in progress\n")
    (root / "results/baseline/first/stray.txt").write_text("by hand\n")

    with pytest.raises(ProjectError) as raised:
        engine.materialize(root, [])

    message = str(raised.value)
    assert "commit these" in message and "notes.md" in message
    assert "discard these" in message and "results/baseline/first/stray.txt" in message


def test_a_dependency_the_lock_does_not_pin_is_refused(root: Path) -> None:
    """Its bytes are not recorded anywhere, so every hash below it would be
    a claim nobody can check."""
    (root / "uv.lock").write_text(
        (root / "uv.lock").read_text()
        + '\n[[package]]\nname = "sibling"\nsource = { path = "../sibling" }\n'
    )

    with pytest.raises(ProjectError, match="cannot be audited"):
        engine.check(root, [])


def test_a_lock_that_builds_from_source_is_a_warning_not_a_refusal(root: Path) -> None:
    """Building from source is legitimate; identity just covers the sdist
    rather than the build of it, and saying so is the whole obligation."""
    (root / "uv.lock").write_text(
        (root / "uv.lock").read_text()
        + '\n[[package]]\nname = "oldlib"\n'
        'source = { registry = "https://pypi.org/simple" }\n'
        '\n[package.sdist]\nurl = "https://example/oldlib.tar.gz"\n'
    )

    report = engine.check(root, [])

    assert report.ok
    assert any("oldlib" in w for w in report.warnings)


# ---- leaving the tree as clean as it was found -----------------------------


def test_a_failing_recipe_commits_nothing_and_leaves_the_tree_clean(
    analysis: Callable[..., Path], inline: None
) -> None:
    """The invariant that makes the dirty-tree refusal survivable: the next
    run must not tell the user to commit truncated, manifest-less
    garbage."""
    spec = _SPEC.replace("echo {decisions.method} > {output}/value.txt", "exit 1")
    root = analysis(spec, universes={"baseline": _UNIVERSE})
    before = _commits(root)

    report = engine.materialize(root, [])

    assert report.failed == ["baseline/first"]
    assert report.blocked == ["baseline/second"]
    assert not report.ok
    assert _commits(root) == before
    assert not dataset.status(root)


def test_a_rebuild_that_fails_puts_the_previous_output_back(
    root: Path, inline: None
) -> None:
    engine.materialize(root, ["first"])
    (root / "astra.yaml").write_text(
        _SPEC.replace("echo {decisions.method} > {output}/value.txt", "exit 1")
    )
    dataset.save(root, [root], "break the recipe")
    at_break = _commits(root)

    report = engine.materialize(root, ["first"])

    assert report.failed == ["baseline/first"]
    assert (root / "results/baseline/first/value.txt").read_text() == "alpha\n"
    assert _commits(root) == at_break
    assert not dataset.status(root)


def test_an_interrupted_run_restores_what_never_reported(
    root: Path, inline: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sibling that already saved keeps its commit; the output still in
    flight is put back, so the tree ends clean either way."""
    engine.materialize(root, [])
    (root / "astra.yaml").write_text(_SPEC.replace("echo {decisions.method}", "echo changed"))
    dataset.save(root, [root], "edit both recipes")

    class _Interrupted(_Inline):
        def completed(self, handles: list[Any]) -> Iterator[TaskResult]:
            yield handles[0]
            raise KeyboardInterrupt

    @contextmanager
    def fake() -> Iterator[_Interrupted]:
        yield _Interrupted()

    monkeypatch.setattr(engine, "cluster_for_run", fake)

    with pytest.raises(KeyboardInterrupt):
        engine.materialize(root, [])

    assert not dataset.status(root)


# ---- the commit message ----------------------------------------------------


def test_the_run_record_is_what_datalad_reads(root: Path, inline: None) -> None:
    """Asserted through datalad's own parser rather than against our JSON:
    it matches with a regex and returns nothing on any mismatch, after
    which `rerun` reports "no command; skipping" and exits 0 — so a golden
    test on the text would stay green through a silent break."""
    from datalad.api import Dataset
    from datalad.local.rerun import get_run_info

    engine.materialize(root, ["second"])
    message = dataset._git(["log", "-1", "--format=%B"], cwd=root)

    subject, info = get_run_info(Dataset(str(root)), message)

    assert subject == "second [baseline]"
    assert info is not None
    assert info["cmd"].endswith("python -m lightcone.engine.worker baseline/second")
    assert info["inputs"] == ["results/baseline/first"]
    assert info["outputs"] == ["results/baseline/second"]
    assert info["dsid"] == "4b7b5c1e-0000-4000-8000-000000000000"
    assert info["chain"] == [] and info["pwd"] == "."


def test_the_record_names_the_declared_input_not_the_annex_object(
    analysis: Callable[..., Path], inline: None
) -> None:
    """Declared inputs are annex symlinks, so a resolved path records
    `.git/annex/objects/SHA256E-…` — the storage rather than the input, and
    something no one can `datalad get`."""
    spec = """
    version: "0.0.13"
    name: analysis
    inputs:
      - id: catalog
        type: data
        source: data/catalog.txt
    outputs:
      - id: fit
        type: metric
        inputs: [catalog]
        recipe:
          command: cat {inputs.catalog} > {output}/seen.txt
    """
    root = analysis(spec, files={"data/catalog.txt": "measured\n"})
    dataset.save(root, [root / "data"], "the catalog")

    engine.materialize(root, [])

    from datalad.api import Dataset
    from datalad.local.rerun import get_run_info

    _, info = get_run_info(Dataset(str(root)), dataset._git(["log", "-1", "--format=%B"], cwd=root))
    assert info is not None
    assert info["inputs"] == ["data/catalog.txt"]


def test_every_manifest_of_one_run_names_the_same_commit(root: Path, inline: None) -> None:
    """The driver commits each output as it lands, so HEAD moves during the
    run — and reading it per task would stamp later manifests with a commit
    this same run created, nondeterministically."""
    engine.materialize(root, [])

    shas = {
        assets.read(root / "results/baseline" / name).git_sha  # type: ignore[union-attr]
        for name in ("first", "second")
    }
    assert len(shas) == 1


def test_check_agrees_with_a_run_on_a_clone_with_no_annex_content(
    root: Path, inline: None, tmp_path: Path
) -> None:
    """Manifests are in git, so an output whose bytes were never fetched
    is still classifiable — check mode reads the recorded digest rather
    than the pointer file sitting in its place."""
    engine.materialize(root, [])
    clone = tmp_path / "clone"
    dataset._git(["clone", "-q", str(root), str(clone)], cwd=tmp_path)
    # `clone` does not copy identity, and `annex init` makes a commit — so
    # this fails on any host without a usable global one, CI included.
    for key, value in (("user.email", "t@example.com"), ("user.name", "Test")):
        dataset._git(["config", key, value], cwd=clone)
    dataset._git(["annex", "init", "-q", "clone"], cwd=clone)
    pointer = (clone / "results/baseline/first/value.txt").read_text()
    assert pointer.startswith("/annex/objects/")  # content really is absent

    assert engine.check(clone, []).planned == {}


def test_a_drifted_environment_is_made_to_match_before_anything_runs(
    root: Path, inline: None
) -> None:
    """Workers pass `--no-sync`, so this is the only place the environment
    is made to match the lock. Reported and refused, a lock edited without
    a sync would leave recipes importing packages the lock does not
    describe while every manifest recorded the new lock's `env_version`;
    doing it instead is shorter and impossible to ignore."""
    pyproject = root / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text().replace("dependencies = []", 'dependencies = ["idna"]')
    )
    from lightcone.engine import project as project_mod

    project_mod._run(["uv", "lock", "-q", "--project", str(root)], cwd=root)
    dataset.save(root, [root], "add a dependency without syncing")
    assert not project_mod._env_is_current(root)

    report = engine.materialize(root, ["first"])

    assert report.ok
    assert project_mod._env_is_current(root)


def test_the_recorded_command_reproduces_the_output(root: Path, inline: None) -> None:
    """The claim the record makes, run literally. `datalad rerun` removes
    the output, executes the recorded command, and commits what came
    back — so the manifest is regenerated inside the same commit."""
    pytest.importorskip("datalad")
    engine.materialize(root, ["first"])
    original = assets.read(root / "results/baseline/first")
    assert original is not None

    proc = subprocess.run(
        [sys.executable, "-c", "from datalad.api import rerun; rerun('HEAD')"],
        cwd=root,
        capture_output=True,
        text=True,
        env={**child_env(), "PYTHONPATH": _engine_path()},
    )

    assert proc.returncode == 0, proc.stderr
    rerun = assets.read(root / "results/baseline/first")
    assert rerun is not None
    assert rerun.data_version == original.data_version
    assert rerun.data_version == assets.data_version(root / "results/baseline/first")
    assert not dataset.status(root)


def _engine_path() -> str:
    """Stand in for the ``lightcone-cli`` pin a real project's lock carries.

    The recorded command resolves the worker out of the project's own
    environment, which is the whole reason the record reproduces the
    *recorded* engine rather than today's. The fixture's project declares
    no dependencies so it stays cheap to build, so the engine under test is
    put on the path explicitly instead.
    """
    import sysconfig

    return os.pathsep.join(
        [str(Path(__file__).parent.parent / "src"), sysconfig.get_paths()["purelib"]]
    )


# ---- the scheduler seam ----------------------------------------------------


def test_a_real_cluster_still_fits_through_the_seam(root: Path) -> None:
    """The one test that starts Dask. The seam is only worth having if the
    thing it abstracts still goes through it."""
    report = engine.materialize(root, [])

    assert report.made == ["baseline/first", "baseline/second"]
    assert not dataset.status(root)


# ---- the report ------------------------------------------------------------


def test_the_report_is_json_ready(root: Path, inline: None) -> None:
    report = engine.materialize(root, ["first"])
    data = json.loads(json.dumps(report.as_dict()))

    assert data["ok"] is True
    assert data["made"] == ["baseline/first"]
