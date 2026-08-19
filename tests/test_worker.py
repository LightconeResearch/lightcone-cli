"""Tests for `lightcone.engine.worker` — making one output.

This file runs real recipes through the real boundary, against a real
project, because that is the only way to answer what it asks: whether the
environment gates hold, whether the output directory is really reset,
whether the manifest agrees with the bytes beside it, and whether a
recipe can reach something it was not given.

Following `test_sandbox_enforcement.py`'s rule, every denial assertion is
mutation-checked — the same command is run through `Unavailable()` and
must *succeed*, or the test would pass without the sandbox doing anything.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from lightcone.engine import assets, identity, plan, worker
from lightcone.engine.sandbox import Unavailable
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
    recipe:
      command: echo one > {output}/value.txt

  - id: second
    type: report
    inputs: [first]
    recipe:
      command: cat {inputs.first}/value.txt > {output}/copy.txt
"""


@pytest.fixture
def root(analysis: Callable[..., Path]) -> Path:
    return analysis(_SPEC)


def _task(root: Path, output_id: str) -> plan.Task:
    graph = plan.build(root)
    return graph.tasks[("baseline", output_id)]


#: What the driver reads once and hands to every task.
_HEAD = ("0123456789abcdef", "https://example/analysis.git")


def _make(
    root: Path, output_id: str, *upstream: TaskResult, refresh: bool = False
) -> TaskResult:
    """Run one task the way Dask would, handed its upstream results."""
    task = _task(root, output_id)
    return worker.materialize(
        root, task, identity.env_version(root), _HEAD, assets.Versions(), refresh, *upstream
    )


def _age(root: Path, output_id: str) -> None:
    """Rewrite an output's manifest to name an environment that is not this
    one — the shape a project takes when `uv add` rewrites the lock after a
    result was made.

    The *recorded* value is what moves, never the run's: the environment
    the recipe actually runs under has to stay the real one, or the mid-run
    gate refuses the execution before any of this is exercised.
    """
    directory = root / "results/baseline" / output_id
    manifest = assets.read(directory)
    assert manifest is not None
    assets.write(directory, replace(manifest, env_version="sha256:an-earlier-environment"))


# ---- executing a recipe ----------------------------------------------------


def test_a_recipe_runs_and_its_output_is_recorded(root: Path) -> None:
    result = _make(root, "first")

    assert result.status == "ok"
    assert (root / "results/baseline/first/value.txt").read_text() == "one\n"
    assert result.data_version == assets.data_version(root / "results/baseline/first")


def test_the_manifest_is_complete_before_anything_is_saved(root: Path) -> None:
    """The driver commits the directory and its manifest as one commit, so
    a manifest that described the bytes only after the save could never be
    in that commit."""
    _make(root, "first")

    manifest = assets.read(root / "results/baseline/first")
    assert manifest is not None
    assert manifest.data_version == assets.data_version(root / "results/baseline/first")
    assert manifest.definition_version == _task(root, "first").definition_version
    assert manifest.env_version == identity.env_version(root)
    assert manifest.git_sha == _HEAD[0] and manifest.git_remote == _HEAD[1]
    assert manifest.hermeticity["mechanism"]


def test_the_recipe_runs_under_the_boundary(root: Path) -> None:
    """Whatever the host can enforce, the manifest records what it was —
    never what the mechanism matrix says it should have been."""
    from lightcone.engine import sandbox

    _make(root, "first")

    manifest = assets.read(root / "results/baseline/first")
    assert manifest is not None
    assert manifest.hermeticity["mechanism"] == sandbox.detect().capability.kind


# ---- deciding whether to run at all ----------------------------------------


def test_an_unchanged_output_is_current(root: Path) -> None:
    made = _make(root, "first")
    again = _make(root, "first")

    assert again.status == "current"
    assert again.data_version == made.data_version


def test_a_skip_returns_the_recorded_digest_rather_than_rehashing(root: Path) -> None:
    """On a clone that has fetched no annex content the files are dangling
    symlinks, so a recompute would quietly report a different output."""
    _make(root, "first")
    output = root / "results/baseline/first"
    manifest = assets.read(output)
    assert manifest is not None
    (output / "value.txt").unlink()

    assert _make(root, "first").data_version == manifest.data_version


def test_a_moved_environment_leaves_the_output_alone(root: Path) -> None:
    """The change this layer exists for. The recipe and the decisions still
    define exactly this output, so it is reported and kept — remaking it
    would spend the compute that a rewritten `uv.lock` never justified."""
    made = _make(root, "first")
    (root / "results/baseline/first/value.txt").write_text("untouched\n")
    _age(root, "first")

    again = _make(root, "first")

    assert again.status == "behind"
    assert "earlier environment" in again.reason
    assert again.data_version == made.data_version
    assert (root / "results/baseline/first/value.txt").read_text() == "untouched\n"


def test_refresh_remakes_what_is_only_behind(root: Path) -> None:
    """And the recipe really runs: the file the previous assertion left in
    place is overwritten, so this cannot pass by skipping too."""
    _make(root, "first")
    (root / "results/baseline/first/value.txt").write_text("untouched\n")
    _age(root, "first")

    again = _make(root, "first", refresh=True)

    assert again.status == "ok"
    assert (root / "results/baseline/first/value.txt").read_text() == "one\n"


def test_refresh_does_not_remake_what_is_current(root: Path) -> None:
    """`--refresh` widens the run by one state, not to everything. Without
    this it would be a rebuild-the-world flag wearing another name."""
    _make(root, "first")

    assert _make(root, "first", refresh=True).status == "current"


def test_a_behind_upstream_still_feeds_its_dependents(root: Path) -> None:
    """`behind` says the environment moved, not that the bytes are wrong —
    so a dependent proceeds on them rather than reporting blocked."""
    first = _make(root, "first")
    _age(root, "first")
    behind = _make(root, "first")
    assert behind.status == "behind"

    second = _make(root, "second", behind)

    assert second.status == "ok"
    manifest = assets.read(root / "results/baseline/second")
    assert manifest is not None
    assert manifest.input_versions == {"first": first.data_version}


def test_a_task_whose_upstream_did_not_finish_is_blocked(root: Path) -> None:
    """Blocked without running: an exception would make Dask propagate to
    every dependent and stop 'who actually failed' being answerable."""
    upstream = TaskResult(("baseline", "first"), "failed", reason="boom")

    result = _make(root, "second", upstream)

    assert result.status == "blocked"
    assert "baseline/first" in result.reason
    assert not (root / "results/baseline/second").exists()


def test_a_downstream_task_takes_its_upstreams_answer(root: Path) -> None:
    first = _make(root, "first")
    second = _make(root, "second", first)

    assert second.status == "ok"
    manifest = assets.read(root / "results/baseline/second")
    assert manifest is not None
    assert manifest.input_versions == {"first": first.data_version}


# ---- the output directory the recipe owns ----------------------------------


def test_a_stale_file_does_not_survive_a_rebuild(root: Path) -> None:
    """It would otherwise land in the content hash and be committed as part
    of an output that never produced it."""
    _make(root, "first")
    output = root / "results/baseline/first"
    (output / "leftover.txt").write_text("from a previous run\n")

    worker.execute(root, _task(root, "first"), identity.env_version(root), {}, head=_HEAD)

    assert not (output / "leftover.txt").exists()
    assert (output / "value.txt").exists()


def test_a_failing_recipe_records_no_manifest(root: Path) -> None:
    spec = _SPEC.replace("echo one > {output}/value.txt", "echo one > {output}/value.txt && false")
    (root / "astra.yaml").write_text(spec)

    result = _make(root, "first")

    assert result.status == "failed"
    assert "exited 1" in result.reason
    assert assets.read(root / "results/baseline/first") is None


def test_a_recipe_that_removes_its_output_directory_fails_on_any_host(root: Path) -> None:
    """The two mechanisms disagree about whether the removal is even
    allowed — Landlock follows POSIX and refuses it, because unlinking the
    directory needs write on `results/`, which is not granted; Seatbelt's
    subpath grant covers the directory node itself and permits it. The
    *contract* is the same either way, so that is what this asserts: a
    `failed` result, never a raise into the driver."""
    (root / "astra.yaml").write_text(
        _SPEC.replace("echo one > {output}/value.txt", "rm -rf {output}")
    )

    assert _make(root, "first").status == "failed"


def test_an_output_that_cannot_be_recorded_fails_rather_than_raises(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recording is fallible too, and a raise here reaches Dask, which
    re-raises in the driver and takes down every other task in flight —
    where reporting one failure and letting the rest finish is the whole
    point of owning the loop. Exercised without a mechanism, because that
    is the host where a recipe really can delete what it was given."""
    from lightcone.engine import sandbox

    monkeypatch.setattr(sandbox, "detect", Unavailable)
    (root / "astra.yaml").write_text(
        _SPEC.replace("echo one > {output}/value.txt", "rm -rf {output}")
    )

    result = _make(root, "first")

    assert result.status == "failed"
    assert "could not be recorded" in result.reason


# ---- the environment gates -------------------------------------------------


def test_an_environment_that_moved_under_the_run_is_refused(root: Path) -> None:
    """A manifest may not claim an environment that had already been edited
    by the time the recipe ran."""
    result = worker.execute(
        root, _task(root, "first"), "sha256:from-another-run", {}, head=_HEAD
    )

    assert result.status == "failed"
    assert "environment changed" in result.reason
    assert assets.read(root / "results/baseline/first") is None


# ---- what a recipe may touch -----------------------------------------------


def test_a_recipe_cannot_write_outside_the_results_tree(root: Path) -> None:
    """One policy for probes and recipes, so `results/` is the whole of a
    recipe's in-tree write scope. Sibling outputs are *not* carved out —
    the manifest's content hash is what says whether an output's bytes are
    its own, and a second mechanism for one guarantee is one more than can
    be kept honest."""
    from lightcone.engine import sandbox

    if sandbox.detect().capability.kind == "none":
        pytest.skip("no sandbox mechanism on this host")
    (root / "astra.yaml").write_text(
        _SPEC.replace("echo one > {output}/value.txt", "echo tampered > src/injected.py")
    )

    assert _make(root, "first").status == "failed"
    assert not (root / "src" / "injected.py").exists()


def test_that_write_would_have_succeeded_unsandboxed(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mutation check. Without it the test above would pass on a host
    that enforces nothing, and pin nothing at all."""
    from lightcone.engine import sandbox

    monkeypatch.setattr(sandbox, "detect", Unavailable)
    (root / "src").mkdir(exist_ok=True)
    (root / "astra.yaml").write_text(
        _SPEC.replace("echo one > {output}/value.txt", "echo tampered > src/injected.py")
    )

    assert _make(root, "first").status == "ok"
    assert (root / "src" / "injected.py").read_text() == "tampered\n"


def test_a_recipe_can_read_an_annexed_input(analysis: Callable[..., Path]) -> None:
    """Declared inputs live in the annex. With `filter=annex` the working
    tree holds the real bytes, so the project read root covers them — but
    that is worth pinning, since it is what every recipe with an input
    depends on."""
    from lightcone.engine import dataset

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

    result = _make(root, "fit")

    assert result.status == "ok", result.reason
    assert (root / "results/baseline/fit/seen.txt").read_text() == "measured\n"


# ---- the entry point the run record names ----------------------------------


def test_the_worker_module_imports_neither_click_nor_rich() -> None:
    """It is on the `python -m` path of every rerun and every task, so a
    CLI import here would be paid on all of them."""
    proc = subprocess.run(
        [sys.executable, "-c", "import lightcone.engine.worker, sys; print(sorted(sys.modules))"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "'click'" not in proc.stdout
    assert "'rich'" not in proc.stdout


def test_the_module_runs_one_task_and_commits_nothing(root: Path) -> None:
    """What `datalad rerun` invokes. It leaves the tree dirty by design —
    which is exactly why it is not an `lc` verb."""
    from lightcone.engine import dataset

    proc = subprocess.run(
        [sys.executable, "-m", "lightcone.engine.worker", "baseline/first"],
        cwd=root,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr
    assert (root / "results/baseline/first/value.txt").read_text() == "one\n"
    assert dataset.status(root)


def test_the_module_reruns_unconditionally(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rerun is a rerun: the caller has already said what they want, and
    a staleness check would answer a question nobody asked."""
    monkeypatch.chdir(root)
    _make(root, "first")
    (root / "results/baseline/first/value.txt").unlink()

    assert worker.main(["baseline/first"]) == 0
    assert (root / "results/baseline/first/value.txt").exists()


def test_the_module_refuses_an_argument_it_cannot_use(root: Path) -> None:
    assert worker.main([]) == 2
    assert worker.main(["first"]) == 2


def test_the_module_refuses_an_output_that_does_not_exist(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(root)
    assert worker.main(["baseline/nothing"]) == 2
