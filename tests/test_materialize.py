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

import hashlib
import json
import subprocess
import sys
import textwrap
import threading
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from conftest import CLUSTER_ID, _cluster, _Inline, _Meanwhile

from lightcone.engine import assets, checkout, dataset, identity, plan
from lightcone.engine import materialize as engine
from lightcone.engine.plan import Task
from lightcone.engine.project import ProjectError, child_env
from lightcone.engine.worker import TaskResult

_SPEC = """
spec_version: "0.1.0"

decisions:
  method:
    options: {alpha: {}, beta: {}}

outputs:
  first:
    type: metric
    format: txt
    decisions: [method]
    recipe:
      command: echo {decisions.method} > {output}

  second:
    type: report
    format: txt
    inputs: [first]
    recipe:
      command: cat {inputs.first} > {output}

universes:
  baseline:
    decisions: {method: alpha}
"""


@pytest.fixture
def root(analysis: Callable[..., Path]) -> Path:
    return analysis(_SPEC)


def _switch_method(root: Path) -> None:
    """Have the one universe select the other option, and commit it."""
    spec = root / "astra.yaml"
    spec.write_text(spec.read_text().replace("{method: alpha}", "{method: beta}"))
    dataset.save(root, [spec], "switch method")


def _record(root: Path, name: str) -> dict[str, Any]:
    """ASTRA's record of the result called *name* in the one universe."""
    record: dict[str, Any] = json.loads(
        (root / ".astra/records/baseline" / f"{name}.json").read_text()
    )
    return record


def _commits(root: Path) -> int:
    return len(dataset._git(["log", "--oneline"], cwd=root).splitlines())


# ---- a run, end to end -----------------------------------------------------


def test_every_output_is_made_and_committed(root: Path, inline: None) -> None:
    before = _commits(root)

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.made == ["first#baseline", "second#baseline"]
    assert report.ok and not report.up_to_date
    assert (root / "results/baseline/second.txt").read_text() == "alpha\n"
    assert _commits(root) == before + 2
    assert not dataset.status(root)


def test_an_output_and_its_record_land_in_one_commit(root: Path, inline: None) -> None:
    """One commit is one complete, self-describing materialization — the
    record can never come to describe different bytes."""
    engine.materialize(root, ["first"], cluster_id=CLUSTER_ID)

    committed = dataset._git(
        ["show", "--name-only", "--format=", "HEAD"], cwd=root
    ).split()
    assert sorted(committed) == [
        ".astra/records/baseline/first.json",
        "results/baseline/first.txt",
    ]


def test_the_bytes_go_to_the_annex_and_what_describes_them_to_git(
    root: Path, inline: None
) -> None:
    """What git records is the test: a pointer for content, the real thing
    for a record. The working tree looks the same either way."""
    engine.materialize(root, ["first"], cluster_id=CLUSTER_ID)

    def blob(rel: str) -> str:
        return dataset._git(["cat-file", "-p", f"HEAD:{rel}"], cwd=root)

    assert blob("results/baseline/first.txt").startswith("/annex/objects/")
    assert blob(".astra/records/baseline/first.json").startswith("{")


def test_a_second_run_does_nothing_and_commits_nothing(root: Path, inline: None) -> None:
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    after_first = _commits(root)

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.made == []
    assert report.current == ["first#baseline", "second#baseline"]
    assert report.up_to_date
    assert _commits(root) == after_first


def test_an_output_built_once_has_no_universe_directory(
    analysis: Callable[..., Path], inline: None
) -> None:
    """Nothing about it varies between universes, so it is one result,
    named by its output alone."""
    root = analysis(_FETCH_SPEC, files={"data/catalog.fits": "stars\n"})

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.made == ["copy"]
    committed = dataset._git(["show", "--name-only", "--format=", "HEAD"], cwd=root).split()
    assert sorted(committed) == [
        ".astra/records/copy.json",
        "results/copy.txt",
    ]
    assert [o.output for o in engine.status(root).outputs] == ["copy"]


def test_a_driven_analysis_is_built_in_the_projects_universes(
    analysis: Callable[..., Path], inline: None
) -> None:
    """A universe that sets a declared analysis's decision drives it: the
    result belongs to the project, under the address it is read by, while
    its recipe runs where it was written to run — in its own folder."""
    stage = """
    spec_version: "0.1.0"
    decisions:
      dec:
        options: {x: {}, y: {}}
    outputs:
      out:
        type: metric
        format: txt
        decisions: [dec]
        recipe:
          command: echo {decisions.dec} $(basename "$PWD") > {output}
    """
    spec = """
    spec_version: "0.1.0"
    analyses:
      stage: {path: stage}
    outputs:
      report:
        type: report
        format: txt
        inputs: [stage.out]
        recipe:
          command: cat {inputs} > {output}
    universes:
      u1:
        decisions: {stage.dec: x}
    """
    root = analysis(spec, files={"stage/astra.yaml": stage})

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.made == ["stage.out#u1", "report#u1"]
    assert (root / "results/u1/stage.out.txt").read_text() == "x stage\n"
    assert (root / "results/u1/report.txt").read_text() == "x stage\n"
    assert not dataset.status(root)
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).up_to_date


# ---- behind: the environment moved, the analysis did not -------------------


def _move_the_environment(root: Path) -> None:
    """Change `env_version` for real, and commit it.

    An install setting is hashed into the environment's identity but is not
    in the lock, so `uv.lock` still matches `pyproject.toml` and both the
    driver's `uv sync --locked` and the workers' `uv run --locked` go
    through — which is what lets this test the classification rather than
    an incidental uv refusal.
    """
    pyproject = root / "pyproject.toml"
    pyproject.write_text(pyproject.read_text() + "\n[tool.uv]\nno-binary = true\n")
    dataset.save(root, [root], "an environment edit")


def test_a_moved_environment_is_reported_and_nothing_is_remade(
    root: Path, inline: None
) -> None:
    """The change the layer turns on. A rewritten environment says nothing
    about whether a result is still right, and remaking one can cost hours,
    so it is reported and left where it is."""
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    after_first = _commits(root)
    first = (root / "results/baseline/first.txt").read_text()
    _move_the_environment(root)

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.made == []
    assert set(report.behind) == {"first#baseline", "second#baseline"}
    assert "earlier environment" in report.behind["first#baseline"]
    assert report.up_to_date, "behind is not out of date"
    assert _commits(root) == after_first + 1, "only the environment edit"
    assert (root / "results/baseline/first.txt").read_text() == first


def test_refresh_remakes_what_is_behind_and_commits_it(root: Path, inline: None) -> None:
    """The other half: the report is not the only thing on offer, and asking
    is one flag."""
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    _move_the_environment(root)
    before = _commits(root)

    report = engine.materialize(root, [], refresh=True, cluster_id=CLUSTER_ID)

    assert set(report.made) == {"first#baseline", "second#baseline"}
    assert report.behind == {}
    assert _commits(root) == before + 2
    made_under = _record(root, "first")["generated_by"]["environment"]["iri"]
    assert made_under == identity.environment(root)


def test_check_reports_behind_without_planning_it(root: Path, inline: None) -> None:
    """`--check` is a gate, and `behind` must not close it — a project of
    curated results would never pass again."""
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    _move_the_environment(root)

    report = engine.check(root, [])

    assert report.planned == {}
    assert set(report.behind) == {"first#baseline", "second#baseline"}
    assert report.up_to_date


def test_check_with_refresh_plans_what_is_behind(root: Path, inline: None) -> None:
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    _move_the_environment(root)

    report = engine.check(root, [], refresh=True)

    assert set(report.planned) == {"first#baseline", "second#baseline"}
    assert report.behind == {}
    assert not report.up_to_date


def test_a_stale_output_is_stale_even_when_the_environment_also_moved(
    root: Path, inline: None
) -> None:
    """Both moved, and only one of them calls for work. Reporting `behind`
    here would say "left alone" about something the next run will remake."""
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    _move_the_environment(root)
    _switch_method(root)

    report = engine.check(root, [])

    assert "method" in report.planned["first#baseline"]
    assert "first#baseline" not in report.behind


# ---- lc status -------------------------------------------------------------


def test_status_names_the_commit_each_output_came_from(root: Path, inline: None) -> None:
    """The verb's whole reason to exist: an output that is behind is not
    wrong, and this is where the code that produced it can be read back."""
    ran_against = dataset.head(root)
    engine.materialize(root, [], cluster_id=CLUSTER_ID)

    report = engine.status(root)

    assert [o.output for o in report.outputs] == ["first#baseline", "second#baseline"]
    assert all(o.status == "current" for o in report.outputs)
    # The commit the tree was at when the run *started* — the code that
    # produced the output, not the commit the run itself went on to make.
    assert all(o.git_sha == ran_against for o in report.outputs)
    assert report.counts == {"current": 2, "behind": 0, "stale": 0}


def test_status_names_the_environment_each_output_ran_in(root: Path, inline: None) -> None:
    """lc minted the name a record gives its environment, so lc is where
    it is read: each output's beside the project's own, which is the one
    a run made now would record."""
    engine.materialize(root, [], cluster_id=CLUSTER_ID)

    report = engine.status(root)

    assert report.environment == identity.environment(root)
    assert [o.environment for o in report.outputs] == [report.environment] * 2
    assert report.as_dict()["environment"] == report.environment
    assert [o["environment"] for o in report.as_dict()["outputs"]] == [report.environment] * 2


def test_status_reports_behind_after_the_environment_moves(
    root: Path, inline: None
) -> None:
    made_at, made_in = dataset.head(root), identity.environment(root)
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    _move_the_environment(root)

    report = engine.status(root)

    assert report.counts == {"current": 0, "behind": 2, "stale": 0}
    assert all(o.git_sha == made_at for o in report.outputs), "the commit it was made at"
    assert "earlier environment" in report.outputs[0].why
    # Behind is exactly this: the record names another environment than
    # the project's own, and status shows both.
    assert report.environment == identity.environment(root) != made_in
    assert all(o.environment == made_in for o in report.outputs)


def test_status_leaves_a_never_materialized_output_without_a_commit(root: Path) -> None:
    """There is nothing to name — and an empty string rather than HEAD,
    which would claim the output came from a commit that never made it."""
    report = engine.status(root)

    assert report.counts == {"current": 0, "behind": 0, "stale": 2}
    assert all(o.git_sha == "" and o.data_version == "" for o in report.outputs)
    assert all(o.environment == "" for o in report.outputs)
    assert "no record" in report.outputs[0].why


def test_status_does_not_mind_a_dirty_tree(root: Path, inline: None) -> None:
    """It reads. Refusing here would make the one verb that tells you what
    state you are in unavailable exactly when you need it."""
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    (root / "results/baseline/first.txt").unlink()
    (root / "results/baseline/first.txt").write_text("edited by hand\n")

    assert engine.status(root).counts["current"] == 2


def test_asking_for_an_output_makes_what_it_is_made_of(root: Path, inline: None) -> None:
    report = engine.materialize(root, ["second"], cluster_id=CLUSTER_ID)

    assert report.made == ["first#baseline", "second#baseline"]


def test_a_changed_decision_remakes_the_output_and_its_dependents(
    root: Path, inline: None
) -> None:
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    _switch_method(root)

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.made == ["first#baseline", "second#baseline"]
    assert (root / "results/baseline/second.txt").read_text() == "beta\n"


def _edit_first_recipe(root: Path, command: str) -> None:
    """Rewrite how `first` is made, and commit it."""
    spec = root / "astra.yaml"
    spec.write_text(spec.read_text().replace("echo {decisions.method}", command))
    dataset.save(root, [spec], "edit the recipe")


def test_a_changed_command_remakes_the_output_and_its_dependents(
    root: Path, inline: None
) -> None:
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    _edit_first_recipe(root, "echo changed")

    assert "command" in engine.check(root, []).planned["first#baseline"]
    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.made == ["first#baseline", "second#baseline"]
    assert (root / "results/baseline/second.txt").read_text() == "changed\n"


def test_a_byte_identical_rebuild_stops_the_cascade(root: Path, inline: None) -> None:
    """What check mode cannot know, a run can: once its upstream has
    landed, the dependent's task sees that the bytes it recorded reading
    are the bytes that came out."""
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    _edit_first_recipe(root, "echo  {decisions.method}")
    assert set(engine.check(root, []).planned) == {"first#baseline", "second#baseline"}
    before = _commits(root)

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.made == ["first#baseline"]
    assert report.current == ["second#baseline"]
    assert _commits(root) == before + 1


def test_a_run_asks_astra_once_however_far_a_remake_cascades(
    root: Path, inline: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The driver reads what ASTRA's checker finds before anything runs,
    and each task settles its own result from that and its upstreams'
    bytes. A check per task would need git wherever a task runs, and
    would read a tree other tasks are still writing."""
    from astra import api

    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    _edit_first_recipe(root, "echo changed")
    asked: list[Path] = []
    status = api.status

    def counted(path: Path) -> api.StatusReport:
        asked.append(path)
        return status(path)

    monkeypatch.setattr(api, "status", counted)

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.made == ["first#baseline", "second#baseline"]
    assert asked == [root]


_SHARED = """
spec_version: "0.1.0"

inputs:
  catalog: {path: data/catalog.txt}
  mask: {path: data/mask.txt}

outputs:
  left:
    type: metric
    format: txt
    inputs: [catalog]
    recipe:
      command: cat {inputs.catalog} > {output}

  right:
    type: metric
    format: txt
    inputs: [catalog, mask]
    recipe:
      command: cat {inputs.catalog} {inputs.mask} > {output}
"""


def test_an_input_more_than_one_task_reads_is_hashed_once(
    analysis: Callable[..., Path], inline: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each task that reads a declared input would otherwise read all of
    it again to name it in its record, and a multiverse reads one catalog
    in every universe. An input one task reads is that task's to hash."""
    root = analysis(_SHARED, {"data/catalog.txt": "stars\n", "data/mask.txt": "sky\n"})
    hashed: list[str] = []
    digest_file = assets.digest_file

    def counted(path: Path) -> assets.Digest:
        hashed.append(path.name)
        return digest_file(path)

    monkeypatch.setattr(assets, "digest_file", counted)

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.ok, report.warnings
    assert (hashed.count("catalog.txt"), hashed.count("mask.txt")) == (1, 1)
    for name in ("left", "right"):
        read = json.loads((root / f".astra/records/{name}.json").read_text())["generated_by"]
        catalog = next(item for item in read["inputs"] if item["entry"] == "catalog")
        assert catalog["content_hash"] == "sha256:" + hashlib.sha256(b"stars\n").hexdigest()
        assert catalog["size"] == 6


def test_a_changed_input_remakes_what_reads_it(
    analysis: Callable[..., Path], inline: None
) -> None:
    root = analysis(_FETCH_SPEC, files={"data/catalog.fits": "stars\n"})
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    catalog = root / "data/catalog.fits"
    _forge(catalog, "galaxies\n")
    dataset.save(root, [catalog], "a new catalog")

    assert "content of" in engine.check(root, []).planned["copy"]
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).made == ["copy"]
    assert (root / "results/copy.txt").read_text() == "galaxies\n"


def test_the_previous_bytes_are_still_there_at_the_previous_commit(
    root: Path, inline: None
) -> None:
    """The property the whole layer exists for: given a commit, recover the
    exact bytes it produced."""
    engine.materialize(root, ["first"], cluster_id=CLUSTER_ID)
    original = dataset._git(["rev-parse", "HEAD"], cwd=root).strip()
    _switch_method(root)
    engine.materialize(root, ["first"], cluster_id=CLUSTER_ID)
    assert (root / "results/baseline/first.txt").read_text() == "beta\n"

    dataset._git(["checkout", original, "--", "results/baseline/first.txt"], cwd=root)

    assert (root / "results/baseline/first.txt").read_text() == "alpha\n"


# ---- check mode ------------------------------------------------------------


def test_check_says_what_would_run_and_why(root: Path) -> None:
    report = engine.check(root, [])

    assert set(report.planned) == {"first#baseline", "second#baseline"}
    assert "no record" in report.planned["first#baseline"]
    assert not report.up_to_date


def test_check_writes_nothing_and_commits_nothing(root: Path) -> None:
    before = _commits(root)

    engine.check(root, [])

    assert not (root / "results/baseline/first.txt").exists()
    assert _commits(root) == before


def test_check_cascades_through_an_output_it_already_decided_to_rebuild(
    root: Path, inline: None
) -> None:
    """Check mode cannot know whether a rebuild comes out byte-identical,
    so it assumes it will not — the one place it is deliberately more
    pessimistic than a worker."""
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    _switch_method(root)

    report = engine.check(root, [])

    assert "method" in report.planned["first#baseline"]
    assert "first#baseline" in report.planned["second#baseline"]


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
    committed, and anything under `results/` or `.astra/` is lc's to write."""
    engine.materialize(root, ["first"], cluster_id=CLUSTER_ID)
    (root / "notes.md").write_text("in progress\n")
    (root / "results/baseline/stray.txt").write_text("by hand\n")
    (root / ".astra/records/baseline/stray.json").write_text("{}\n")

    with pytest.raises(ProjectError) as raised:
        engine.materialize(root, [], cluster_id=CLUSTER_ID)

    message = str(raised.value)
    assert "commit these" in message and "notes.md" in message
    assert "discard them" in message
    assert (
        "git clean -f -- .astra/records/baseline/stray.json results/baseline/stray.txt"
        in message
    )
    assert "git restore" not in message  # nothing tracked was changed there


def test_the_discard_commands_do_what_they_say(root: Path, inline: None) -> None:
    """A remedy that fails is worse than none: each command names the very
    paths, so it runs on the tree as it is."""
    engine.materialize(root, ["first"], cluster_id=CLUSTER_ID)
    (root / "results/baseline/first.txt").unlink()
    (root / "results/baseline/stray.txt").write_text("by hand\n")

    with pytest.raises(ProjectError) as raised:
        engine.materialize(root, [], cluster_id=CLUSTER_ID)

    commands = [
        line.strip() for line in str(raised.value).splitlines() if line.strip().startswith("git ")
    ]
    assert len(commands) == 2
    for command in commands:
        subprocess.run(command, shell=True, cwd=root, check=True, env=child_env())
    assert not dataset.status(root)


@pytest.mark.parametrize("invalid", ["dirty", "spec"])
def test_project_refusals_precede_cluster_connection(
    root: Path, monkeypatch: pytest.MonkeyPatch, invalid: str,
) -> None:
    if invalid == "dirty":
        (root / "notes.md").write_text("in progress\n")
    else:
        (root / "astra.yaml").write_text(_SPEC.replace("    format: txt\n", ""))
        dataset.save(root, [root], "invalid spec")

    def unexpected(*args: object) -> None:
        pytest.fail("an invalid project contacted the cluster")

    monkeypatch.setattr(engine, "cluster_for_run", unexpected)
    with pytest.raises(ProjectError):
        engine.materialize(root, [], cluster_id=CLUSTER_ID)


def test_a_run_with_nothing_to_make_needs_no_cluster(
    analysis: Callable[..., Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Built per universe, and no universe to build it in.
    root = analysis(_SPEC[: _SPEC.index("universes:")])
    _declare_license(root)

    def unexpected(*args: object) -> None:
        pytest.fail("a run that submits nothing contacted the cluster")

    monkeypatch.setattr(engine, "cluster_for_run", unexpected)
    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.ok and report.up_to_date
    assert (root / "ro-crate-metadata.json").is_file()


def test_a_result_that_cannot_be_committed_is_reported_and_the_project_put_back(
    root: Path, inline: None
) -> None:
    """A commit git refuses loses the result, not the tree: what the
    landing staged and wrote is taken back out, so the next run starts."""
    hook = root / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\necho refused >&2\nexit 1\n")
    hook.chmod(0o755)

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.failed == ["first#baseline", "second#baseline"] and not report.made
    assert all(
        any(w.startswith(f"{name}: made, but not recorded") and "refused" in w
            for w in report.warnings)
        for name in report.failed
    )
    assert not dataset.status(root)
    assert not (root / "results/baseline/first.txt").exists()

    hook.unlink()
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok


def test_a_result_waits_for_another_git_command_to_be_done(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The researcher's own `git add` of a large file holds the index for
    as long as hashing takes. That is over in a moment, so a landing waits
    it out — refusing on it would fail whichever result arrived last."""
    lock = root / ".git/index.lock"

    class Adding(_Inline):
        def completed(self, handles: list[object]) -> Iterator[object]:
            lock.touch()
            threading.Timer(0.6, lock.unlink).start()
            yield from handles

    _cluster(monkeypatch, Adding())

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.ok and report.made == ["first#baseline", "second#baseline"]
    assert not dataset.status(root)


def test_an_index_lock_that_does_not_go_away_refuses_before_anything_runs(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A git that was killed leaves it for good, and every commit after
    that fails: for a run, every result, once its recipe has run."""
    monkeypatch.setattr(dataset, "_PATIENCE", 0.3)
    (root / ".git/index.lock").touch()

    with pytest.raises(ProjectError, match="holds the project's index"):
        engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert not (root / ".lightcone/checkouts").exists()


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


def test_machine_level_uv_config_is_reported(
    root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The config-file half of the same hole the ambient scrub closes:
    env_version cannot see it, so the run says so."""
    from lightcone.engine import identity

    user = tmp_path / "user-uv.toml"
    user.write_text("no-binary = true\n")
    monkeypatch.setattr(identity, "_machine_config_paths", lambda: (user,))

    report = engine.check(root, [])

    assert any("env_version cannot see it" in w and str(user) in w for w in report.warnings)


def test_ambient_uv_settings_are_scrubbed_and_reported(
    root: Path, inline: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scrub protects env_version's install-settings term; the warning
    is what tells a user why their variable stopped steering the sync."""
    monkeypatch.setenv("UV_NO_BINARY", "1")

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.ok
    assert any("UV_NO_BINARY" in w for w in report.warnings)


def test_an_edit_while_the_graph_runs_is_no_part_of_the_run(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recipes run in the run's own checkout of the commit, so what is
    edited in the project meanwhile changes nothing they see and nothing
    a record says — and stays exactly where the researcher left it."""
    start = dataset.head(root)

    def edit() -> None:
        spec = root / "astra.yaml"
        spec.write_text(spec.read_text().replace("echo {decisions.method}", "echo edited"))
        (root / "notes.md").write_text("scribbled while the graph ran\n")

    _cluster(monkeypatch, _Meanwhile(edit))

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.ok and report.made == ["first#baseline", "second#baseline"]
    assert (root / "results/baseline/second.txt").read_text() == "alpha\n"  # the commit's recipe
    assert _record(root, "first")["generated_by"]["commit"] == start
    assert dataset.status(root) == [(" M", "astra.yaml"), ("??", "notes.md")]


def test_a_commit_while_the_graph_runs_is_built_on(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Results land on top of whatever the researcher committed meanwhile,
    each record naming the commit its recipe ran on."""
    start = dataset.head(root)

    def commit() -> None:
        (root / "notes.md").write_text("committed while the graph ran\n")
        dataset.save(root, ["notes.md"], "notes")

    _cluster(monkeypatch, _Meanwhile(commit))

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.ok and report.made == ["first#baseline", "second#baseline"]
    assert _record(root, "second")["generated_by"]["commit"] == start
    subjects = dataset._git(["log", "--format=%s", "-3"], cwd=root).splitlines()
    assert subjects == [
        "[DATALAD RUNCMD] second#baseline", "[DATALAD RUNCMD] first#baseline", "notes",
    ]
    assert not dataset.status(root)
    assert engine.status(root).counts["current"] == 2


def test_a_project_that_changes_while_a_run_is_prepared_is_refused(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ASTRA reads the working tree and names its commit; the two are one
    thing only while nothing is uncommitted. An edit between that read and
    the checkout would have the run follow a plan its commit does not hold."""

    class Editing(_Inline):
        def validate(self, tasks: Iterable[Task]) -> dict[str, dict[str, float]]:
            (root / "notes.md").write_text("scribbled while lc was reading\n")
            return super().validate(tasks)

    _cluster(monkeypatch, Editing())

    with pytest.raises(ProjectError, match="changed while lc was preparing"):
        engine.materialize(root, [], cluster_id=CLUSTER_ID)
    assert not (root / "results/baseline/first.txt").exists()


def test_a_mid_run_stage_is_not_swept_into_lcs_commits(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Work the researcher staged while the graph ran ends the run exactly
    where they left it: staged, and in none of lc's commits —
    `dataset.save` stages scoped and commits scoped."""

    def stage() -> None:
        (root / "notes.py").write_text("draft = True\n")
        dataset._git(["add", "--", "notes.py"], cwd=root)

    _cluster(monkeypatch, _Meanwhile(stage))

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.ok and report.made == ["first#baseline", "second#baseline"]
    staged = dataset._git(["diff", "--cached", "--name-only"], cwd=root).split()
    assert staged == ["notes.py"]
    ever_committed = dataset._git(["log", "--name-only", "--format="], cwd=root).split()
    assert "notes.py" not in ever_committed


def test_the_crate_waits_for_a_tree_that_is_the_commit(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The crate is read off the working tree, so one being edited would
    publish the edit: it is left for the next run, and said so."""
    _declare_license(root)

    def edit() -> None:
        (root / "notes.md").write_text("in progress\n")

    _cluster(monkeypatch, _Meanwhile(edit))

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.ok
    assert any("RO-Crate" in w and "uncommitted changes" in w for w in report.warnings)
    assert not (root / "ro-crate-metadata.json").exists()


def test_the_crate_reads_the_tree_while_no_other_run_is_landing(
    root: Path, inline: None
) -> None:
    """Another run's result, half landed, would read as an edit, and the
    crate would be left for the next run though the tree is a commit's."""
    _declare_license(root)
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok
    report = engine.MaterializeReport()
    converge = threading.Thread(
        target=engine._converge_crate,
        args=(root, report, plan.build(root), dataset.dataset_id(root)),
    )
    half_landed = root / "results/baseline/third.txt"

    with checkout.landing(root):
        half_landed.write_text("another run's\n")
        converge.start()
        converge.join(timeout=0.5)
        assert converge.is_alive()
        half_landed.unlink()
    converge.join(timeout=60)

    assert not converge.is_alive()
    assert not any("uncommitted changes" in w for w in report.warnings)


def test_a_result_made_while_the_project_is_on_another_branch_waits_for_it(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A result lands on the branch its run started from. While the
    project is elsewhere it waits, and lands with the next one to arrive."""
    home = dataset.branch(root)

    class Wandering(_Inline):
        def completed(self, handles: list[object]) -> Iterator[object]:
            dataset._git(["checkout", "-q", "-b", "elsewhere"], cwd=root)
            yield handles[0]
            dataset._git(["checkout", "-q", home.removeprefix("refs/heads/")], cwd=root)
            yield handles[1]

    _cluster(monkeypatch, Wandering())

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.ok and sorted(report.made) == ["first#baseline", "second#baseline"]
    assert not dataset._git(["log", "--format=%s", "elsewhere", f"^{home}"], cwd=root).strip()
    assert engine.status(root).counts["current"] == 2


def test_a_result_that_never_gets_to_land_is_reported(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Leaving(_Inline):
        def completed(self, handles: list[object]) -> Iterator[object]:
            dataset._git(["checkout", "-q", "-b", "elsewhere"], cwd=root)
            yield from handles

    _cluster(monkeypatch, Leaving())

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.failed == ["first#baseline", "second#baseline"] and not report.made
    unrecorded = [w for w in report.warnings if "made, but not recorded" in w]
    assert len(unrecorded) == 2 and all("no longer on" in w for w in unrecorded)
    assert not dataset.status(root)


def test_a_run_that_finishes_last_never_replaces_a_newer_result(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recipe is changed and committed while a run is in flight, and a
    run of that commit lands first: the older run's result is reported,
    and the newer one stays."""
    newer: list[str] = []

    class Overtaken(_Inline):
        def completed(self, handles: list[object]) -> Iterator[object]:
            spec = root / "astra.yaml"
            spec.write_text(spec.read_text().replace("echo {decisions.method}", "echo newer"))
            dataset.save(root, [spec], "a newer recipe")
            newer.append(dataset.head(root))
            with monkeypatch.context() as later:
                _cluster(later, _Inline())
                assert engine.materialize(root, ["first"], cluster_id=CLUSTER_ID).ok
            yield from handles

    _cluster(monkeypatch, Overtaken())

    report = engine.materialize(root, ["first"], cluster_id=CLUSTER_ID)

    assert report.failed == ["first#baseline"] and not report.made
    assert any("newer than what this run made" in w for w in report.warnings)
    assert (root / "results/baseline/first.txt").read_text() == "newer\n"
    assert _record(root, "first")["generated_by"]["commit"] == newer[0]
    assert not dataset.status(root)


def test_a_clean_run_records_everything_it_made(root: Path, inline: None) -> None:
    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.ok
    assert report.made == ["first#baseline", "second#baseline"]


# ---- leaving the tree as clean as it was found -----------------------------


def test_a_failing_recipe_commits_nothing_and_leaves_the_tree_clean(
    analysis: Callable[..., Path], inline: None
) -> None:
    """The invariant that makes the dirty-tree refusal survivable: the next
    run must not tell the user to commit truncated, record-less
    garbage."""
    root = analysis(_SPEC.replace("echo {decisions.method} > {output}", "exit 1"))
    before = _commits(root)

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.failed == ["first#baseline"]
    assert report.blocked == ["second#baseline"]
    assert not report.ok
    assert _commits(root) == before
    assert not dataset.status(root)


def test_a_run_in_which_everything_failed_is_not_up_to_date(
    analysis: Callable[..., Path], inline: None
) -> None:
    """`made` stays empty when every recipe fails, so `up_to_date` alone
    read "nothing to do" over a list of failures — and it is the second key
    of the JSON report, which is what an agent branches on."""
    root = analysis(_SPEC.replace("echo {decisions.method} > {output}", "exit 1"))

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.made == []
    assert not report.up_to_date
    assert json.loads(json.dumps(report.as_dict()))["up_to_date"] is False
    # The positive control is `test_a_second_run_does_nothing_and_commits_
    # nothing`, where the same empty `made` does mean up to date.


def test_a_rebuild_that_fails_leaves_the_committed_output_in_the_project(
    root: Path, inline: None
) -> None:
    engine.materialize(root, ["first"], cluster_id=CLUSTER_ID)
    (root / "astra.yaml").write_text(
        _SPEC.replace("echo {decisions.method} > {output}", "exit 1")
    )
    dataset.save(root, [root], "break the recipe")
    at_break = _commits(root)

    report = engine.materialize(root, ["first"], cluster_id=CLUSTER_ID)

    assert report.failed == ["first#baseline"]
    assert (root / "results/baseline/first.txt").read_text() == "alpha\n"
    assert _commits(root) == at_break
    assert not dataset.status(root)


def test_an_interrupted_run_leaves_the_project_clean_and_keeps_its_checkout(
    root: Path, inline: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sibling that already landed keeps its commit. The result still in
    flight may still have a writer, so the checkout it writes in is kept
    out of use, and the project's own tree never held any of it."""
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    (root / "astra.yaml").write_text(_SPEC.replace("echo {decisions.method}", "echo changed"))
    dataset.save(root, [root], "edit both recipes")

    class _Interrupted(_Inline):
        def completed(self, handles: list[Any]) -> Iterator[TaskResult]:
            yield handles[0]
            raise KeyboardInterrupt

    _cluster(monkeypatch, _Interrupted())

    with pytest.raises(KeyboardInterrupt):
        engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert not dataset.status(root)
    assert (root / "results/baseline/first.txt").read_text() == "changed\n"  # landed
    assert (root / "results/baseline/second.txt").read_text() == "alpha\n"  # as committed
    held = root / ".lightcone/checkouts"
    assert (held / "0.run").read_text() == CLUSTER_ID
    assert (held / "0/results/baseline/second.txt").read_text() == "changed\n"

    # The next run does not wait for it, and does not write where it may.
    _cluster(monkeypatch, _Inline())
    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.ok and report.made == ["second#baseline"]
    assert (held / "0.run").exists() and not (held / "1.run").exists()
    assert (held / "1").is_dir()


def test_a_checkout_is_kept_and_taken_again(root: Path, inline: None) -> None:
    """One checkout serves run after run: moved to each new commit, given
    back when the run's tasks have all reported."""
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    _switch_method(root)

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.ok
    held = root / ".lightcone/checkouts"
    assert sorted(p.name for p in held.iterdir()) == ["0", "pyproject.toml", "uv.toml"]
    assert dataset._git(["rev-parse", "HEAD"], cwd=held / "0").strip() != dataset.head(root)
    assert not dataset.status(root)


def test_a_checkout_whose_allocation_ended_is_taken_again(
    root: Path, inline: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one fact that proves no recipe is left writing in it."""
    from lightcone.engine import compute

    held = root / ".lightcone/checkouts"
    held.mkdir(parents=True)
    (held / "0.run").write_text("clu_of_an_earlier_run")
    monkeypatch.setattr(compute, "ended", lambda allocation: allocation == "clu_of_an_earlier_run")

    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok

    assert (held / "0").is_dir() and not (held / "1").exists()
    assert not (held / "0.run").exists()


def test_a_landed_result_is_one_copy_and_cannot_be_written_in_place(
    root: Path, inline: None
) -> None:
    """The project's file is the stored object itself, as a thin add
    leaves it — and read-only, so nothing can change the bytes a record
    names by opening the file for update."""
    engine.materialize(root, ["first"], cluster_id=CLUSTER_ID)

    landed = root / "results/baseline/first.txt"
    key = dataset._git(["annex", "lookupkey", "results/baseline/first.txt"], cwd=root).strip()
    stored = root / dataset._git(["annex", "contentlocation", key], cwd=root).strip()
    assert landed.samefile(stored)
    with pytest.raises(PermissionError):
        landed.open("a")
    assert not dataset.status(root)


# ---- an analysis declared beside the project ---------------------------------


def _beside(analysis: Callable[..., Path], tmp_path: Path) -> Path:
    """A project below the top of its repository that declares the
    analysis in the folder next to it."""
    built = analysis(
        """
        spec_version: "0.1.0"
        analyses:
          shared: {path: ../shared}
        universes:
          shared_v1: {analysis: shared, universe: v1}
        outputs:
          report:
            type: report
            format: txt
            inputs: ["shared.fit#shared_v1"]
            recipe:
              command: cat {inputs} > {output}
        """
    )
    outer = tmp_path / "outer"
    (outer / "shared").mkdir(parents=True)
    for name in (".git", ".gitattributes"):
        (built / name).rename(outer / name)
    root = built.rename(outer / "project")
    (outer / "shared/astra.yaml").write_text(
        textwrap.dedent(
            """
            spec_version: "0.1.0"
            decisions:
              m:
                options: {x: {}}
            outputs:
              fit:
                type: metric
                format: txt
                decisions: [m]
                recipe:
                  command: echo committed {decisions.m} > {output}
            universes:
              v1: {decisions: {m: x}}
            """
        )
    )
    dataset._git(["add", "-A", "."], cwd=outer)
    dataset._git(["commit", "-q", "-m", "move the project below the top"], cwd=outer)
    return root


def test_an_analysis_declared_beside_the_project_is_built_from_the_commit(
    analysis: Callable[..., Path], tmp_path: Path, inline: None
) -> None:
    root = _beside(analysis, tmp_path)

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.ok and sorted(report.made) == ["report", "shared:fit#v1"]
    assert (root / "results/report.txt").read_text() == "committed x\n"
    assert not dataset._git(["status", "--porcelain"], cwd=root.parent).strip()


def test_an_uncommitted_change_in_an_analysis_declared_beside_the_project_refuses(
    analysis: Callable[..., Path], tmp_path: Path, inline: None
) -> None:
    """ASTRA reads every analysis of the stack in the working tree, so the
    tree that has to be a commit's reaches as far as the stack does: an
    edit there would be in the plan, and in no commit a record could name."""
    root = _beside(analysis, tmp_path)
    spec = root.parent / "shared/astra.yaml"
    spec.write_text(spec.read_text().replace("echo committed", "echo UNCOMMITTED"))

    with pytest.raises(ProjectError, match="uncommitted changes") as raised:
        engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert "../shared/astra.yaml" in str(raised.value)
    assert not (root.parent / "shared/results").exists()


def test_an_analysis_declared_beside_the_project_runs_its_own_code(
    analysis: Callable[..., Path], tmp_path: Path, inline: None
) -> None:
    """Its recipe runs in its own folder of the run's checkout, outside the
    project root the boundary grants — and the code it runs is there."""
    root = _beside(analysis, tmp_path)
    shared = root.parent / "shared"
    (shared / "fit.sh").write_text('echo committed "$1"\n')
    spec = shared / "astra.yaml"
    spec.write_text(spec.read_text().replace("echo committed", "bash fit.sh"))
    dataset.save(root.parent, ["shared/astra.yaml", "shared/fit.sh"], "a script of its own")

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.ok, report.warnings
    assert (root / "results/report.txt").read_text() == "committed x\n"


# ---- results git would not store as lc does ---------------------------------


def _rules(root: Path, name: str, rewrite: Callable[[str], str]) -> None:
    path = root / name
    path.write_text(rewrite(path.read_text()))
    dataset.save(root, [path], f"rewrite {name}")


def test_a_result_no_line_sends_to_the_annex_is_refused(root: Path, inline: None) -> None:
    """Committed anyway, its bytes would be in git itself, in every clone,
    for good — and the run would have reported it made."""
    _rules(
        root,
        ".gitattributes",
        lambda text: "\n".join(line for line in text.splitlines() if "results/" not in line) + "\n",
    )

    with pytest.raises(ProjectError, match="committed to git itself") as raised:
        engine.materialize(root, [], cluster_id=CLUSTER_ID)
    assert "results/baseline/first.txt" in str(raised.value)
    assert "lc init" in str(raised.value)


def test_a_result_git_would_not_hand_to_the_annex_filter_is_refused(
    root: Path, inline: None
) -> None:
    """`annex.largefiles` says what git-annex keeps of what git hands it.
    A later line that unsets the filter hands it nothing, and git stores
    the bytes itself."""
    _rules(root, ".gitattributes", lambda text: text + "results/** -filter\n")

    with pytest.raises(ProjectError, match="does not set it to annex") as raised:
        engine.materialize(root, [], cluster_id=CLUSTER_ID)
    assert "results/baseline/first.txt" in str(raised.value)
    assert "RUNCMD" not in dataset._git(["log", "--format=%s"], cwd=root)


def test_a_result_git_ignores_is_refused(root: Path, inline: None) -> None:
    """`git add` skips an ignored path without a word."""
    _rules(root, ".gitignore", lambda text: text + "results/baseline/second.*\n")

    with pytest.raises(ProjectError, match="results/baseline/second.txt is git-ignored"):
        engine.materialize(root, [], cluster_id=CLUSTER_ID)


def test_an_ignored_result_is_named_as_it_is_on_disk(
    analysis: Callable[..., Path], inline: None
) -> None:
    """A space or an accent in the folder of an analysis: git quotes such
    a path unless asked not to, and a refusal has to name the file."""
    stage = """
    spec_version: "0.1.0"
    decisions:
      depth:
        options: {deep: {}, shallow: {}}
    universes:
      v: {decisions: {depth: deep}}
    outputs:
      mask:
        type: data
        format: txt
        decisions: [depth]
        recipe:
          command: echo {decisions.depth} > {output}
    """
    spec = """
    spec_version: "0.1.0"
    analyses:
      stage: {path: "étape une"}
    outputs:
      report:
        type: report
        format: txt
        inputs: ["stage.mask#v"]
        recipe:
          command: cat "{inputs}" > {output}
    """
    root = analysis(spec, files={"étape une/astra.yaml": stage})
    _rules(root, ".gitignore", lambda text: text + "étape une/results/\n")

    with pytest.raises(ProjectError) as raised:
        engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert "étape une/results/v/mask.txt is git-ignored" in str(raised.value)


def test_a_universe_may_be_called_results(analysis: Callable[..., Path], inline: None) -> None:
    """Its records are at `.astra/records/results/`, which a line meant for
    the results of a nested analysis would annex: unreadable then on a
    clone that holds no content."""
    root = analysis(_SPEC.replace("  baseline:", "  results:"))

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.ok
    blob = dataset._git(["cat-file", "-p", "HEAD:.astra/records/results/second.json"], cwd=root)
    assert blob.startswith("{")


def test_a_record_a_line_would_annex_is_refused(
    analysis: Callable[..., Path], inline: None
) -> None:
    root = analysis(_SPEC.replace("  baseline:", "  results:"))
    _rules(
        root,
        ".gitattributes",
        lambda text: "\n".join(line for line in text.splitlines() if ".astra/" not in line) + "\n",
    )

    with pytest.raises(ProjectError, match="a record has to stay in git"):
        engine.materialize(root, [], cluster_id=CLUSTER_ID)


# ---- the commit message ----------------------------------------------------


def test_the_run_record_is_what_datalad_reads(
    root: Path, inline: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asserted through datalad's own parser rather than against our JSON:
    it matches with a regex and returns nothing on any mismatch, after
    which `rerun` reports "no command; skipping" and exits 0 — so a golden
    test on the text would stay green through a silent break."""
    from datalad.api import Dataset
    from datalad.local.rerun import get_run_info

    monkeypatch.setattr(engine.worker, "lc_version", lambda: "1.2.3")
    engine.materialize(root, ["second"], cluster_id=CLUSTER_ID)
    message = dataset._git(["log", "-1", "--format=%B"], cwd=root)

    subject, info = get_run_info(Dataset(str(root)), message)

    assert subject == "second#baseline"
    assert info is not None
    # Full-string, not endswith: a flag appearing or vanishing here must
    # fail this test, not slip past a suffix match.
    assert info["cmd"] == (
        "uv run --no-project --with 'lightcone-cli==1.2.3' -- "
        "python -m lightcone.engine.worker 'second#baseline'"
    )
    assert info["inputs"] == ["results/baseline/first.txt"]
    assert info["outputs"] == [
        "results/baseline/second.txt",
        ".astra/records/baseline/second.json",
    ]
    assert info["dsid"] == "4b7b5c1e-0000-4000-8000-000000000000"
    assert info["chain"] == [] and info["pwd"] == "."


def test_the_engine_pin_follows_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """A release resolves from an index by version; a dev build cannot,
    but hatch-vcs embeds its source commit, so the pin becomes that commit
    at the engine's own repository — read from the engine's metadata, not
    a constant. Dirty or clean, the commit is the last one; the version's
    dirty marker is what says which."""
    monkeypatch.setattr(engine.worker, "lc_version", lambda: "1.2.3")
    assert engine._engine_requirement() == "lightcone-cli==1.2.3"

    url = engine._repository_url()
    assert url.startswith("https://")
    monkeypatch.setattr(engine.worker, "lc_version", lambda: "1.3.dev2+g19986bb8")
    assert engine._engine_requirement() == f"lightcone-cli @ git+{url}@19986bb8"

    monkeypatch.setattr(
        engine.worker, "lc_version", lambda: "1.3.dev2+g19986bb8.d20260820"
    )
    assert engine._engine_requirement() == f"lightcone-cli @ git+{url}@19986bb8"


def test_the_record_names_the_declared_input_not_the_annex_object(
    analysis: Callable[..., Path], inline: None
) -> None:
    """A declared input is annexed, so its bytes live under
    `.git/annex/objects/SHA256E-…` — the storage rather than the input, and
    something no one can `datalad get`."""
    root = analysis(_FETCH_SPEC, files={"data/catalog.fits": "measured\n"})

    engine.materialize(root, [], cluster_id=CLUSTER_ID)

    from datalad.api import Dataset
    from datalad.local.rerun import get_run_info

    _, info = get_run_info(Dataset(str(root)), dataset._git(["log", "-1", "--format=%B"], cwd=root))
    assert info is not None
    assert info["inputs"] == ["data/catalog.fits"]


def test_every_record_of_one_run_names_the_commit_it_started_from(
    root: Path, inline: None
) -> None:
    """The driver commits each output as it lands, so HEAD moves during the
    run — and reading it per task would stamp later records with a commit
    this same run created, nondeterministically."""
    started_at = dataset.head(root)

    engine.materialize(root, [], cluster_id=CLUSTER_ID)

    for name in ("first", "second"):
        assert _record(root, name)["generated_by"]["commit"] == started_at


def test_a_record_says_what_was_made_from_what_and_by_whom(root: Path, inline: None) -> None:
    """The record is ASTRA's, and it is what every later check compares the
    project against — so what the run read is the upstream's own bytes, and
    the environment is the one `behind` is measured from."""
    engine.materialize(root, [], cluster_id=CLUSTER_ID)

    first, second = _record(root, "first"), _record(root, "second")

    assert second["address"] == "second#baseline"
    assert second["generated_by"]["inputs"] == [
        {"entry": "first", "content_hash": first["content_hash"], "size": first["size"]}
    ]
    assert second["generated_by"]["environment"] == {"iri": identity.environment(root)}
    assert second["generated_by"]["runner"]["id"] == "lightcone-cli"


def test_check_agrees_with_a_run_on_a_clone_with_no_annex_content(
    root: Path, inline: None, tmp_path: Path
) -> None:
    """Records are in git, so an output whose bytes were never fetched
    is still classifiable — check mode reads the recorded digest rather
    than the pointer file sitting in its place."""
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    clone = _clone(root, tmp_path)
    pointer = (clone / "results/baseline/first.txt").read_text()
    assert pointer.startswith("/annex/objects/")  # content really is absent

    assert engine.check(clone, []).planned == {}


def _clone(root: Path, into: Path) -> Path:
    """Clone *root* into a usable annexed repository, as a colleague would.

    The identity is set because `clone` does not copy one and
    `annex init` makes a commit — so without it this fails on any host
    with no usable global identity, CI included. No annex content is
    fetched: a fresh clone holds pointers, and that is the state these
    tests are about.
    """
    clone = into / "clone"
    dataset._git(["clone", "-q", str(root), str(clone)], cwd=into)
    for key, value in (("user.email", "t@example.com"), ("user.name", "Test")):
        dataset._git(["config", key, value], cwd=clone)
    dataset._git(["annex", "init", "-q", "clone"], cwd=clone)
    return clone


_FETCH_SPEC = """
spec_version: "0.1.0"

inputs:
  catalog:
    path: data/catalog.fits

outputs:
  copy:
    type: metric
    format: txt
    inputs: [catalog]
    recipe:
      command: cat {inputs.catalog} > {output}
"""


_CHAIN = """
spec_version: "0.1.0"

inputs:
  catalog: {path: "data/my catalog.txt"}

outputs:
  first:
    type: data
    format: txt
    inputs: [catalog]
    recipe:
      command: sort "{inputs.catalog}" > {output}

  second:
    type: data
    format: txt
    inputs: [first]
    recipe:
      command: head -1 {inputs.first} > {output}

  third:
    type: report
    format: txt
    inputs: [second]
    recipe:
      command: cat {inputs.second} > {output}
"""


def test_a_result_left_standing_by_an_identical_rebuild_is_still_read(
    analysis: Callable[..., Path], inline: None
) -> None:
    """`second` may run only because `first` may, and is left alone when
    `first` comes out byte-identical. `third`, which does run, reads the
    bytes `second` already had — which the run's checkout has to hold."""
    root = analysis(_CHAIN, {"data/my catalog.txt": "2\n1\n"})
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok
    spec = root / "astra.yaml"
    spec.write_text(
        spec.read_text()
        .replace('sort "{inputs.catalog}"', 'sort  "{inputs.catalog}"')
        .replace("cat {inputs.second}", "cat -u {inputs.second}")
    )
    dataset.save(root, [spec], "the same bytes upstream, another recipe downstream")

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.ok, report.warnings
    assert (report.made, report.current) == (["first", "third"], ["second"])
    assert (root / "results/third.txt").read_text() == "1\n"
    assert not dataset.status(root)


def test_a_path_git_would_quote_is_a_path_like_any_other(
    analysis: Callable[..., Path], inline: None
) -> None:
    """A space or an accent in an input's name: git quotes such a path
    unless asked not to, and a quoted one equals no path on disk."""
    root = analysis(
        _CHAIN.replace("my catalog.txt", "données du relevé.txt"),
        {"data/données du relevé.txt": "2\n1\n"},
    )

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.ok, report.warnings
    assert report.made == ["first", "second", "third"]
    assert not dataset.status(root)


def test_a_bytes_free_clone_that_is_up_to_date_fetches_nothing(
    analysis: Callable[..., Path], inline: None, tmp_path: Path
) -> None:
    """A run fetches what its recipes read. With nothing to make, a clone
    holding only pointers is up to date as it is."""
    root = analysis(_FETCH_SPEC, files={"data/catalog.fits": "stars\n"})
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok

    clone = _clone(root, tmp_path)
    again = engine.materialize(clone, [], cluster_id=CLUSTER_ID)

    assert again.up_to_date, (again.failed, again.warnings)
    with pytest.raises(assets.ContentNotFetchedError):
        assets.require_fetched(clone / "data" / "catalog.fits")
    assert not dataset.status(clone)


def test_a_bytes_free_clone_fetches_what_its_recipes_read(
    root: Path, inline: None, tmp_path: Path
) -> None:
    """lc fetches rather than telling anyone to — a declared input, and a
    result left alone that a remade one reads. Handed the pointer in its
    place, the recipe would read a line of text for its data, under a
    record naming the real bytes."""
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    clone = _clone(root, tmp_path)
    spec = clone / "astra.yaml"
    spec.write_text(spec.read_text().replace("cat {inputs.first}", "cat -u {inputs.first}"))
    dataset.save(clone, [spec], "edit the downstream recipe")

    report = engine.materialize(clone, [], cluster_id=CLUSTER_ID)

    assert report.ok and report.made == ["second#baseline"]
    assert report.current == ["first#baseline"]
    assert (clone / "results/baseline/second.txt").read_text() == "alpha\n"
    read = _record(clone, "second")["generated_by"]["inputs"]
    assert read == [{"entry": "first", **dict(zip(
        ("content_hash", "size"), assets.digest_file(clone / "results/baseline/first.txt"),
        strict=True,
    ))}]


def test_an_unreachable_input_is_a_warning_and_a_per_task_failure(
    analysis: Callable[..., Path], inline: None, tmp_path: Path
) -> None:
    """A failed fetch must not refuse the whole run — independent tasks
    still run, and the task whose input is unreachable reports its own
    failure. Reaching the state honestly: clone, delete the origin the
    annex would fetch from, and give the recipe a reason to run again."""
    root = analysis(_FETCH_SPEC, files={"data/catalog.fits": "stars\n"})
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok
    clone = _clone(root, tmp_path)
    dataset._git(["remote", "remove", "origin"], cwd=clone)
    spec = clone / "astra.yaml"
    spec.write_text(spec.read_text().replace("cat {inputs.catalog}", "cat -u {inputs.catalog}"))
    dataset.save(clone, [spec], "edit the recipe")

    report = engine.materialize(clone, [], cluster_id=CLUSTER_ID)

    assert not report.ok
    assert report.failed == ["copy"]
    assert any("could not be fetched" in w for w in report.warnings)


def test_check_mode_never_fetches(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--check` and `status` are read-only verbs: an unfetched input is a
    reported fact there, never a network transfer."""

    def refuse(*args: Any) -> None:
        raise AssertionError("check mode fetched")

    monkeypatch.setattr(engine.checkout, "fill", refuse)
    engine.check(root, [])
    engine.status(root)


def test_a_drifted_environment_is_made_to_match_before_anything_runs(
    root: Path, inline: None
) -> None:
    """Workers pass `--no-sync`, so this is the only place on a run's path
    where an environment is made to match the commit's lock (a rerun's
    worker entry point syncs for itself): the one in the run's checkout,
    which is the one its recipes import from."""
    pyproject = root / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text().replace("dependencies = []", 'dependencies = ["idna"]')
    )
    from lightcone.engine import project as project_mod

    project_mod._run(["uv", "lock", "-q", "--project", str(root)], cwd=root)
    dataset.save(root, [root], "add a dependency without syncing")
    assert not project_mod._env_is_current(root)

    report = engine.materialize(root, ["first"], cluster_id=CLUSTER_ID)

    assert report.ok
    # The run's environment is its checkout's; the project's own is the
    # researcher's, and is left as it was.
    assert project_mod._env_is_current(root / ".lightcone/checkouts/0")
    assert not project_mod._env_is_current(root)


def test_the_recorded_command_reproduces_the_output(
    root: Path,
    inline: None,
    engine_dist: tuple[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The claim the record makes, run literally. `datalad rerun` removes
    the output, executes the recorded command, and commits what came
    back — so the record is regenerated inside the same commit, by the
    pinned engine resolved into an ephemeral environment."""
    pytest.importorskip("datalad")
    version, dist = engine_dist
    # The requirement seam, not `lc_version`: a git pin can only ever
    # build committed code, so the suite pins the wheel built from the
    # working tree — the code actually under test.
    monkeypatch.setattr(engine, "_engine_requirement", lambda: f"lightcone-cli=={version}")
    engine.materialize(root, ["first"], cluster_id=CLUSTER_ID)
    original = _record(root, "first")

    proc = subprocess.run(
        [sys.executable, "-c", "from datalad.api import rerun; rerun('HEAD')"],
        cwd=root,
        capture_output=True,
        text=True,
        env={**child_env(), "UV_FIND_LINKS": str(dist)},
    )

    assert proc.returncode == 0, proc.stderr
    assert _record(root, "first")["content_hash"] == original["content_hash"]
    assert (root / "results/baseline/first.txt").read_text() == "alpha\n"
    assert not dataset.status(root)


def test_the_recorded_command_holds_on_a_fresh_clone(
    root: Path,
    inline: None,
    tmp_path: Path,
    engine_dist: tuple[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clone checks out the lock but never `.venv`, and `uv run --no-sync`
    against a missing environment silently creates an *empty* one — so the
    record only reproduces the output because the worker converges the
    environment for itself. The in-place rerun above cannot catch a missing
    sync; this is the test that does."""
    pytest.importorskip("datalad")
    version, dist = engine_dist
    monkeypatch.setattr(engine, "_engine_requirement", lambda: f"lightcone-cli=={version}")
    engine.materialize(root, ["first"], cluster_id=CLUSTER_ID)
    original = _record(root, "first")

    clone = _clone(root, tmp_path)
    assert not (clone / ".venv").exists()

    proc = subprocess.run(
        [sys.executable, "-c", "from datalad.api import rerun; rerun('HEAD')"],
        cwd=clone,
        capture_output=True,
        text=True,
        env={**child_env(), "UV_FIND_LINKS": str(dist)},
    )

    assert proc.returncode == 0, proc.stderr
    assert _record(clone, "first")["content_hash"] == original["content_hash"]
    assert (clone / ".venv").exists()


# ---- the scheduler seam ----------------------------------------------------


def _resource_cluster(
    monkeypatch: pytest.MonkeyPatch, *, workers: int = 1, gpus: int = 0,
) -> None:
    from distributed import Client, LocalCluster

    from lightcone.engine import compute

    @contextmanager
    def connect(cluster_id: str) -> Iterator[Any]:
        with LocalCluster(
            n_workers=workers, threads_per_worker=4, processes=False,
            dashboard_address=None, resources={"CPU": 4, "MEMORY": 2 * 1024**3, "GPU": gpus},
        ) as cluster, Client(cluster, set_as_default=False) as client:
            yield client

    monkeypatch.setattr(compute, "connect", connect)


@pytest.mark.parametrize("resource_spec", ["cpus: 5", "memory: 3Gi"])
def test_resource_refusal_precedes_preparation_and_all_recipes(
    analysis: Callable[..., Path], monkeypatch: pytest.MonkeyPatch, resource_spec: str,
) -> None:
    spec = _SPEC.replace(
        "command: cat", f"resources: {{{resource_spec}}}\n      command: cat"
    )
    root = analysis(spec)
    before = dataset.head(root)
    _resource_cluster(monkeypatch)

    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("preparation began before all resource requests were validated")

    monkeypatch.setattr(engine.checkout, "fill", unexpected)
    monkeypatch.setattr(engine.container, "runtime_for_run", unexpected)
    with pytest.raises(ProjectError, match="second#baseline:.*no worker"):
        engine.materialize(root, [], cluster_id=CLUSTER_ID)
    assert dataset.head(root) == before
    assert not dataset.status(root)
    assert not (root / "results/baseline/first.txt").exists()


@pytest.mark.parametrize("resource_spec", [
    "gpus: 1", "disk: 1Gi", "cpus: 0.5", "cpus: 0", "time_limit: 1s",
])
def test_execution_only_resources_do_not_block_read_only_commands(
    analysis: Callable[..., Path], monkeypatch: pytest.MonkeyPatch, resource_spec: str,
) -> None:
    spec = _SPEC.replace(
        "command: cat", f"resources: {{{resource_spec}}}\n      command: cat",
    )
    root = analysis(spec)
    assert len(engine.status(root).outputs) == 2
    assert set(engine.check(root, []).planned) == {"first#baseline", "second#baseline"}

    _resource_cluster(monkeypatch)

    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("preparation began before execution requirements were validated")

    monkeypatch.setattr(engine.checkout, "fill", unexpected)
    with pytest.raises(ProjectError, match="second#baseline"):
        engine.materialize(root, [], cluster_id=CLUSTER_ID)
    assert not dataset.status(root)


@pytest.mark.parametrize("runtime_name", ["docker", "podman"])
def test_gpu_runtime_refusal_precedes_image_build_and_all_recipes(
    analysis: Callable[..., Path], monkeypatch: pytest.MonkeyPatch, runtime_name: str,
) -> None:
    spec = _SPEC.replace("command: cat", "resources: {gpus: 1}\n      command: cat")
    root = analysis(spec)
    _resource_cluster(monkeypatch, gpus=1)
    pyproject = root / "pyproject.toml"
    pyproject.write_text(pyproject.read_text() + "\n[tool.lightcone.image]\napt-install = []\n")
    dataset.save(root, [pyproject], "declare a container image")
    monkeypatch.setattr(engine.container, "runtime_name", lambda _: runtime_name)

    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("image preparation began for an unsupported GPU runtime")

    monkeypatch.setattr(engine.container.image, "tag", unexpected)
    before = dataset.head(root)
    with pytest.raises(ProjectError, match="GPU containers require podman-hpc"):
        engine.materialize(root, [], cluster_id=CLUSTER_ID)
    assert dataset.head(root) == before
    assert not dataset.status(root)
    assert not (root / "results/baseline/first.txt").exists()


def test_empty_cluster_refuses_before_project_preparation(
    root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _resource_cluster(monkeypatch, workers=0)

    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("preparation began without an available worker")

    monkeypatch.setattr(engine.checkout, "fill", unexpected)
    with pytest.raises(ProjectError, match="no workers"):
        engine.materialize(root, [], cluster_id=CLUSTER_ID)
    assert not dataset.status(root)


@pytest.mark.parametrize(
    ("resource_spec", "gpus", "expected_parallelism"),
    [
        ("", 0, 4),
        ("cpus: 3, memory: 256Mi", 0, 1),
        ("cpus: 1, memory: 1Gi", 0, 2),
        ("cpus: 1, memory: 256Mi, gpus: 1", 2, 1),
    ],
)
def test_real_dask_respects_recipe_resource_reservations(
    analysis: Callable[..., Path], monkeypatch: pytest.MonkeyPatch,
    resource_spec: str, gpus: int, expected_parallelism: int,
) -> None:
    # Four Dask threads would run all four subprocesses together without
    # resource reservations. Each independent output records its live interval.
    spec = 'spec_version: "0.1.0"\noutputs:\n' + "".join(
        f"  task{index}:\n"
        "    type: metric\n"
        "    format: json\n"
        "    recipe:\n"
        f"      resources: {{{resource_spec}}}\n"
        "      command: python src/work.py {output}\n"
        for index in range(4)
    )
    root = analysis(spec, files={"src/work.py": """
        import json
        import os
        import sys
        import time
        from pathlib import Path
        start = time.monotonic()
        time.sleep(0.5)
        Path(sys.argv[1]).write_text(json.dumps([
            start, time.monotonic(), os.environ.get("CUDA_VISIBLE_DEVICES"),
        ]))
    """})
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "2,0")
    _resource_cluster(monkeypatch, gpus=gpus)

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.ok and len(report.made) == 4
    events = []
    for path in (root / "results").glob("task*.json"):
        start, finish, visible = json.loads(path.read_text())
        assert visible == ("2,0" if gpus else "")
        events.extend([(start, 1), (finish, -1)])
    live = peak = 0
    for _, change in sorted(events):
        live += change
        peak = max(peak, live)
    assert peak == expected_parallelism
    assert not dataset.status(root)


def test_current_gpu_output_needs_no_gpu_to_build_a_cpu_dependent(
    analysis: Callable[..., Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = _SPEC.replace("command: echo", "resources: {gpus: 1}\n      command: echo")
    root = analysis(spec)
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    _resource_cluster(monkeypatch, gpus=1)
    assert engine.materialize(root, ["first"], cluster_id=CLUSTER_ID).made == ["first#baseline"]
    original = (root / ".astra/records/baseline/first.json").read_bytes()

    _resource_cluster(monkeypatch)
    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)
    assert report.ok
    assert report.current == ["first#baseline"]
    assert report.made == ["second#baseline"]
    assert (root / ".astra/records/baseline/first.json").read_bytes() == original
    assert not dataset.status(root)


def test_current_outputs_are_not_submitted_to_dask(
    root: Path, inline: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine.materialize(root, [], cluster_id=CLUSTER_ID)

    class NoExecution(_Inline):
        def validate(self, tasks: Any) -> dict[Any, Any]:
            assert not list(tasks)
            return {}

        def submit(self, *args: Any, **kwargs: Any) -> Any:
            pytest.fail("a current output was submitted to Dask")

    _cluster(monkeypatch, NoExecution())
    assert len(engine.materialize(root, [], cluster_id=CLUSTER_ID).current) == 2


def test_a_real_cluster_still_fits_through_the_seam(root: Path, cluster_id: str) -> None:
    """The one test that starts Dask. The seam is only worth having if the
    thing it abstracts still goes through it."""
    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.made == ["first#baseline", "second#baseline"]
    assert not dataset.status(root)


def test_a_processes_cluster_fits_through_the_seam(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Workers in other processes — the shape every venue beyond one
    machine has. Pins that the unit crosses the boundary by reference
    (`worker.materialize`, `Task`, `RunContext`, `TaskResult`) and that
    results travel back whole."""

    from distributed import Client, LocalCluster

    from lightcone.engine import compute

    @contextmanager
    def processes(cluster_id: str) -> Iterator[Any]:
        with LocalCluster(
            n_workers=2, threads_per_worker=1, processes=True, dashboard_address=None,
            resources={"CPU": 1, "MEMORY": 1024**3},
        ) as cluster:
            with Client(cluster, set_as_default=False) as client:
                yield client

    monkeypatch.setattr(compute, "connect", processes)
    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.made == ["first#baseline", "second#baseline"]
    assert not dataset.status(root)


# ---- the report ------------------------------------------------------------


def test_the_report_is_json_ready(root: Path, inline: None) -> None:
    report = engine.materialize(root, ["first"], cluster_id=CLUSTER_ID)
    data = json.loads(json.dumps(report.as_dict()))

    assert data["ok"] is True
    assert data["made"] == ["first#baseline"]


# ---- the foreign-write fact ------------------------------------------------


def _forge(path: Path, text: str) -> None:
    """Overwrite a committed result the way a hand edit would — unlinking
    first, because results are committed thin: an in-place truncate would
    rewrite the shared annex object and dirty every file hard-linked to it
    (the recorded thin-write hazard, demonstrated by this very test suite
    when it forged in place)."""
    path.unlink()
    path.write_text(text)


def test_a_materialized_output_has_no_foreign_write(root: Path, inline: None) -> None:
    engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert all(not o.foreign_write for o in engine.status(root).outputs)


def test_a_foreign_write_is_stale_and_names_its_commit(root: Path, inline: None) -> None:
    """The agent-forged-file fact: a hand-edited-and-committed output would
    read `current` forever, because a skip returns the recorded digest —
    so a directory last written by anything but its own run record is a
    *contradiction*, and contradiction is what `stale` means."""
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    forged = root / "results" / "baseline" / "first.txt"
    _forge(forged, "curated by hand\n")
    dataset.save(root, [forged], "tweak colors")

    outputs = {o.output: o for o in engine.status(root).outputs}

    assert outputs["first#baseline"].status == "stale"
    forged_sha = dataset.last_writer(root, root / "results/baseline/first.txt").sha
    assert outputs["first#baseline"].foreign_write == forged_sha
    assert "tweak colors" in outputs["first#baseline"].why
    assert "git show" in outputs["first#baseline"].why
    assert not outputs["second#baseline"].foreign_write


def test_check_plans_the_remake_of_a_foreign_written_output(
    root: Path, inline: None
) -> None:
    """Status and `--check` answer from one walk, so they cannot disagree
    about a foreign write — and the gate exits nonzero over it."""
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    forged = root / "results" / "baseline" / "first.txt"
    _forge(forged, "curated by hand\n")
    dataset.save(root, [forged], "tweak colors")

    report = engine.check(root, [])

    assert "tweak colors" in report.planned["first#baseline"]
    assert "would be remade" in report.planned["second#baseline"]
    assert not report.up_to_date


def test_the_foreign_write_fact_survives_a_bytes_free_clone(
    root: Path, inline: None, tmp_path: Path
) -> None:
    """History-based on purpose: content changes move pointers in git, so
    the fact needs no annex content — where a rehash would have nothing to
    hash."""
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    forged = root / "results" / "baseline" / "first.txt"
    _forge(forged, "curated by hand\n")
    dataset.save(root, [forged], "tweak colors")
    clone = _clone(root, tmp_path)

    outputs = {o.output: o for o in engine.status(clone).outputs}

    assert outputs["first#baseline"].status == "stale"
    assert "tweak colors" in outputs["first#baseline"].why
    assert outputs["first#baseline"].foreign_write
    assert not outputs["second#baseline"].foreign_write


def test_the_next_run_remakes_a_foreign_written_output(root: Path, inline: None) -> None:
    """`results/` is lc's to write — the same philosophy as the dirty-tree
    refusal's path split — so a committed hand edit is remade, and the
    rebuild's own run record becomes the last writer again."""
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    forged = root / "results" / "baseline" / "first.txt"
    _forge(forged, "curated by hand\n")
    dataset.save(root, [forged], "tweak colors")

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert "first#baseline" in report.made
    assert forged.read_text() == "alpha\n"  # the recipe's bytes, not the hand's
    assert all(not o.foreign_write for o in engine.status(root).outputs)


# ---- the publication view --------------------------------------------------


def _declare_license(root: Path) -> None:
    """Declare publication intent the way a researcher would: one key,
    committed like any other edit."""
    pyproject = root / "pyproject.toml"
    pyproject.write_text(pyproject.read_text() + 'license = "MIT"\n')
    dataset.save(root, [pyproject], "declare a license")


def test_an_unlicensed_project_gets_no_crate_and_one_report_line(
    root: Path, inline: None
) -> None:
    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert not (root / "ro-crate-metadata.json").exists()
    assert any("[project].license" in w for w in report.warnings)


def test_a_licensed_materialize_converges_the_crate_and_commits_it(
    root: Path, inline: None
) -> None:
    _declare_license(root)

    engine.materialize(root, [], cluster_id=CLUSTER_ID)

    crate_path = root / "ro-crate-metadata.json"
    assert crate_path.is_file()
    assert not dataset.status(root)  # committed, tree exactly as clean as before
    assert dataset.last_writer(root, crate_path).subject == "Update the RO-Crate publication view"
    graph = json.loads(crate_path.read_text())["@graph"]
    types = {e["@id"]: e["@type"] for e in graph}
    assert "OrganizeAction" in types.values()
    assert types["results/baseline/first.txt"] == "File"


def test_an_idempotent_rerun_commits_nothing(root: Path, inline: None) -> None:
    """The document is a pure function of repository state — a re-render at
    the same state is a string compare, not a commit."""
    _declare_license(root)
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    before = _commits(root)

    engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert _commits(root) == before


def test_declaring_a_license_later_creates_the_crate_then(root: Path, inline: None) -> None:
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    assert not (root / "ro-crate-metadata.json").exists()

    _declare_license(root)
    engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert (root / "ro-crate-metadata.json").is_file()


def test_a_removed_license_stops_maintenance_but_keeps_the_file(
    root: Path, inline: None
) -> None:
    """The crate is in committed history either way; deleting a file over a
    possibly temporary edit is not convergence's call."""
    _declare_license(root)
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    pyproject = root / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace('license = "MIT"\n', ""))
    dataset.save(root, [pyproject], "drop the license")

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert (root / "ro-crate-metadata.json").is_file()
    assert any("no longer maintained" in w for w in report.warnings)


def test_status_places_the_publication_view(root: Path, inline: None) -> None:
    """The `crate:` header line, through its three plain states."""
    assert engine.status(root).crate == "not maintained — declare [project].license to enable it"

    _declare_license(root)
    assert engine.status(root).crate == "will be created by the next `lc materialize`"

    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    assert engine.status(root).crate == "up to date with the outputs"


def test_status_sees_the_crate_lag_a_rerun_leaves(root: Path, inline: None) -> None:
    """The recorded residue made visible: a rerun rewrites a record but
    never regenerates the view. Status reads the mismatch off the
    document's own datePublished against the records it already read —
    no git, no rocrate import."""
    _declare_license(root)
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    path = root / ".astra/records/baseline/second.json"
    record = _record(root, "second")
    record["generated_by"]["finished"] = "2027-01-01T00:00:00.000Z"
    path.write_text(json.dumps(record))
    dataset.save(root, [path], engine.datalad_run_subject("second#baseline"))

    assert engine.status(root).crate.startswith("behind")

    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    assert engine.status(root).crate == "up to date with the outputs"


def test_an_output_the_spec_dropped_is_excluded_and_named(root: Path, inline: None) -> None:
    _declare_license(root)
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    spec_path = root / "astra.yaml"
    spec = spec_path.read_text()
    block = spec[spec.index("  second:") : spec.index("\nuniverses:") + 1]
    spec_path.write_text(spec.replace(block, ""))
    dataset.save(root, [spec_path], "drop the second output")

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    document = (root / "ro-crate-metadata.json").read_text()
    assert "results/baseline/second.txt" not in document
    assert any(".astra/records/baseline/second.json" in w for w in report.warnings)
