"""The specification's runner layer, rule by rule.

ASTRA's table of rules has a layer only a runner can answer for: what it
writes, where it runs a recipe, and what it refuses to build from. `lc` is
a runner, so each of those rules has a test here, named after its code,
that runs the real engine on a real project and looks at what it left in
git. `test_every_runner_rule_is_answered_for` reads the rules from the
installed astra-spec, so a rule a later release adds fails here until it
is answered too.

Two rules are kept by not doing what they govern: lc builds nothing from
an analysis declared with a `commit`, so it never drives one on a commit's
files (`NOT_AT_COMMIT`) and never reads a result from one
(`STALE_INPUT`). The tests pin the refusal, which is the whole of lc's
answer.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from collections.abc import Callable, Iterator
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest
from conftest import CLUSTER_ID, _cluster, _Inline, _Meanwhile

from lightcone.engine import dataset, identity, plan, sandbox, worker
from lightcone.engine import materialize as engine
from lightcone.engine.project import ProjectError
from lightcone.engine.sandbox import Unavailable

RULES = [
    rule["code"]
    for rule in json.loads((files("astra.schema") / "rules" / "rules.json").read_text())["rules"]
    if rule["layer"] == "runner"
]


def test_every_runner_rule_is_answered_for() -> None:
    assert RULES, "astra-spec's catalogue has a runner layer"
    unanswered = [code for code in RULES if f"test_{code.lower()}" not in globals()]
    assert unanswered == []


_SPEC = """
spec_version: "0.1.0"

inputs:
  catalog: {path: data/catalog.txt}

decisions:
  method:
    options: {alpha: {}, beta: {}}

outputs:
  fit:
    type: metric
    format: txt
    inputs: [catalog]
    decisions: [method]
    recipe:
      command: cat {inputs.catalog} > {output} && echo {decisions.method} >> {output}

  summary:
    type: report
    format: txt
    inputs: [fit]
    recipe:
      command: wc -l < {inputs.fit} > {output}

universes:
  baseline:
    decisions: {method: alpha}
"""


@pytest.fixture
def root(analysis: Callable[..., Path]) -> Path:
    return analysis(_SPEC, {"data/catalog.txt": "1\n2\n"})


def _record(root: Path, path: str) -> dict[str, Any]:
    record: dict[str, Any] = json.loads((root / path).read_text())
    return record


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _commits(root: Path) -> list[str]:
    return dataset._git(["log", "--format=%s"], cwd=root).splitlines()


# ---- what a runner writes ---------------------------------------------------

#: Results with two names: the project declares `inner` twice, by itself and
#: through `stage`, and its universes drive it through both.
_PAIR = {
    "astra.yaml": """
        spec_version: "0.1.0"
        analyses:
          stage: {path: stage}
          inner: {path: stage/inner}
        outputs:
          report:
            type: report
            format: txt
            inputs: [inner.fit, stage.inner.fit]
            recipe:
              command: cat {inputs} > {output}
        universes:
          u1: {decisions: {inner.method: alpha}}
          u2: {decisions: {stage.inner.method: beta}}
        """,
    "stage/astra.yaml": """
        spec_version: "0.1.0"
        analyses:
          inner: {path: inner}
        """,
    "stage/inner/astra.yaml": """
        spec_version: "0.1.0"
        decisions:
          method:
            options: {alpha: {}, beta: {}}
        outputs:
          fit:
            type: metric
            format: txt
            decisions: [method]
            recipe:
              command: echo {decisions.method} > {output}
        """,
}


def test_unrecorded_result(analysis: Callable[..., Path], inline: None) -> None:
    """Each result lc makes has its bytes at each default place and a record
    at each place, the record naming that place's own address — and a
    result with two names is still made once."""
    spec = _PAIR["astra.yaml"]
    root = analysis(spec, {name: text for name, text in _PAIR.items() if name != "astra.yaml"})

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.ok
    assert sorted(report.made) == ["inner.fit#u1", "inner.fit#u2", "report#u1", "report#u2"]
    twice = plan.build(root).tasks["inner.fit#u1"].result
    assert [place.address for place in twice.places] == ["inner.fit#u1", "stage.inner.fit#u1"]
    for place in twice.places:
        assert (root / place.file_path).read_text() == "alpha\n"
        record = _record(root, place.record_path)
        assert record["address"] == place.address
        assert record["content_hash"] == _sha256(root / place.file_path)
    assert (root / "results/u1/report.txt").read_text() == "alpha\nalpha\n"
    assert dataset.status(root) == []  # recorded, and committed


def test_false_record(root: Path, inline: None) -> None:
    """Every field of a record is true of the run that wrote it: the bytes
    the recipe made, the bytes it read through each entry, the commit and
    the environment it ran in, and who ran it."""
    started_at = dataset.head(root)

    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok

    fit = _record(root, ".astra/records/baseline/fit.json")
    assert fit["content_hash"] == _sha256(root / "results/baseline/fit.txt")
    assert fit["size"] == (root / "results/baseline/fit.txt").stat().st_size
    made = fit["generated_by"]
    assert made["inputs"] == [
        {"entry": "catalog", "content_hash": _sha256(root / "data/catalog.txt"), "size": 4}
    ]
    assert made["commit"] == started_at
    assert made["environment"] == {"iri": identity.environment(root)}
    assert made["runner"]["id"] == "lightcone-cli"
    assert made["started"] <= made["finished"]
    # What a result read of another is the bytes that one's run made.
    summary = _record(root, ".astra/records/baseline/summary.json")
    assert summary["generated_by"]["inputs"] == [
        {"entry": "fit", "content_hash": fit["content_hash"], "size": fit["size"]}
    ]


def test_unrecreatable_environment(root: Path, inline: None) -> None:
    """A record names its environment by the files of its commit, so a run
    is refused while one of them is not in git — which a clean tree does
    not rule out, as an ignored file is clean."""
    with (root / ".gitignore").open("a") as ignore:
        ignore.write("uv.lock\n")
    dataset._git(["rm", "--cached", "--quiet", "uv.lock"], cwd=root)
    dataset._git(["commit", "--quiet", "-am", "ignore the lock"], cwd=root)
    assert dataset.status(root) == []

    with pytest.raises(ProjectError, match=r"uv\.lock.*UNRECREATABLE_ENVIRONMENT"):
        engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert not (root / "results/baseline").exists()


def test_a_rerun_answers_for_its_environment_too(
    root: Path,
    inline: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The rerun entry point writes a record as a run does, naming the
    commit it is on, so it is refused where a run would be."""
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok
    made = _record(root, ".astra/records/baseline/fit.json")
    with (root / ".gitignore").open("a") as ignore:
        ignore.write("uv.lock\n")
    dataset._git(["rm", "--cached", "--quiet", "uv.lock"], cwd=root)
    dataset._git(["commit", "--quiet", "-am", "ignore the lock"], cwd=root)
    assert dataset.status(root) == []
    monkeypatch.chdir(root)

    assert worker.main(["fit#baseline"]) == 2

    assert "UNRECREATABLE_ENVIRONMENT" in capsys.readouterr().err
    assert _record(root, ".astra/records/baseline/fit.json") == made


def test_the_environment_a_record_names_is_in_its_commit(root: Path, inline: None) -> None:
    """The other half of `UNRECREATABLE_ENVIRONMENT`: checked out at the
    commit a record names, the project's files give the IRI it records."""
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok
    made = _record(root, ".astra/records/baseline/fit.json")["generated_by"]

    checkout = root.parent / "at-the-commit"
    dataset._git(["worktree", "add", "--quiet", str(checkout), made["commit"]], cwd=root)

    assert identity.environment(checkout) == made["environment"]["iri"]
    assert dataset.untracked(root, identity.files(root)) == []


# ---- where a recipe runs ----------------------------------------------------


def test_uncommitted_changes(root: Path, inline: None) -> None:
    """A run starts only from a commit's checkout: a tracked edit and an
    untracked file both refuse it, and an ignored file does not."""
    (root / "notes.md").write_text("untracked\n")
    with pytest.raises(ProjectError, match="uncommitted changes"):
        engine.materialize(root, [], cluster_id=CLUSTER_ID)
    (root / "notes.md").unlink()

    (root / "data/catalog.txt").write_text("edited\n")
    with pytest.raises(ProjectError, match="uncommitted changes"):
        engine.materialize(root, [], cluster_id=CLUSTER_ID)
    dataset._git(["checkout", "--quiet", "--", "data/catalog.txt"], cwd=root)

    (root / ".venv" / "scratch").write_text("ignored\n")
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok


def test_an_edit_while_a_run_is_in_flight_is_not_what_its_recipes_run_on(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`UNCOMMITTED_CHANGES` holds for the whole run, not only its start:
    each recipe runs in a checkout of the run's commit that is lc's own,
    so an edit in the project meanwhile is none of what it reads — and
    is left exactly where it was."""
    start = dataset.head(root)
    committed = _sha256(root / "data/catalog.txt")

    def edit() -> None:
        # As an editor saves: a new file under the old name. What the run
        # reads is read-only, so it cannot be written to in place.
        with pytest.raises(PermissionError):
            (root / "data/catalog.txt").open("a")
        (root / "data/catalog.txt").unlink()
        (root / "data/catalog.txt").write_text("edited while the graph ran\n")

    _cluster(monkeypatch, _Meanwhile(edit))

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.ok and sorted(report.made) == ["fit#baseline", "summary#baseline"]
    assert (root / "results/baseline/fit.txt").read_text() == "1\n2\nalpha\n"
    made = _record(root, ".astra/records/baseline/fit.json")["generated_by"]
    assert made["commit"] == start
    assert made["inputs"][0]["content_hash"] == committed
    assert dataset.status(root) == [(" M", "data/catalog.txt")]


def test_a_commit_while_a_run_is_in_flight_is_not_the_one_its_records_name(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A record names the commit its recipe ran on: the one the run
    started from, whatever was committed in the project meanwhile. Its
    results land on top of that."""
    start = dataset.head(root)

    def commit() -> None:
        (root / "notes.md").write_text("committed while the graph ran\n")
        dataset._git(["add", "--", "notes.md"], cwd=root)
        dataset._git(["commit", "--quiet", "-m", "notes"], cwd=root)

    _cluster(monkeypatch, _Meanwhile(commit))

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.ok
    for name in ("fit", "summary"):
        made = _record(root, f".astra/records/baseline/{name}.json")["generated_by"]
        assert made["commit"] == start
    assert _commits(root)[2] == "notes"
    assert dataset.status(root) == []


def test_what_a_run_writes_is_no_edit_of_its_checkout(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Records and results are what a run's checkout may change by: a file
    another task is still writing beside its result does not stop a
    finished one from being recorded, and never reaches the project."""

    class Writing(_Inline):
        def completed(self, handles: list[object]) -> Iterator[object]:
            scratch = root / ".lightcone/checkouts/0/results/baseline/scratch.tmp"
            scratch.write_text("another task, mid-write\n")
            yield from handles

    _cluster(monkeypatch, Writing())

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.ok and sorted(report.made) == ["fit#baseline", "summary#baseline"]
    assert not (root / "results/baseline/scratch.tmp").exists()
    assert dataset.status(root) == []


def test_a_recipe_that_edits_its_checkout_leaves_what_was_made_unrecorded(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A checkout changed by more than records and results is no longer
    the commit's: nothing arriving after that is recorded as made from it."""

    class Editing(_Inline):
        def completed(self, handles: list[object]) -> Iterator[object]:
            (root / ".lightcone/checkouts/0/notes.md").write_text("left by a recipe\n")
            yield from handles

    _cluster(monkeypatch, Editing())
    before = _commits(root)

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert not report.ok and report.made == []
    assert sorted(report.failed) == ["fit#baseline", "summary#baseline"]
    assert any("notes.md" in w and "UNCOMMITTED_CHANGES" in w for w in report.warnings)
    assert _commits(root) == before
    assert dataset.status(root) == []


#: A driven analysis whose recipe names a file by a path from its own folder.
_STAGED = {
    "astra.yaml": """
        spec_version: "0.1.0"
        analyses:
          stage: {path: stage}
        outputs:
          report:
            type: report
            format: txt
            inputs: [stage.fit]
            recipe:
              command: cat {inputs.stage.fit} > {output}
        universes:
          u1: {decisions: {stage.method: alpha}}
        """,
    "stage/astra.yaml": """
        spec_version: "0.1.0"
        decisions:
          method:
            options: {alpha: {}, beta: {}}
        outputs:
          fit:
            type: metric
            format: txt
            decisions: [method]
            recipe:
              command: cat here.txt > {output} && pwd >> {output}
        """,
    "stage/here.txt": "in the stage's folder\n",
}


def test_wrong_working_directory(analysis: Callable[..., Path], inline: None) -> None:
    """A recipe runs in its own analysis's folder even when the project's
    universe drives it: its result goes to the project's `results/`, and
    its relative paths are read from where its `astra.yaml` is."""
    spec = _STAGED["astra.yaml"]
    root = analysis(spec, {name: text for name, text in _STAGED.items() if name != "astra.yaml"})

    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok

    task = plan.build(root).tasks["stage.fit#u1"]
    assert task.workdir == "stage"
    assert task.result.places[0].file_path == "results/u1/stage.fit.txt"
    here, cwd = (root / "results/u1/stage.fit.txt").read_text().splitlines()
    assert here == "in the stage's folder"
    # In the run's own checkout of the commit, as every recipe is.
    assert Path(cwd).resolve() == (root / ".lightcone/checkouts/0/stage").resolve()


# ---- analyses from a commit -------------------------------------------------

_LIB = """
spec_version: "0.1.0"
decisions:
  method:
    options: {alpha: {}, beta: {}}
outputs:
  fit:
    type: metric
    format: txt
    decisions: [method]
    recipe:
      command: echo {decisions.method} > {output}
  once:
    type: metric
    format: txt
    recipe:
      command: echo once > {output}
universes:
  v1:
    decisions: {method: beta}
"""


def _from_commit(analysis: Callable[..., Path], spec: str) -> Path:
    """A project that declares `lib` at the commit that added it."""
    root = analysis('spec_version: "0.1.0"\n', {"lib/astra.yaml": _LIB})
    (root / "astra.yaml").write_text(spec % dataset.head(root))
    dataset.save(root, [root / "astra.yaml"], "declare lib at a commit")
    return root


def test_rebuilt_from_commit(analysis: Callable[..., Path], inline: None) -> None:
    """A result that belongs to an analysis from a commit is planned, as
    evidence cites it, and is no task: a run makes the project's own
    results and writes nothing where that analysis keeps its."""
    root = _from_commit(
        analysis,
        """
spec_version: "0.1.0"
analyses:
  lib: {path: lib, commit: "%s"}
outputs:
  alone:
    type: metric
    format: txt
    recipe:
      command: echo alone > {output}
findings:
  cited:
    claim: The library's result is what it was.
    evidence:
      - artifact: lib.once
""",
    )

    assert list(plan.build(root).tasks) == ["alone"]

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.ok and report.made == ["alone"]
    assert not (root / "lib/results").exists() and not (root / "lib/.astra").exists()


def test_stale_input(analysis: Callable[..., Path], inline: None) -> None:
    """lc builds nothing from a result from a commit that is missing,
    stale or lacks its recorded bytes: here `lib` was never built at that
    commit, so `lib.fit#v1` has no record there. What reads it is blocked,
    by `--check` as by a run, and what does not is made."""
    root = _from_commit(
        analysis,
        """
spec_version: "0.1.0"
analyses:
  lib: {path: lib, commit: "%s"}
outputs:
  report:
    type: report
    format: txt
    inputs: [lib.fit#v1]
    recipe:
      command: cat {inputs} > {output}
  alone:
    type: metric
    format: txt
    recipe:
      command: echo alone > {output}
""",
    )

    checked = engine.check(root, [])
    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert checked.blocked == ["report"] and "alone" in checked.planned
    assert any("lib:fit#v1" in w and "STALE_INPUT" in w for w in checked.warnings)
    assert report.blocked == ["report"] and report.made == ["alone"]
    assert any(w.startswith("lib:fit#v1: it is missing") for w in report.warnings)
    assert not (root / "results/report.txt").exists()
    assert not list((root / ".lightcone").rglob("commits"))  # nothing given to any tree


def test_not_at_commit(analysis: Callable[..., Path]) -> None:
    """lc drives no analysis from a commit: `lib.fit` read without a pin is
    made in `u1` on exactly that commit's files, and lc runs recipes on
    the run's own commit only."""
    root = _from_commit(
        analysis,
        """
spec_version: "0.1.0"
analyses:
  lib: {path: lib, commit: "%s"}
outputs:
  alone:
    type: metric
    format: txt
    inputs: [lib.fit]
    recipe:
      command: cat {inputs} > {output}
universes:
  u1: {decisions: {lib.method: alpha}}
""",
    )

    with pytest.raises(ProjectError, match=r"`lib\.fit#u1` is made on the files of an analysis"):
        plan.build(root)


# ---- what a recipe reaches --------------------------------------------------


@pytest.fixture
def outside() -> Iterator[Path]:
    """A file no analysis declares, outside the project — under `$HOME`,
    because `/tmp` is in the write baseline and so granted."""
    elsewhere = Path(tempfile.mkdtemp(prefix="lc-runner-rules-", dir=Path.home()))
    (elsewhere / "undeclared.txt").write_text("undeclared\n")
    try:
        yield elsewhere / "undeclared.txt"
    finally:
        shutil.rmtree(elsewhere, ignore_errors=True)


def _reaching(analysis: Callable[..., Path], outside: Path) -> Path:
    return analysis(
        _SPEC.replace("cat {inputs.catalog}", f"cat {{inputs.catalog}} {outside}"),
        {"data/catalog.txt": "1\n2\n"},
    )


def test_undeclared_read(analysis: Callable[..., Path], outside: Path, inline: None) -> None:
    """A recipe reads the input its output declares, and a file outside the
    project that nothing declares is out of its reach. Inside the project
    the tree is readable whole — the boundary grants, it cannot subtract —
    so there the rule is the author's to keep."""
    if sandbox.detect().capability.kind == "none":
        pytest.skip("no sandbox mechanism on this host")
    root = _reaching(analysis, outside)

    report = engine.materialize(root, ["fit"], cluster_id=CLUSTER_ID)

    assert report.failed == ["fit#baseline"]
    assert dataset.status(root) == []


def test_that_read_would_have_succeeded_unsandboxed(
    analysis: Callable[..., Path], outside: Path, inline: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mutation check: with no mechanism the same recipe reads the file."""
    monkeypatch.setattr(sandbox, "detect", Unavailable)
    root = _reaching(analysis, outside)

    assert engine.materialize(root, ["fit"], cluster_id=CLUSTER_ID).ok
    assert (root / "results/baseline/fit.txt").read_text() == "1\n2\nundeclared\nalpha\n"


def _reaching_the_project(analysis: Callable[..., Path], tmp_path: Path) -> Path:
    """A recipe that names a file of the project's own working tree, where
    the researcher works, by its absolute path."""
    theirs = tmp_path / "analysis" / "notes.md"
    return analysis(
        _SPEC.replace("cat {inputs.catalog}", f"cat {{inputs.catalog}} {theirs}"),
        {"data/catalog.txt": "1\n2\n", "notes.md": "the researcher's\n"},
    )


def test_a_recipe_reads_its_checkout_not_the_project_it_was_taken_from(
    analysis: Callable[..., Path], tmp_path: Path, inline: None
) -> None:
    """The run's checkout is the tree a recipe is given. The project's own
    working tree, which may hold anything by now, is outside it."""
    if sandbox.detect().capability.kind == "none":
        pytest.skip("no sandbox mechanism on this host")
    root = _reaching_the_project(analysis, tmp_path)

    report = engine.materialize(root, ["fit"], cluster_id=CLUSTER_ID)

    assert report.failed == ["fit#baseline"]
    assert dataset.status(root) == []


def test_that_read_of_the_project_would_have_succeeded_unsandboxed(
    analysis: Callable[..., Path], tmp_path: Path, inline: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mutation check."""
    monkeypatch.setattr(sandbox, "detect", Unavailable)
    root = _reaching_the_project(analysis, tmp_path)

    assert engine.materialize(root, ["fit"], cluster_id=CLUSTER_ID).ok
    assert "the researcher's" in (root / "results/baseline/fit.txt").read_text()


def test_code_outside_analysis(analysis: Callable[..., Path], inline: None) -> None:
    """A command that names code outside its analysis's folder is ASTRA's to
    find, as only the command's template tells a path it names from one a
    placeholder gives; lc reports it with every run and with `--check`."""
    root = analysis(
        _STAGED["astra.yaml"],
        {
            "stage/astra.yaml": _STAGED["stage/astra.yaml"].replace(
                "cat here.txt", "cat ../shared.txt"
            ),
            "shared.txt": "above the stage\n",
        },
    )

    for report in (
        engine.check(root, []),
        engine.materialize(root, [], cluster_id=CLUSTER_ID),
    ):
        assert any("../shared.txt" in w and "outside its analysis" in w for w in report.warnings)
    assert report.ok
