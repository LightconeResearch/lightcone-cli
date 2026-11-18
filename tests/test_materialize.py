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
import pickle
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from conftest import CLUSTER_ID, _Inline

from lightcone.engine import assets, dataset, identity
from lightcone.engine import materialize as engine
from lightcone.engine.project import ProjectError, child_env
from lightcone.engine.worker import TaskResult

_SPEC = """
spec_version: "0.1.0"

decisions:
  method:
    options:
      alpha: {}
      beta: {}

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


_FIRST_RECORD = ".astra/records/baseline/first.json"


def _switch_method(root: Path) -> None:
    """Have the one universe select the other option."""
    spec = root / "astra.yaml"
    spec.write_text(spec.read_text().replace("{method: alpha}", "{method: beta}"))


def _commits(root: Path) -> int:
    return len(dataset._git(["log", "--oneline"], cwd=root).splitlines())


def _cluster(monkeypatch: pytest.MonkeyPatch, scheduler: _Inline) -> None:
    """Point the run at a custom scheduler — the one monkeypatch point."""

    @contextmanager
    def fake(cluster_id: str) -> Iterator[_Inline]:
        yield scheduler

    monkeypatch.setattr(engine, "cluster_for_run", fake)


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
    record can never come to describe other bytes than the ones beside it,
    and it is all lc writes beside them."""
    engine.materialize(root, ["first"], cluster_id=CLUSTER_ID)

    committed = dataset._git(
        ["show", "--name-only", "--format=", "HEAD"], cwd=root
    ).split()
    assert sorted(committed) == [
        ".astra/records/baseline/first.json",
        "results/baseline/first.txt",
    ]


def test_the_bytes_go_to_the_annex_and_the_record_to_git(root: Path, inline: None) -> None:
    """What git records is the test: a pointer for content, the real thing
    for a record. The working tree looks the same either way now."""
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
    record = json.loads((root / ".astra/records/baseline/first.json").read_text())
    assert record["generated_by"]["environment"]["iri"] == identity.environment(root)


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
    dataset.save(root, [root], "switch method")

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
    assert all(o.git_sha == "" and o.content_hash == "" for o in report.outputs)
    assert all(o.environment == "" for o in report.outputs)
    assert "no record" in report.outputs[0].why


def test_status_does_not_mind_a_dirty_tree(root: Path, inline: None) -> None:
    """It reads. Refusing here would make the one verb that tells you what
    state you are in unavailable exactly when you need it."""
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    (root / "notes.md").write_text("in progress\n")

    assert engine.status(root).counts["current"] == 2


def test_a_result_whose_bytes_are_not_its_records_is_stale(root: Path, inline: None) -> None:
    """Written over after its run, committed or not: what is at its place
    is not what its record describes, so it is made again — and so is
    what reads it."""
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    (root / "results/baseline/first.txt").unlink()
    (root / "results/baseline/first.txt").write_text("edited by hand\n")

    first, second = engine.status(root).outputs

    assert (first.status, second.status) == ("stale", "stale")
    assert "aren't the ones its record gives" in first.why
    assert second.why == "`first#baseline`, which it reads, is stale"


def test_status_reports_a_result_reading_a_stale_one_as_stale(
    root: Path, inline: None
) -> None:
    """ASTRA finds nothing wrong with `second` in itself, and it is still
    made from bytes the analysis no longer asks for: a result made from a
    stale one is stale until that one is made again."""
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    _switch_method(root)
    dataset.save(root, [root], "switch method")

    second = {o.output: o for o in engine.status(root).outputs}["second#baseline"]

    assert second.status == "stale"
    assert second.why == "`first#baseline`, which it reads, is stale"


def test_asking_for_an_output_makes_what_it_is_made_of(root: Path, inline: None) -> None:
    report = engine.materialize(root, ["second"], cluster_id=CLUSTER_ID)

    assert report.made == ["first#baseline", "second#baseline"]


def test_a_changed_decision_remakes_the_output_and_its_dependents(
    root: Path, inline: None
) -> None:
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    _switch_method(root)
    dataset.save(root, [root], "switch method")

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
    """What check mode cannot know, a run can: once its upstream is made,
    the dependent's task sees that the bytes it recorded reading are the
    bytes that came out."""
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


def test_the_previous_bytes_are_still_there_at_the_previous_commit(
    root: Path, inline: None
) -> None:
    """The property the whole layer exists for: given a commit, recover the
    exact bytes it produced."""
    engine.materialize(root, ["first"], cluster_id=CLUSTER_ID)
    original = dataset._git(["rev-parse", "HEAD"], cwd=root).strip()
    _switch_method(root)
    dataset.save(root, [root], "switch method")
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
    dataset.save(root, [root], "switch method")

    report = engine.check(root, [])

    assert "method" in report.planned["first#baseline"]
    assert report.planned["second#baseline"] == "`first#baseline`, which it reads, would be remade"


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
    engine.materialize(root, ["first"], cluster_id=CLUSTER_ID)
    (root / "notes.md").write_text("in progress\n")
    (root / "results/baseline/stray.txt").write_text("by hand\n")

    with pytest.raises(ProjectError) as raised:
        engine.materialize(root, [], cluster_id=CLUSTER_ID)

    message = str(raised.value)
    assert "commit these" in message and "notes.md" in message
    assert "discard these" in message and "results/baseline/stray.txt" in message
    assert message.index("lc compute down") < message.index("git clean -fd results/")
    # Nothing git knows of changed there, and `git restore` refuses a
    # folder it knows no file of.
    assert "git restore" not in message


def test_the_discard_command_undoes_what_an_interrupted_run_left(
    root: Path, inline: None
) -> None:
    """A task removes its record before its recipe runs, so an interrupted
    one leaves that deletion beside part of its result — all of it lc's
    to discard, none of it the researcher's to commit."""
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    (root / ".astra/records/baseline/second.json").unlink()
    (root / "results/baseline/second.txt").unlink()
    (root / "results/baseline/second.txt").write_text("hal")
    (root / "results/baseline/second.tmp").write_text("partial\n")

    with pytest.raises(ProjectError) as raised:
        engine.materialize(root, [], cluster_id=CLUSTER_ID)

    message = str(raised.value)
    assert "commit these" not in message
    [command] = [line.strip() for line in message.splitlines() if "git restore" in line]
    subprocess.run(command, shell=True, cwd=root, check=True, capture_output=True)
    assert not dataset.status(root)
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).up_to_date


def test_an_index_lock_that_never_goes_away_refuses_before_anything_runs(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every output is committed, so a lock a killed git left behind would
    fail each of them after its recipe had run."""
    monkeypatch.setattr(dataset, "_PATIENCE", 0.5)
    (root / ".git" / "index.lock").write_text("")

    def unexpected(*args: object) -> None:
        pytest.fail("a project that cannot commit contacted the cluster")

    monkeypatch.setattr(engine, "cluster_for_run", unexpected)
    with pytest.raises(ProjectError, match="holds the project's index"):
        engine.materialize(root, [], cluster_id=CLUSTER_ID)


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
    root = analysis(
        'spec_version: "0.1.0"\n',
    )

    def unexpected(*args: object) -> None:
        pytest.fail("a run that submits nothing contacted the cluster")

    monkeypatch.setattr(engine, "cluster_for_run", unexpected)
    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.ok and report.up_to_date


def test_a_project_with_nothing_to_make_needs_no_cluster(
    root: Path, inline: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What to make is decided before the cluster is asked for: a project
    whose results all stand is reported as it is."""
    engine.materialize(root, [], cluster_id=CLUSTER_ID)

    def unexpected(*args: object) -> None:
        pytest.fail("a run with nothing to make contacted the cluster")

    monkeypatch.setattr(engine, "cluster_for_run", unexpected)
    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.up_to_date and report.current == ["first#baseline", "second#baseline"]


@pytest.mark.parametrize("failing", ["first#baseline", "second#baseline"])
def test_a_failed_commit_says_whether_remote_tasks_may_still_run(
    root: Path, monkeypatch: pytest.MonkeyPatch, failing: str,
) -> None:
    _cluster(monkeypatch, _Inline())
    save = dataset.save

    def fail(root: Path, paths: list[Path], message: str) -> None:
        if message.startswith(engine.datalad_run_subject(failing)):
            raise ProjectError("git commit failed")
        save(root, paths, message)

    monkeypatch.setattr(dataset, "save", fail)
    with pytest.raises(ProjectError, match="git commit failed") as raised:
        engine.materialize(root, [], cluster_id=CLUSTER_ID)
    # Only the first output leaves another one outstanding.
    assert ("did not stop the allocation" in str(raised.value)) == (failing == "first#baseline")

def test_an_input_more_than_one_task_reads_is_hashed_once(
    analysis: Callable[..., Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """By the driver, and the digest crosses to each task with the stamp
    of the file it was taken of: each task would otherwise read all of it
    again to name it in its record. A run that makes nothing reads
    nothing."""
    spec = _consuming("data/catalog.fits").replace("inputs: [first]", "inputs: [first, catalog]")
    root = analysis(spec, files={"data/catalog.fits": "data"})
    hashed: list[Path] = []
    real = assets.digest_file

    def digest_file(path: Path) -> assets.Digest:
        hashed.append(path)
        return real(path)

    class _Copied(_Inline):
        def submit(
            self, fn: Callable[..., object], *args: object, key: str, resources: dict[str, float],
        ) -> object:
            return fn(*pickle.loads(pickle.dumps(args)))

    monkeypatch.setattr(assets, "digest_file", digest_file)
    _cluster(monkeypatch, _Copied())
    first = engine.materialize(root, [], cluster_id=CLUSTER_ID)
    assert len(first.made) == 2
    assert hashed.count(root / "data/catalog.fits") == 1
    for name in ("first", "second"):
        made = json.loads((root / f".astra/records/baseline/{name}.json").read_text())
        [catalog] = [i for i in made["generated_by"]["inputs"] if i["entry"] == "catalog"]
        assert (catalog["content_hash"], catalog["size"]) == real(root / "data/catalog.fits")
    second = engine.materialize(root, [], cluster_id=CLUSTER_ID)
    assert len(second.current) == 2
    assert hashed.count(root / "data/catalog.fits") == 1


def _consuming(path: str) -> str:
    """`_SPEC` with `first` reading a declared input at *path*."""
    return _SPEC.replace(
        "\ndecisions:\n", f"\ninputs:\n  catalog: {{path: {path}}}\n\ndecisions:\n", 1
    ).replace("    decisions: [method]", "    inputs: [catalog]\n    decisions: [method]")


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


def test_an_edit_while_the_graph_runs_is_reported(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dirty check runs at start of run and records are written
    per-output later, so an edit in between leaves records whose commit
    no longer describes the code that ran. The run ends with one status
    call and says so."""

    class Editing(_Inline):
        def completed(self, handles: list[object]) -> Iterator[object]:
            (root / "notes.md").write_text("scribbled while the graph ran\n")
            yield from handles

    _cluster(monkeypatch, Editing())

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.ok
    assert any("notes.md" in w and "in flight" in w for w in report.warnings)


def test_a_mid_run_stage_is_not_swept_into_lcs_commits(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`dataset.save` stages scoped and commits scoped — a partial
    commit — so work the user staged while the graph ran ends the run
    exactly where they left it: staged, warned about, and in none of
    lc's commits."""

    class Staging(_Inline):
        def completed(self, handles: list[object]) -> Iterator[object]:
            (root / "notes.py").write_text("draft = True\n")
            dataset._git(["add", "--", "notes.py"], cwd=root)
            yield from handles

    _cluster(monkeypatch, Staging())

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.ok
    assert any("notes.py" in w and "in flight" in w for w in report.warnings)
    staged = dataset._git(["diff", "--cached", "--name-only"], cwd=root).split()
    assert staged == ["notes.py"]
    ever_committed = dataset._git(["log", "--name-only", "--format="], cwd=root).split()
    assert "notes.py" not in ever_committed


def test_a_clean_run_reports_no_in_flight_edit(root: Path, inline: None) -> None:
    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)
    assert not any("in flight" in w for w in report.warnings)


# ---- leaving the tree as clean as it was found -----------------------------


def test_a_failing_recipe_commits_nothing_and_leaves_the_tree_clean(
    analysis: Callable[..., Path], inline: None
) -> None:
    """The invariant that makes the dirty-tree refusal survivable: the next
    run must not tell the user to commit truncated, unrecorded
    garbage."""
    spec = _SPEC.replace("echo {decisions.method} > {output}", "exit 1")
    root = analysis(spec)
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
    spec = _SPEC.replace("echo {decisions.method} > {output}", "exit 1")
    root = analysis(spec)

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.made == []
    assert not report.up_to_date
    assert json.loads(json.dumps(report.as_dict()))["up_to_date"] is False
    # The positive control is `test_a_second_run_does_nothing_and_commits_
    # nothing`, where the same empty `made` does mean up to date.


def test_a_rebuild_that_fails_puts_the_previous_output_back(
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


def test_an_interrupted_run_retains_what_never_reported(
    root: Path, inline: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sibling that already saved keeps its commit; the output still in
    flight may still have a writer, so its partial files are retained."""
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

    assert dataset.status(root)
    assert (root / "results/baseline/second.txt").read_text() == "changed\n"


# ---- where git stores what a run commits ----------------------------------


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


def test_a_result_git_ignores_is_refused(root: Path, inline: None) -> None:
    """`git add` skips an ignored path without a word."""
    _rules(root, ".gitignore", lambda text: text + "results/baseline/second.*\n")

    with pytest.raises(ProjectError, match="results/baseline/second.txt is git-ignored"):
        engine.materialize(root, [], cluster_id=CLUSTER_ID)


def test_a_result_another_filter_takes_is_refused(root: Path, inline: None) -> None:
    """`git lfs track` appends a line that wins over lightcone's: the bytes
    would go to Git LFS, which lc neither fetches from nor reads — or into
    git itself, wherever git-lfs is not installed."""
    _rules(root, ".gitattributes", lambda text: text + "*.txt filter=lfs diff=lfs merge=lfs\n")

    with pytest.raises(ProjectError, match="stored by `filter=lfs`") as raised:
        engine.materialize(root, [], cluster_id=CLUSTER_ID)
    assert "results/baseline/first.txt would be stored by `filter=lfs`" in str(raised.value)


def test_a_record_a_line_would_annex_is_refused(root: Path, inline: None) -> None:
    """A line the researcher adds below lightcone's would win: a record
    in the annex cannot be read on a clone that holds no content."""
    _rules(root, ".gitattributes", lambda text: text + ".astra/** annex.largefiles=anything\n")

    with pytest.raises(ProjectError, match="a record has to stay in git") as raised:
        engine.materialize(root, [], cluster_id=CLUSTER_ID)
    assert ".astra/records/baseline/first.json would be annexed" in str(raised.value)


def test_a_record_another_filter_takes_is_refused(root: Path, inline: None) -> None:
    _rules(root, ".gitattributes", lambda text: text + "*.json filter=lfs diff=lfs merge=lfs\n")

    with pytest.raises(ProjectError, match="a record has to stay in git") as raised:
        engine.materialize(root, [], cluster_id=CLUSTER_ID)
    assert ".astra/records/baseline/first.json would be stored by `filter=lfs`" in str(
        raised.value
    )


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
    """Declared inputs are annex symlinks, so a resolved path records
    `.git/annex/objects/SHA256E-…` — the storage rather than the input, and
    something no one can `datalad get`."""
    spec = """
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
    """
    root = analysis(spec, files={"data/catalog.txt": "measured\n"})
    dataset.save(root, [root / "data"], "the catalog")

    engine.materialize(root, [], cluster_id=CLUSTER_ID)

    from datalad.api import Dataset
    from datalad.local.rerun import get_run_info

    _, info = get_run_info(Dataset(str(root)), dataset._git(["log", "-1", "--format=%B"], cwd=root))
    assert info is not None
    assert info["inputs"] == ["data/catalog.txt"]


def test_every_record_of_one_run_names_the_commit_it_started_from(
    root: Path, inline: None
) -> None:
    """The driver commits each output as it lands, so HEAD moves during the
    run — and reading it per task would stamp later records with a commit
    this same run created, nondeterministically."""
    started_from = dataset.head(root)
    engine.materialize(root, [], cluster_id=CLUSTER_ID)

    commits = {
        json.loads((root / f".astra/records/baseline/{name}.json").read_text())[
            "generated_by"
        ]["commit"]
        for name in ("first", "second")
    }
    assert commits == {started_from}


def test_check_agrees_with_a_run_on_a_clone_with_no_annex_content(
    root: Path, inline: None, tmp_path: Path
) -> None:
    """Records are in git, so a result whose bytes were never fetched is
    still classifiable — ASTRA's checker reads the pointer file sitting in
    its place as a stand-in for the content, never as the content."""
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
  catalog: {path: data/catalog.fits}

outputs:
  copy:
    type: metric
    format: txt
    inputs: [catalog]
    recipe:
      command: cat {inputs.catalog} > {output}
"""


def test_a_bytes_free_clone_fetches_its_inputs_and_is_up_to_date(
    analysis: Callable[..., Path], inline: None, tmp_path: Path
) -> None:
    """lc fetches declared inputs rather than telling anyone to — a clone
    holding only pointers materializes straight to up-to-date, hashing
    the same bytes the origin recorded. Without the fetch this run
    *failed*: the worker's hash refused the pointer file."""
    root = analysis(_FETCH_SPEC, files={"data/catalog.fits": "stars\n"})
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok

    clone = _clone(root, tmp_path)
    with pytest.raises(assets.ContentNotFetchedError):
        assets.require_fetched(clone / "data" / "catalog.fits")

    again = engine.materialize(clone, [], cluster_id=CLUSTER_ID)

    assert again.up_to_date, (again.failed, again.warnings)
    assert not dataset.status(clone)


def test_a_bytes_free_clone_fetches_a_result_a_remade_one_reads(
    root: Path, inline: None, tmp_path: Path
) -> None:
    """A result left alone is still read by one that is remade, so its
    bytes are fetched like a declared input's. Without the fetch the
    remade task refused the pointer it found in their place."""
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    clone = _clone(root, tmp_path)
    spec = clone / "astra.yaml"
    spec.write_text(
        spec.read_text().replace(
            "cat {inputs.first} > {output}", "cat {inputs.first} > {output} && true"
        )
    )
    dataset.save(clone, [spec], "change only the second recipe")

    report = engine.materialize(clone, [], cluster_id=CLUSTER_ID)

    assert report.made == ["second#baseline"], (report.failed, report.warnings)
    assert report.current == ["first#baseline"]
    assert (clone / "results/baseline/second.txt").read_text() == "alpha\n"


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


_CHAIN = """
spec_version: "0.1.0"

inputs:
  catalog: {path: data/catalog.txt}

outputs:
  first:
    type: data
    format: txt
    inputs: [catalog]
    recipe:
      command: sort {inputs.catalog} > {output}

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


def test_a_clone_fetches_a_result_an_identical_rebuild_leaves_standing(
    analysis: Callable[..., Path], inline: None, tmp_path: Path
) -> None:
    """`second` may run only because `first` may, and is left alone when
    `first` comes out byte-identical. `third`, which does run, then reads
    the bytes `second` already had — so they are fetched, though `second`
    was among the tasks that might run."""
    root = analysis(_CHAIN, {"data/catalog.txt": "2\n1\n"})
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok
    clone = _clone(root, tmp_path)
    spec = clone / "astra.yaml"
    spec.write_text(
        spec.read_text()
        .replace("sort {inputs.catalog}", "sort  {inputs.catalog}")
        .replace("cat {inputs.second}", "cat -u {inputs.second}")
    )
    dataset.save(clone, [spec], "the same bytes upstream, another recipe downstream")

    report = engine.materialize(clone, [], cluster_id=CLUSTER_ID)

    assert report.ok, report.warnings
    assert (report.made, report.current) == (["first", "third"], ["second"])
    assert (clone / "results/third.txt").read_text() == "1\n"
    assert not dataset.status(clone)


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

    monkeypatch.setattr(engine, "_fetch_inputs", refuse)
    engine.check(root, [])
    engine.status(root)


def test_a_drifted_environment_is_made_to_match_before_anything_runs(
    root: Path, inline: None
) -> None:
    """Workers pass `--no-sync`, so this is the only place on a run's path
    where the environment is made to match the lock (a rerun's worker
    entry point syncs for itself). Reported and refused, a lock edited
    without a sync would leave recipes importing packages the lock does
    not describe while every record named the new lock's environment;
    doing it instead is shorter and impossible to ignore."""
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
    assert project_mod._env_is_current(root)


def test_the_recorded_command_reproduces_the_output(
    root: Path,
    inline: None,
    engine_dist: tuple[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The claim the record makes, run literally. `datalad rerun` removes
    the output, executes the recorded command, and commits what came
    back — so the record is written again inside the same commit, by the
    pinned engine resolved into an ephemeral environment."""
    pytest.importorskip("datalad")
    version, dist = engine_dist
    # The requirement seam, not `lc_version`: a git pin can only ever
    # build committed code, so the suite pins the wheel built from the
    # working tree — the code actually under test.
    monkeypatch.setattr(engine, "_engine_requirement", lambda: f"lightcone-cli=={version}")
    engine.materialize(root, ["first"], cluster_id=CLUSTER_ID)
    original = json.loads((root / _FIRST_RECORD).read_text())
    made_at = dataset.head(root)

    proc = subprocess.run(
        [sys.executable, "-c", "from datalad.api import rerun; rerun('HEAD')"],
        cwd=root,
        capture_output=True,
        text=True,
        env={**child_env(), "UV_FIND_LINKS": str(dist)},
    )

    assert proc.returncode == 0, proc.stderr
    # The record is among the run record's outputs, so the rerun wrote it
    # again — naming the commit it ran from — and `datalad rerun`
    # committed it with the bytes.
    rerun = json.loads((root / _FIRST_RECORD).read_text())
    assert rerun["content_hash"] == original["content_hash"]
    assert (rerun["content_hash"], rerun["size"]) == assets.digest_file(
        root / "results/baseline/first.txt"
    )
    assert rerun["generated_by"]["commit"] == made_at
    assert rerun["generated_by"]["finished"] != original["generated_by"]["finished"]
    assert dataset.head(root) != made_at
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
    original = json.loads((root / _FIRST_RECORD).read_text())

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
    rerun = json.loads((clone / _FIRST_RECORD).read_text())
    assert rerun["content_hash"] == original["content_hash"]
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

    monkeypatch.setattr(engine, "_fetch_inputs", unexpected)
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

    monkeypatch.setattr(engine, "_fetch_inputs", unexpected)
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

    monkeypatch.setattr(engine, "_fetch_inputs", unexpected)
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
    original = (root / _FIRST_RECORD).read_bytes()

    _resource_cluster(monkeypatch)
    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)
    assert report.ok
    assert report.current == ["first#baseline"]
    assert report.made == ["second#baseline"]
    assert (root / _FIRST_RECORD).read_bytes() == original
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
    (`worker.materialize`, `Task`, the checker's `Status`, `TaskResult`) and that
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

    assert any("tweak colors" in why for why in report.planned.values())
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
