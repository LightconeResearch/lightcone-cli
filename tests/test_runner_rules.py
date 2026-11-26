"""The specification's runner layer, rule by rule.

ASTRA's table of rules has a layer only a runner can answer for: what it
writes, and what that says of its run. `lc` is a runner, so each such
rule it keeps has a test here, named after its code, that runs the real
engine on a real project and looks at what it left in git — and asks
ASTRA's own checker whether what lc wrote holds.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import CLUSTER_ID, FIRST, since

from lightcone.engine import dataset, identity, plan, staging, worker
from lightcone.engine import materialize as engine
from lightcone.engine.project import ProjectError

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


def _ignore_the_lock(root: Path) -> None:
    """Leave `uv.lock` on disk but out of git: a clean tree, and a commit
    that cannot recreate the environment."""
    with (root / ".gitignore").open("a") as ignore:
        ignore.write("uv.lock\n")
    dataset._git(["rm", "--cached", "--quiet", "uv.lock"], cwd=root)
    dataset._git(["commit", "--quiet", "-am", "ignore the lock"], cwd=root)
    assert dataset.status(root) == []


# ---- what a runner writes ---------------------------------------------------


def test_unrecorded_result(root: Path, inline: None) -> None:
    """Each result lc makes has its bytes at its default place and a record
    at its place, naming that place's address — committed with the bytes,
    and kept in git, so a clone with no annex content can read it."""
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok

    for name in ("fit", "summary"):
        place = f".astra/records/baseline/{name}.json"
        record = _record(root, place)
        assert record["address"] == f"{name}#baseline"
        assert record["content_hash"] == _sha256(root / f"results/baseline/{name}.txt")
        # The commit that made the result carries its record.
        made = dataset._git(["log", "-1", "--format=%H %s", "--", place], cwd=root).split()
        assert made[1:] == ["[DATALAD", "RUNCMD]", f"{name}#baseline"]
        committed = dataset._git(["show", "--name-only", "--format=", made[0]], cwd=root).split()
        assert place in committed and f"results/baseline/{name}.txt" in committed
        # Git holds the record itself, not a pointer to annexed content.
        assert json.loads(dataset._git(["show", f"HEAD:{place}"], cwd=root)) == record
    assert dataset.status(root) == []


def test_false_record(root: Path, inline: None) -> None:
    """Every field of a record is true of the run that wrote it: the bytes
    the recipe made, the bytes it read through each entry, the commit and
    the environment it ran in, and who ran it — so ASTRA's checker finds
    each result unchanged, with its bytes present."""
    from astra import api

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
    assert made["runner"] == {"id": "lightcone-cli", "version": worker.lc_version()}
    assert made["started"] <= made["finished"]
    # What a result read of another is the bytes that one's run made.
    summary = _record(root, ".astra/records/baseline/summary.json")
    assert summary["generated_by"]["inputs"] == [
        {"entry": "fit", "content_hash": fit["content_hash"], "size": fit["size"]}
    ]

    report = api.status(root)
    for key in ("fit#baseline", "summary#baseline"):
        found = report.statuses[key]
        assert found.state == "unchanged", found.why
        assert found.bytes is not None and found.bytes.state == "present"
        assert not [broken for broken in found.why if broken.level == "error"]


def test_a_rerun_refuses_a_tree_its_record_would_not_describe(
    root: Path,
    inline: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The rerun entry point records the commit it is on, so the tree has
    to be that commit's: all a rerun may have changed is the result's own
    files, which `datalad rerun` removes before running it."""
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok
    made = _record(root, ".astra/records/baseline/fit.json")
    monkeypatch.chdir(root)

    (root / "notes.md").write_text("not committed\n")
    assert worker.main(["fit#baseline"]) == 2
    assert "uncommitted changes" in capsys.readouterr().err
    assert _record(root, ".astra/records/baseline/fit.json") == made
    (root / "notes.md").unlink()

    # The result's own files are the rerun's to replace.
    for own in ("results/baseline/fit.txt", ".astra/records/baseline/fit.json"):
        (root / own).unlink()
    assert worker.main(["fit#baseline"]) == 0
    again = _record(root, ".astra/records/baseline/fit.json")
    assert again["content_hash"] == made["content_hash"]
    assert again["generated_by"]["commit"] == dataset.head(root)


def test_unrecreatable_environment(root: Path, inline: None) -> None:
    """A record names its environment by the files of its commit, so a run
    is refused while one of them is not in git — which a clean tree does
    not rule out, as an ignored file is clean."""
    _ignore_the_lock(root)

    with pytest.raises(ProjectError, match=r"uv\.lock.*UNRECREATABLE_ENVIRONMENT"):
        engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert not (root / "results/baseline").exists()


def test_an_environment_file_committed_as_a_symlink_is_refused(
    root: Path, inline: None
) -> None:
    """A symlink is committed as the path it points to: a pin read through
    one, to a file outside the repository, is not in the commit a record
    names, however clean the tree."""
    outside = root.parent / "python-version"
    outside.write_text((root / ".python-version").read_text())
    (root / ".python-version").unlink()
    (root / ".python-version").symlink_to(outside)
    dataset.save(root, [root / ".python-version"], "pin the interpreter through a link")
    assert not dataset.status(root)

    with pytest.raises(ProjectError, match=r"\.python-version.*UNRECREATABLE_ENVIRONMENT"):
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
    _ignore_the_lock(root)
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
    assert dataset.not_in_git(root, identity.files(root)) == []


# ---- what a runner builds, and where -------------------------------------------

#: An analysis the project drives: its output is made with the option the
#: project's universe selects, and says where its recipe ran.
_DRIVEN = """
spec_version: "0.1.0"
decisions:
  d:
    options: {a: {}, b: {}}
outputs:
  where:
    type: data
    format: txt
    decisions: [d]
    recipe:
      command: echo {decisions.d} > {output} && pwd >> {output}
"""

#: The project that drives it, declared as `%s`.
_DRIVES = """
spec_version: "0.1.0"
analyses:
  stage: %s
outputs:
  copy:
    type: report
    format: txt
    inputs: [stage.where]
    recipe:
      command: cat {inputs} > {output} && pwd >> {output}
universes:
  u1:
    decisions: {stage.d: a}
"""


def test_wrong_working_directory(analysis: Callable[..., Path], inline: None) -> None:
    """Each recipe runs in its own analysis's folder, even when driven: the
    project's universe selects `stage`'s option, and `stage`'s recipe runs
    in `stage`, writing to the project's place."""
    root = analysis(_DRIVES % "{path: stage}", {"stage/astra.yaml": _DRIVEN})
    tasks = plan.build(root).tasks

    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok

    lines = tasks["stage.where#u1"].path.read_text().splitlines()
    workdir = Path(lines[1])
    assert lines[0] == "a" and workdir.name == "stage"
    assert workdir.is_relative_to(root / staging.RUNS)
    assert tasks["copy#u1"].path.read_text().splitlines()[-1] == str(workdir.parent)


def test_not_at_commit(analysis: Callable[..., Path], inline: None) -> None:
    """Driving `stage` as a commit has it would run its recipe on that
    commit's files, which lc does not do: the run is refused before
    anything runs, naming the result."""
    root = analysis(FIRST, {"stage/astra.yaml": _DRIVEN})
    spec = root / "astra.yaml"
    spec.write_text(_DRIVES % f'{{path: stage, commit: "{dataset.head(root)}"}}')
    dataset.save(root, [spec], "drive stage as a commit has it")
    head = dataset.head(root)

    with pytest.raises(ProjectError, match="`stage.where#u1`.*NOT_AT_COMMIT"):
        engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert dataset.head(root) == head


def test_rebuilt_from_commit(analysis: Callable[..., Path], inline: None) -> None:
    """A result from a commit is never built: `previous.fit` is no task, and
    what reads it reads it as its record at that commit gives it, which
    ASTRA's checker then finds unchanged."""
    root = analysis(FIRST)
    at = since(root)
    assert "previous:fit" not in plan.build(root).tasks

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert sorted(report.made) == ["compare", "fit"], (report.failed, report.warnings)
    assert (root / "results/compare.txt").read_text().splitlines() == ["second", "first"]
    recorded = plan.build(root).checked.statuses["previous:fit"].record
    assert recorded is not None
    read = _record(root, ".astra/records/compare.json")["generated_by"]["inputs"]
    assert {
        "entry": "previous.fit",
        "content_hash": recorded.content_hash,
        "size": recorded.size,
    } in read
    key = dataset.keys(root, [f"{at}:results/fit.txt"])[f"{at}:results/fit.txt"]
    assert recorded.content_hash == _sha256(dataset.locations(root, [key])[key])
    assert engine.check(root, []).up_to_date


def test_stale_input(analysis: Callable[..., Path], inline: None) -> None:
    """`previous.fit` has no record at the commit `previous` names: nothing
    is built from it. `--check` says what it blocks, and a run makes what
    does not read it and blocks what does."""
    root = analysis(FIRST)
    since(root, made=False)

    checked = engine.check(root, [])
    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert (checked.blocked, list(checked.planned)) == (["compare"], ["fit"])
    assert any("previous:fit" in w and "STALE_INPUT" in w for w in checked.warnings)
    assert (report.made, report.blocked) == (["fit"], ["compare"])
    assert any("previous:fit" in w and "STALE_INPUT" in w for w in report.warnings)
    assert not (root / ".astra/records/compare.json").exists()
    assert not dataset.status(root)


def test_stale_input_of_a_result_that_lacks_its_recorded_bytes(
    analysis: Callable[..., Path], inline: None
) -> None:
    """At the commit `previous` names, `fit`'s bytes are not the ones its
    record gives, which its stand-in there says (ASTRA's `ABSENT_RESULT`):
    `--check` says so, and nothing is built from them."""
    root = analysis(FIRST)
    _overwrite_fit(root)
    since(root, made=False)

    checked = engine.check(root, [])
    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert checked.blocked == ["compare"], (checked.planned, checked.warnings)
    assert report.blocked == ["compare"], (report.made, report.warnings)
    assert any("lacks its recorded bytes" in w and "STALE_INPUT" in w for w in report.warnings)
    assert not (root / ".astra/records/compare.json").exists()


def test_stale_input_of_a_result_whose_stand_in_vouches_for_nothing(
    analysis: Callable[..., Path], inline: None
) -> None:
    """A git-annex key of another backend than SHA256 gives no SHA-256, so
    nothing says `fit`'s bytes are not its record's until a run has them:
    they are hashed where it gives the commit's tree, before anything reads
    them."""
    root = analysis(FIRST)
    dataset._git(["config", "annex.backend", "MD5E"], cwd=root)
    _overwrite_fit(root)
    since(root, made=False)

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.blocked == ["compare"], (report.made, report.warnings)
    assert any("lacks its recorded bytes" in w and "STALE_INPUT" in w for w in report.warnings)


def _overwrite_fit(root: Path) -> None:
    """Make `fit`, then commit other bytes in its place under its record."""
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok
    fit = root / "results/fit.txt"
    # Removed in a commit of its own: git-annex keeps a modified file's
    # backend, and annexed thin, writing it in place writes its object.
    fit.unlink()
    dataset.save(root, [fit], "remove fit")
    fit.write_text("not what the record gives\n")
    dataset.save(root, [fit], "write over fit, and leave its record")


def test_stale_input_blocks_only_what_a_run_would_make(
    analysis: Callable[..., Path], inline: None
) -> None:
    """`compare` was made reading `previous.fit` at one commit; `previous`
    now names another, where `fit`'s record is the same and its bytes are
    gone. `compare` reads nothing a run would make it read again, so
    `--check`, like a run, leaves it alone."""
    root = analysis(FIRST)
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok
    branch = dataset._git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=root).strip()
    dataset._git(["checkout", "-q", "-b", "without-bytes"], cwd=root)
    dataset._git(["rm", "-q", "results/fit.txt"], cwd=root)
    dataset._git(["commit", "-q", "-m", "keep fit's record, not its bytes"], cwd=root)
    without = dataset.head(root)
    dataset._git(["checkout", "-q", branch], cwd=root)
    at = since(root, made=False)
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok
    spec = root / "astra.yaml"
    spec.write_text(spec.read_text().replace(at, without))
    dataset.save(root, [spec], "read previous where fit's bytes are gone")

    checked = engine.check(root, [])
    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert "compare" in checked.unchanged, (checked.blocked, checked.planned, checked.warnings)
    assert "compare" in report.unchanged, (report.blocked, report.made, report.warnings)
