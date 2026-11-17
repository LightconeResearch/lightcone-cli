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
from conftest import CLUSTER_ID

from lightcone.engine import dataset, identity, worker
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
    assert dataset.untracked(root, identity.files(root)) == []
