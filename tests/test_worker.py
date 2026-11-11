"""Tests for `lightcone.engine.worker` — making one output.

This file runs real recipes through the real boundary, against a real
project, because that is the only way to answer what it asks: whether the
environment gates hold, whether the output directory is really reset,
whether the record agrees with the bytes it describes, and whether a
recipe can reach something it was not given.

Following `test_sandbox_enforcement.py`'s rule, every denial assertion is
mutation-checked — the same command is run through `Unavailable()` and
must *succeed*, or the test would pass without the sandbox doing anything.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from astra.hashing import digest_bytes, digest_file

from lightcone.engine import assets, container, dataset, identity, plan, sandbox, worker
from lightcone.engine.sandbox import Unavailable
from lightcone.engine.worker import TaskResult

_SPEC = """
spec_version: "0.1.0"

decisions:
  word:
    options: {one: {}, two: {}}

universes:
  baseline:
    decisions: {word: one}

outputs:
  first:
    type: metric
    format: txt
    decisions: [word]
    recipe:
      command: echo {decisions.word} > {output}

  second:
    type: report
    format: txt
    inputs: [first]
    recipe:
      command: cat {inputs.first} > {output}
"""

#: Results built once, the first of them from a tracked input.
_READS = """
spec_version: "0.1.0"

inputs:
  catalog: {path: data/catalog.txt}

outputs:
  fit:
    type: metric
    format: txt
    inputs: [catalog]
    recipe:
      command: cat {inputs.catalog} > {output}

  summary:
    type: report
    format: txt
    inputs: [fit]
    recipe:
      command: wc -c < {inputs.fit} > {output}
"""

_FIRST = "results/baseline/first.txt"
_SECOND = "results/baseline/second.txt"
_FIRST_RECORD = ".astra/records/baseline/first.json"
_SECOND_RECORD = ".astra/records/baseline/second.json"
_FIRST_MANIFEST = "results/baseline/.first.manifest.json"


@pytest.fixture
def root(analysis: Callable[..., Path]) -> Path:
    return analysis(_SPEC)


def _task(root: Path, key: str) -> plan.Task:
    return plan.build(root).tasks[key]


#: The `origin` a driver would have read; the fixture's repository has none.
_REMOTE = "https://example/analysis.git"


def _context(root: Path, environment: str | None = None) -> worker.RunContext:
    """The driver-resolved facts a real run would hand down.

    The commit is the repository's own HEAD: a record names the commit its
    result is compared against, so an invented one would leave every
    result stale.
    """
    return worker.RunContext(
        environment=environment if environment is not None else identity.environment(root),
        head=(dataset.head(root)[0], _REMOTE),
        runtime=container.runtime_for_run(root, build=False),
        uv_version="0.0.0-test",
    )


def _make(root: Path, key: str, *upstream: TaskResult, refresh: bool = False) -> TaskResult:
    """Run one task the way Dask would, classified beforehand as the driver
    does and handed its upstream results."""
    task = _task(root, key)
    context = _context(root)
    decided = worker.decide(assets.check(root)[task.places[0].record], context.environment, None)
    return worker.materialize(root, task, context, refresh, decided, None, *upstream)


def _record(root: Path, path: str) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((root / path).read_text())
    return document


def _age(root: Path, record: str) -> None:
    """Rewrite a result's record to name an environment that is not this
    one — the shape a project takes when `uv add` rewrites the lock after a
    result was made.

    The *recorded* value is what moves, never the run's: the environment
    the recipe actually runs under has to stay the real one, or the mid-run
    gate refuses the execution before any of this is exercised.
    """
    document = json.loads((root / record).read_text())
    document["generated_by"]["environment"]["iri"] = "urn:lightcone:env:sha256:an-earlier-one"
    (root / record).write_text(json.dumps(document))


# ---- executing a recipe ----------------------------------------------------


def test_a_recipe_runs_and_its_output_is_recorded(root: Path) -> None:
    result = _make(root, "first#baseline")

    assert result.status == "ok"
    assert (root / _FIRST).read_text() == "one\n"
    assert (result.content_hash, result.size) == (digest_bytes(b"one\n").content_hash, 4)


def test_the_record_says_what_the_run_was(root: Path) -> None:
    """The record is what the result is checked against from then on, so
    it has to name this result, these bytes, the commit the run started
    from and the environment it ran under — and be one ASTRA accepts."""
    result = _make(root, "first#baseline")

    record = _record(root, _FIRST_RECORD)
    run = record["generated_by"]
    assert record["address"] == "first#baseline"
    assert (record["content_hash"], record["size"]) == (result.content_hash, result.size)
    assert run["commit"] == dataset.head(root)[0]
    assert run["environment"] == {"iri": identity.environment(root)}
    # The engine's version is attestation, not identity: with lc outside
    # the project's lock, this field is the record of which engine ran.
    assert run["runner"] == {"id": "lightcone-cli", "version": worker.lc_version()}
    assert run["started"] <= run["finished"]
    assert "inputs" not in run
    assert assets.check(root)[_FIRST_RECORD][0].record_status == "unchanged"


def test_the_manifest_holds_what_the_record_cannot(root: Path) -> None:
    """Written before anything is saved: the driver commits the bytes, the
    record and the manifest as one commit."""
    _make(root, "first#baseline")

    manifest = assets.read(root / _FIRST_MANIFEST)
    assert manifest is not None
    assert manifest.recipe == _task(root, "first#baseline").command
    assert manifest.decisions == {"word": "one"}
    assert manifest.git_remote == _REMOTE
    # The uv that converged the environment: probed once by the driver,
    # handed down, never a rebuild signal.
    assert manifest.uv_version == "0.0.0-test"
    assert manifest.image is None
    # And nothing the record already holds.
    assert set(_record(root, _FIRST_MANIFEST)) == {
        "schema_version", "recipe", "decisions", "git_remote", "uv_version", "hermeticity",
        "image",
    }


def test_the_recipe_runs_under_the_boundary(root: Path) -> None:
    """Whatever the host can enforce, the manifest records what it was —
    never what the mechanism matrix says it should have been."""
    _make(root, "first#baseline")

    manifest = assets.read(root / _FIRST_MANIFEST)
    assert manifest is not None
    assert manifest.hermeticity["mechanism"] == sandbox.detect().capability.kind


def test_a_result_built_once_has_no_universe_directory(analysis: Callable[..., Path]) -> None:
    """And it reads a declared input, which lives in the annex. With
    `filter=annex` the working tree holds the real bytes, so the project
    read root covers them — worth pinning, since it is what every recipe
    with an input depends on."""
    root = analysis(_READS, files={"data/catalog.txt": "measured\n"})

    result = _make(root, "fit")

    assert result.status == "ok", result.reason
    assert (root / "results/fit.txt").read_text() == "measured\n"
    assert (root / "results/.fit.manifest.json").is_file()
    record = _record(root, ".astra/records/fit.json")
    assert record["address"] == "fit"
    read = digest_bytes(b"measured\n")
    assert record["generated_by"]["inputs"] == [
        {"entry": "catalog", "content_hash": read.content_hash, "size": read.size}
    ]


def test_a_driven_analysis_is_built_in_its_own_folder_for_the_project(
    analysis: Callable[..., Path],
) -> None:
    """The result belongs to the project — its bytes, record and manifest
    land under the project's own `results/` and `.astra/` — while the
    recipe runs where its analysis's relative paths mean something."""
    spec = """
    spec_version: "0.1.0"

    analyses:
      stage: {path: stage}

    universes:
      u1:
        decisions: {stage.dec: a}

    outputs:
      report:
        type: report
        format: txt
        inputs: [stage.out]
        recipe:
          command: cat {inputs.stage.out} > {output}
    """
    stage = """
    spec_version: "0.1.0"

    decisions:
      dec:
        options: {a: {}, b: {}}

    universes:
      v1:
        decisions: {dec: b}

    outputs:
      out:
        type: metric
        format: txt
        decisions: [dec]
        recipe:
          command: echo {decisions.dec} $(cat here.txt) > {output}
    """
    root = analysis(spec, files={"stage/astra.yaml": stage, "stage/here.txt": "in stage\n"})

    made = _make(root, "stage.out#u1")
    report = _make(root, "report#u1", made)

    assert made.status == "ok", made.reason
    assert _task(root, "stage.out#u1").workdir == "stage"
    assert (root / "results/u1/stage.out.txt").read_text() == "a in stage\n"
    assert (root / "results/u1/.stage.out.manifest.json").is_file()
    assert _record(root, ".astra/records/u1/stage.out.json")["address"] == "stage.out#u1"
    assert not (root / "stage/results").exists()
    assert report.status == "ok", report.reason
    assert (root / "results/u1/report.txt").read_text() == "a in stage\n"


# ---- deciding whether to run at all ----------------------------------------


def test_an_unchanged_output_is_current(root: Path) -> None:
    made = _make(root, "first#baseline")
    again = _make(root, "first#baseline")

    assert again.status == "current"
    assert (again.content_hash, again.size) == (made.content_hash, made.size)


def test_a_skip_returns_the_recorded_digest_rather_than_rehashing(root: Path) -> None:
    """On a clone that has fetched no annex content the files are dangling
    symlinks, so a recompute would quietly report a different output."""
    made = _make(root, "first#baseline")
    (root / _FIRST).unlink()

    again = _make(root, "first#baseline")

    assert again.status == "current"
    assert again.content_hash == made.content_hash


def test_a_moved_environment_leaves_the_output_alone(root: Path) -> None:
    """The change this layer exists for. The recipe and the decisions still
    define exactly this output, so it is reported and kept — remaking it
    would spend the compute that a rewritten `uv.lock` never justified."""
    made = _make(root, "first#baseline")
    (root / _FIRST).write_text("untouched\n")
    _age(root, _FIRST_RECORD)

    again = _make(root, "first#baseline")

    assert again.status == "behind"
    assert "earlier environment" in again.reason
    assert again.content_hash == made.content_hash
    assert (root / _FIRST).read_text() == "untouched\n"


def test_refresh_remakes_what_is_only_behind(root: Path) -> None:
    """And the recipe really runs: the file the previous assertion left in
    place is overwritten, so this cannot pass by skipping too."""
    _make(root, "first#baseline")
    (root / _FIRST).write_text("untouched\n")
    _age(root, _FIRST_RECORD)

    again = _make(root, "first#baseline", refresh=True)

    assert again.status == "ok"
    assert (root / _FIRST).read_text() == "one\n"


def test_refresh_does_not_remake_what_is_current(root: Path) -> None:
    """`--refresh` widens the run by one state, not to everything. Without
    this it would be a rebuild-the-world flag wearing another name."""
    _make(root, "first#baseline")

    assert _make(root, "first#baseline", refresh=True).status == "current"


def _reads_first(result: TaskResult) -> list[dict[str, object]]:
    """What a record of `second` holds when it read *result*."""
    return [{"entry": "first", "content_hash": result.content_hash, "size": result.size}]


def test_a_behind_upstream_still_feeds_its_dependents(root: Path) -> None:
    """`behind` says the environment moved, not that the bytes are wrong —
    so a dependent proceeds on them rather than reporting blocked."""
    first = _make(root, "first#baseline")
    _age(root, _FIRST_RECORD)
    behind = _make(root, "first#baseline")
    assert behind.status == "behind"

    second = _make(root, "second#baseline", behind)

    assert second.status == "ok"
    assert _record(root, _SECOND_RECORD)["generated_by"]["inputs"] == _reads_first(first)


def test_a_task_whose_upstream_did_not_finish_is_blocked(root: Path) -> None:
    """Blocked without running: an exception would make Dask propagate to
    every dependent and stop 'who actually failed' being answerable."""
    upstream = TaskResult("first#baseline", "failed", reason="boom")

    result = _make(root, "second#baseline", upstream)

    assert result.status == "blocked"
    assert "first#baseline" in result.reason
    assert not (root / _SECOND).exists()


def test_a_downstream_task_takes_its_upstreams_answer(root: Path) -> None:
    """What the upstream task reported, never a rehash of the file: where
    the upstream was left alone on a clone without its content, the file
    is a pointer, and its hash is not the result's."""
    first = _make(root, "first#baseline")
    (root / _FIRST).write_text("not the bytes the upstream task reported\n")

    second = _make(root, "second#baseline", first)

    assert second.status == "ok"
    assert first.content_hash != digest_file(root / _FIRST).content_hash
    assert _record(root, _SECOND_RECORD)["generated_by"]["inputs"] == _reads_first(first)


# ---- downstream of a task that may be remade -------------------------------


def _ask(root: Path, key: str, *upstream: TaskResult) -> TaskResult:
    """Run one task the driver could not classify beforehand, because
    something it reads was about to be remade."""
    return worker.materialize(root, _task(root, key), _context(root), False, None, None, *upstream)


def test_a_byte_identical_remake_leaves_its_dependents_current(root: Path) -> None:
    """Where the cascade stops: the dependent's record still names the
    bytes it read, so there is nothing to make again."""
    second = _make(root, "second#baseline", _make(root, "first#baseline"))
    (root / _SECOND).write_text("untouched\n")
    remade = worker.execute(root, _task(root, "first#baseline"), {}, _context(root))
    assert remade.status == "ok"

    again = _ask(root, "second#baseline", remade)

    assert again.status == "current"
    assert again.content_hash == second.content_hash
    assert (root / _SECOND).read_text() == "untouched\n"


def test_a_remake_with_other_bytes_remakes_its_dependents(
    analysis: Callable[..., Path],
) -> None:
    root = analysis(_READS, files={"data/catalog.txt": "measured\n"})
    _make(root, "summary", _make(root, "fit"))
    assert (root / "results/summary.txt").read_text().strip() == "9"
    (root / "data/catalog.txt").write_text("measured again\n")
    remade = _make(root, "fit")
    assert remade.status == "ok"

    again = _ask(root, "summary", remade)

    assert again.status == "ok"
    assert (root / "results/summary.txt").read_text().strip() == "15"


# ---- the output directory the recipe owns ----------------------------------


def test_a_payload_from_an_earlier_format_does_not_survive_a_rebuild(root: Path) -> None:
    """The reset takes every file named after the output, not just the one
    the spec declares today — so re-declaring the serialization leaves no
    orphan behind for the publication view to trip over."""
    _make(root, "first#baseline")
    earlier = root / "results/baseline/first.json"
    earlier.write_text("from a run that declared another format\n")

    worker.execute(root, _task(root, "first#baseline"), {}, _context(root))

    assert not earlier.exists()
    assert (root / _FIRST).exists()


def test_a_sibling_output_is_left_alone_by_the_reset(root: Path) -> None:
    """Outputs share a directory, and under Dask they are written
    concurrently — so the reset may only ever touch what its own name
    names."""
    _make(root, "first#baseline")
    sibling = root / _SECOND
    sibling.write_text("a neighbour's bytes\n")

    worker.execute(root, _task(root, "first#baseline"), {}, _context(root))

    assert sibling.read_text() == "a neighbour's bytes\n"


def _recorded(root: Path) -> bool:
    """Whether anything says `first` was made."""
    return (root / _FIRST_RECORD).exists() or (root / _FIRST_MANIFEST).exists()


def test_a_failing_recipe_records_nothing(root: Path) -> None:
    (root / "astra.yaml").write_text(_SPEC.replace("> {output}", "> {output} && false", 1))

    result = _make(root, "first#baseline")

    assert result.status == "failed"
    assert "exited 1" in result.reason
    assert not _recorded(root)


def test_a_recipe_that_writes_nothing_fails(root: Path) -> None:
    """Exit 0 is not evidence of an output. Without this the run would
    commit a record for a file that is not there."""
    (root / "astra.yaml").write_text(_SPEC.replace("> {output}", "> /dev/null", 1))

    result = _make(root, "first#baseline")

    assert result.status == "failed"
    assert "left nothing" in result.reason
    assert not _recorded(root)


def test_a_recipe_that_writes_a_directory_fails(root: Path) -> None:
    """A result is one file, and a record of a directory would describe
    something that is not the output at all."""
    (root / "astra.yaml").write_text(
        _SPEC.replace("echo {decisions.word} > {output}", "mkdir -p {output}")
    )

    result = _make(root, "first#baseline")

    assert result.status == "failed"
    assert "a directory" in result.reason
    assert not _recorded(root)


def test_an_output_that_cannot_be_recorded_fails_rather_than_raises(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recording is fallible too, and a raise here reaches Dask, which
    re-raises in the driver and takes down every other task in flight —
    where reporting one failure and letting the rest finish is the whole
    point of owning the loop."""
    def refuse(*args: object, **kwargs: object) -> None:
        raise OSError("no space left on device")

    monkeypatch.setattr(assets, "write", refuse)

    result = _make(root, "first#baseline")

    assert result.status == "failed"
    assert "could not be recorded" in result.reason


def test_whatever_goes_wrong_is_returned_rather_than_raised(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The contract holds for failures nobody anticipated, not only for
    the ones with a message of their own."""
    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("nobody guarded this")

    monkeypatch.setattr(worker, "execute", explode)

    result = _make(root, "first#baseline")

    assert result.status == "failed"
    assert "RuntimeError: nobody guarded this" in result.reason


# ---- the environment gates -------------------------------------------------


def test_an_environment_that_moved_before_the_recipe_is_refused(root: Path) -> None:
    """A record may not claim an environment that had already been edited
    by the time the recipe ran — and the recipe is not run to find out."""
    result = worker.execute(
        root,
        _task(root, "first#baseline"),
        {},
        _context(root, environment="urn:lightcone:env:sha256:from-another-run"),
    )

    assert result.status == "failed"
    assert "environment changed" in result.reason
    assert not (root / _FIRST).exists()
    assert not _recorded(root)


def test_an_environment_that_moved_under_the_recipe_is_refused(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recipe ran, so there are bytes — and nothing says which
    environment made them, so nothing is recorded."""
    task, context = _task(root, "first#baseline"), _context(root)
    answers = iter([context.environment])
    monkeypatch.setattr(
        identity, "environment", lambda _: next(answers, "urn:lightcone:env:sha256:edited")
    )

    result = worker.execute(root, task, {}, context)

    assert result.status == "failed"
    assert "environment changed" in result.reason
    assert (root / _FIRST).read_text() == "one\n"
    assert not _recorded(root)


# ---- what a recipe may touch -----------------------------------------------

_TAMPERS = _SPEC.replace("echo {decisions.word}", "echo tampered > src/injected.py && echo one")


def test_a_recipe_cannot_write_outside_the_results_tree(root: Path) -> None:
    """The tree is read-only apart from the directory the output lands in.
    Sibling outputs inside that directory are *not* carved out — an output
    is one file, and a mechanism can only grant creating it through the
    directory that will hold it; the record's content hash is what says
    whether an output's bytes are its own."""
    if sandbox.detect().capability.kind == "none":
        pytest.skip("no sandbox mechanism on this host")
    (root / "src").mkdir()
    (root / "astra.yaml").write_text(_TAMPERS)

    assert _make(root, "first#baseline").status == "failed"
    assert not (root / "src" / "injected.py").exists()


def test_that_write_would_have_succeeded_unsandboxed(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mutation check. Without it the test above would pass on a host
    that enforces nothing, and pin nothing at all."""
    monkeypatch.setattr(sandbox, "detect", Unavailable)
    (root / "src").mkdir()
    (root / "astra.yaml").write_text(_TAMPERS)

    assert _make(root, "first#baseline").status == "ok"
    assert (root / "src" / "injected.py").read_text() == "tampered\n"


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
    proc = subprocess.run(
        [sys.executable, "-m", "lightcone.engine.worker", "first#baseline"],
        cwd=root,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr
    assert (root / _FIRST).read_text() == "one\n"
    assert _record(root, _FIRST_RECORD)["address"] == "first#baseline"
    assert dataset.status(root)


def test_the_module_reruns_unconditionally(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rerun is a rerun: the caller has already said what they want, and
    a staleness check would answer a question nobody asked."""
    monkeypatch.chdir(root)
    _make(root, "first#baseline")
    (root / _FIRST).write_text("untouched\n")

    assert worker.main(["first#baseline"]) == 0
    assert (root / _FIRST).read_text() == "one\n"


def test_the_module_converges_the_environment(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rerun checks out the lock but never `.venv`, and the recipe's own
    `uv run --no-sync` silently creates an *empty* environment where none
    exists — so the entry point syncs before anything executes, or a fresh
    clone's rerun would record the lock's environment over a recipe that
    ran in a bare venv."""
    monkeypatch.chdir(root)
    shutil.rmtree(root / ".venv")

    assert worker.main(["first#baseline"]) == 0
    assert (root / ".venv").exists()
    assert (root / _FIRST).read_text() == "one\n"


def test_the_module_reads_what_its_task_read_from_the_records_in_the_tree(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """There is no graph in flight to take an upstream's result from, so
    its record stands in — and not a rehash of its file, for the reason
    no dependent ever rehashes."""
    monkeypatch.chdir(root)
    first = _make(root, "first#baseline")
    (root / _FIRST).write_text("not the bytes the record gives\n")

    assert worker.main(["second#baseline"]) == 0
    assert _record(root, _SECOND_RECORD)["generated_by"]["inputs"] == _reads_first(first)


def test_the_module_refuses_a_task_whose_upstream_has_no_record(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(root)

    assert worker.main(["second#baseline"]) == 2
    assert "no valid record" in capsys.readouterr().err
    assert not (root / _SECOND).exists()


def test_the_module_refuses_anything_but_one_task() -> None:
    assert worker.main([]) == 2
    assert worker.main(["first#baseline", "second#baseline"]) == 2


def test_the_module_refuses_an_output_that_does_not_exist(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A result's name alone is not a task: a rerun makes exactly one."""
    monkeypatch.chdir(root)

    assert worker.main(["nothing#baseline"]) == 2
    assert "no output `nothing#baseline`" in capsys.readouterr().err
    assert worker.main(["first"]) == 2


def test_only_the_lookup_can_blame_the_target(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """This is the entry point every `[DATALAD RUNCMD]` record names, so a
    rerun failing for an unrelated reason must not misdiagnose itself as
    "no output <x>"."""
    monkeypatch.chdir(root)

    def explode(_: Path) -> None:
        raise KeyError("something inside the resolver")

    monkeypatch.setattr(worker.plan, "build", explode)
    with pytest.raises(KeyError):
        worker.main(["first#baseline"])
    assert "no output" not in capsys.readouterr().err


def test_the_module_refuses_an_analysis_astra_rejects(
    analysis: Callable[..., Path], monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A declared input that is not there is ASTRA's to report, and it
    reaches whoever is reading a rerun as a refusal, not a traceback."""
    root = analysis(_READS, files={"data/catalog.txt": "measured\n"})
    monkeypatch.chdir(root)
    (root / "data/catalog.txt").unlink()

    assert worker.main(["fit"]) == 2
    assert "does not validate" in capsys.readouterr().err
    assert not (root / "results/fit.txt").exists()
