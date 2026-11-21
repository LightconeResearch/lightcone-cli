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
from astra.api import Bytes
from conftest import CLUSTER_ID, _Inline, clone_of, locked_version, with_idna

from lightcone.engine import content, dataset, identity, plan
from lightcone.engine import materialize as engine
from lightcone.engine.project import ProjectError, child_env
from lightcone.engine.worker import TaskOutcome

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


def test_every_result_is_made_and_committed(root: Path, inline: None) -> None:
    before = _commits(root)

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.made == ["first#baseline", "second#baseline"]
    assert report.ok and not report.up_to_date
    assert (root / "results/baseline/second.txt").read_text() == "alpha\n"
    assert _commits(root) == before + 2
    assert not dataset.status(root)


def test_a_result_and_its_record_land_in_one_commit(root: Path, inline: None) -> None:
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
    assert report.unchanged == ["first#baseline", "second#baseline"]
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


def test_a_stale_result_is_stale_even_when_the_environment_also_moved(
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


def test_status_names_the_commit_each_result_came_from(root: Path, inline: None) -> None:
    """The verb's whole reason to exist: a result that is behind is not
    wrong, and this is where the code that produced it can be read back."""
    ran_against = dataset.head(root)
    engine.materialize(root, [], cluster_id=CLUSTER_ID)

    report = engine.status(root)

    assert [r.result for r in report.results] == ["first#baseline", "second#baseline"]
    assert all(r.state == "ok" for r in report.results)
    # The commit the tree was at when the run *started* — the code that
    # produced the result, not the commit the run itself went on to make.
    assert all(r.commit == ran_against for r in report.results)
    assert report.counts == {"ok": 2, "behind": 0, "stale": 0, "missing": 0}


def test_status_gives_astras_own_status_of_each_result(root: Path, inline: None) -> None:
    """In JSON, as ASTRA's checker reports it: the record, which names the
    environment each result ran in, where its bytes are, and each rule it
    breaks by code. lc minted the environment's name, so the project's
    own is beside them: the one a run made now would record."""
    engine.materialize(root, [], cluster_id=CLUSTER_ID)

    data = json.loads(json.dumps(engine.status(root).as_dict()))

    assert data["environment"] == identity.environment(root)
    for result in data["results"]:
        astra = result["astra"]
        assert (astra["state"], astra["bytes"]["state"]) == ("unchanged", "present")
        assert astra["record"]["generated_by"]["environment"]["iri"] == data["environment"]
        assert astra["record"]["address"] == result["result"]


def test_status_reports_behind_after_the_environment_moves(
    root: Path, inline: None
) -> None:
    made_at, made_in = dataset.head(root), identity.environment(root)
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    _move_the_environment(root)

    report = engine.status(root)

    assert report.counts == {"ok": 0, "behind": 2, "stale": 0, "missing": 0}
    assert all(r.commit == made_at for r in report.results), "the commit it was made at"
    assert "earlier environment" in report.results[0].why
    # Behind is exactly this: the record names another environment than
    # the project's own, and status shows both.
    assert report.environment == identity.environment(root) != made_in
    assert all(
        r.astra["record"]["generated_by"]["environment"]["iri"] == made_in
        for r in report.results
    )


def test_status_leaves_a_never_made_result_missing_and_without_a_commit(root: Path) -> None:
    """There is nothing to name — and an empty string rather than HEAD,
    which would claim the result came from a commit that never made it."""
    report = engine.status(root)

    assert report.counts == {"ok": 0, "behind": 0, "stale": 0, "missing": 2}
    assert all(r.commit == "" and "record" not in r.astra for r in report.results)
    assert "no record" in report.results[0].why


def test_status_does_not_mind_a_dirty_tree(root: Path, inline: None) -> None:
    """It reads. Refusing here would make the one verb that tells you what
    state you are in unavailable exactly when you need it."""
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    (root / "notes.md").write_text("in progress\n")

    assert engine.status(root).counts["ok"] == 2


def test_status_reports_a_result_reading_a_stale_one_as_stale(
    root: Path, inline: None
) -> None:
    """ASTRA finds nothing wrong with `second` in itself, and it is still
    made from bytes the analysis no longer asks for: stale through what it
    reads, by entry, until that is made again."""
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    _switch_method(root)
    dataset.save(root, [root], "switch method")

    second = {r.result: r for r in engine.status(root).results}["second#baseline"]

    assert second.state == "stale"
    assert second.why == "`first` has changed since this result read it"


def test_asking_for_an_output_makes_what_it_is_made_of(root: Path, inline: None) -> None:
    report = engine.materialize(root, ["second"], cluster_id=CLUSTER_ID)

    assert report.made == ["first#baseline", "second#baseline"]


def test_a_changed_decision_remakes_the_result_and_its_dependents(
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


def test_a_changed_command_remakes_the_result_and_its_dependents(
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
    assert report.unchanged == ["second#baseline"]
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


def test_check_cascades_through_a_result_it_already_decided_to_rebuild(
    root: Path, inline: None
) -> None:
    """Check mode cannot know whether a rebuild comes out byte-identical,
    so it plans what reads a stale result too — the one place it is
    deliberately more pessimistic than a worker."""
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    _switch_method(root)
    dataset.save(root, [root], "switch method")

    report = engine.check(root, [])

    assert "method" in report.planned["first#baseline"]
    assert report.planned["second#baseline"] == "`first` has changed since this result read it"


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

    assert report.up_to_date and report.unchanged == ["first#baseline", "second#baseline"]


@pytest.mark.parametrize("failing", ["first#baseline", "second#baseline"])
def test_a_failed_commit_says_whether_remote_tasks_may_still_run(
    root: Path, monkeypatch: pytest.MonkeyPatch, failing: str,
) -> None:
    _cluster(monkeypatch, _Inline())
    save = dataset.save

    def fail(root: Path, paths: list[Path], message: str, **stored: list[Path]) -> None:
        if message.startswith(f"[DATALAD RUNCMD] {failing}"):
            raise ProjectError("git commit failed")
        save(root, paths, message, **stored)

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
    real = content.digest_file

    def digest_file(path: Path) -> content.Digest:
        hashed.append(path)
        return real(path)

    class _Copied(_Inline):
        def submit(
            self, fn: Callable[..., object], *args: object, key: str, resources: dict[str, float],
        ) -> object:
            return fn(*pickle.loads(pickle.dumps(args)))

    monkeypatch.setattr(content, "digest_file", digest_file)
    _cluster(monkeypatch, _Copied())
    first = engine.materialize(root, [], cluster_id=CLUSTER_ID)
    assert len(first.made) == 2
    assert hashed.count(root / "data/catalog.fits") == 1
    for name in ("first", "second"):
        made = json.loads((root / f".astra/records/baseline/{name}.json").read_text())
        [catalog] = [i for i in made["generated_by"]["inputs"] if i["entry"] == "catalog"]
        assert (catalog["content_hash"], catalog["size"]) == real(root / "data/catalog.fits")
    second = engine.materialize(root, [], cluster_id=CLUSTER_ID)
    assert len(second.unchanged) == 2
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


def test_an_analysis_git_does_not_hold_is_refused(root: Path, inline: None) -> None:
    """ASTRA reads the analysis from the working tree, and a record names
    the commit it is compared at. An ignored, untracked `astra.yaml` leaves
    the tree clean and that commit without an analysis: whatever a run made
    would be stale at once."""
    (root / ".gitignore").write_text((root / ".gitignore").read_text() + "astra.yaml\n")
    dataset._git(["rm", "-q", "--cached", "astra.yaml"], cwd=root)
    dataset._git(["add", ".gitignore"], cwd=root)
    dataset._git(["commit", "-q", "-m", "stop tracking the analysis"], cwd=root)
    assert not dataset.status(root)

    with pytest.raises(ProjectError, match="astra.yaml — not in git as files of their own"):
        engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert not (root / "results/baseline").exists()


def test_an_analysis_committed_to_the_annex_is_refused(root: Path, inline: None) -> None:
    """An annexed file is committed as a pointer to its bytes: with them
    here, the tree holds the analysis and the commit a record names holds
    none to check the result against."""
    attributes = root / ".gitattributes"
    attributes.write_text(attributes.read_text() + "astra.yaml annex.largefiles=anything\n")
    # Out of the index first: git-annex leaves a file git already holds in git.
    dataset._git(["rm", "-q", "--cached", "astra.yaml"], cwd=root)
    dataset.save(root, [attributes, root / "astra.yaml"], "annex the analysis")
    pointer = dataset._git(["cat-file", "-p", "HEAD:astra.yaml"], cwd=root)
    assert pointer.startswith("/annex/objects/") and not dataset.status(root)

    with pytest.raises(ProjectError, match="astra.yaml — not in git as files of their own"):
        engine.materialize(root, [], cluster_id=CLUSTER_ID)


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


def test_a_rebuild_that_fails_puts_the_previous_result_back(
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
        def completed(self, handles: list[Any]) -> Iterator[TaskOutcome]:
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


def test_a_record_an_expression_annexes_is_refused_and_the_tree_left_as_it_was(
    root: Path, inline: None
) -> None:
    """An `annex.largefiles` expression can turn on the bytes, which do not
    exist before the run, so what git staged is the answer: a record that
    would land in the annex is committed by nothing, and what the task made
    goes, as a failed task's does. The result and record the last run made
    stand, and the tree is clean for the next run."""
    assert engine.materialize(root, ["first"], cluster_id=CLUSTER_ID).ok
    record = (root / _FIRST_RECORD).read_text()
    _rules(root, ".gitattributes", lambda text: text + ".astra/** annex.largefiles=largerthan=1b\n")
    _rules(root, "astra.yaml", lambda text: text.replace("> {output}\n", "> {output} && true\n", 1))
    head = dataset.head(root)

    with pytest.raises(ProjectError, match="staged in the annex") as raised:
        engine.materialize(root, ["first"], cluster_id=CLUSTER_ID)

    assert ".astra/records/baseline/first.json" in str(raised.value)
    assert dataset.head(root) == head
    assert (root / _FIRST_RECORD).read_text() == record
    assert (root / "results/baseline/first.txt").read_text() == "alpha\n"
    assert not dataset.status(root)


def test_a_save_moves_the_branch_by_its_own_commit_alone(root: Path, inline: None) -> None:
    """What a save tells git-annex is committed to git-annex's own branch,
    and nothing else moves: a `synced/` branch a `git annex sync` left
    pending is not merged into the one checked out in the middle of a run,
    as `git annex merge` would (*measured*)."""
    branch = dataset._git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=root).strip()
    before = dataset.head(root)
    dataset._git(["checkout", "-q", "-b", f"synced/{branch}"], cwd=root)
    (root / "notes.md").write_text("brought by a sync, not merged yet\n")
    dataset.save(root, [root / "notes.md"], "a change from another clone")
    dataset._git(["checkout", "-q", branch], cwd=root)

    assert engine.materialize(root, ["first"], cluster_id=CLUSTER_ID).ok

    added = dataset._git(["rev-list", f"{before}..HEAD"], cwd=root).split()
    assert len(added) == 1
    assert not (root / "notes.md").exists()


def test_a_clone_where_git_annex_was_never_set_up_is_refused(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clone holds `.gitattributes` but not the filter `git annex init`
    configures, and git takes `filter=annex` naming no filter for none at
    all: every result would be committed to git itself."""
    clone = tmp_path / "clone"
    dataset._git(["clone", "-q", str(root), str(clone)], cwd=tmp_path)
    for key, value in (("user.email", "t@example.com"), ("user.name", "Test")):
        dataset._git(["config", key, value], cwd=clone)

    def unexpected(*args: object) -> None:
        pytest.fail("a refused run contacted the cluster")

    monkeypatch.setattr(engine, "cluster_for_run", unexpected)

    with pytest.raises(ProjectError, match="git-annex is not set up in this clone"):
        engine.materialize(clone, [], cluster_id=CLUSTER_ID)


def test_a_clone_made_right_after_a_run_can_fetch_what_it_committed(
    root: Path, inline: None, tmp_path: Path
) -> None:
    """git-annex journals that this repository holds what a `git add`
    stored, and commits that to its branch only at its next command: a
    clone made before then is told of no copy. Each save is that command."""
    engine.materialize(root, ["first"], cluster_id=CLUSTER_ID)
    clone = clone_of(root, tmp_path)

    dataset._git(["annex", "get", "-q", "--", "results/baseline/first.txt"], cwd=clone)

    assert (clone / "results/baseline/first.txt").read_text() == "alpha\n"


# ---- what a result owns ------------------------------------------------------


def test_a_result_declared_in_another_format_leaves_its_earlier_file_alone(
    root: Path, inline: None
) -> None:
    """The format names the file, so a re-declared one is a new artifact.
    The earlier file is only no longer declared, and nothing says it should
    go: it stays committed, as does a file merely named like the result."""
    engine.materialize(root, ["first"], cluster_id=CLUSTER_ID)
    notes = root / "results/baseline/first.notes"
    notes.write_text("kept beside the result by hand\n")
    dataset.save(root, [notes], "notes on first")
    spec = root / "astra.yaml"
    spec.write_text(
        spec.read_text().replace("format: txt\n    decisions", "format: csv\n    decisions")
    )
    dataset.save(root, [spec], "first as csv")

    report = engine.materialize(root, ["first"], cluster_id=CLUSTER_ID)

    assert report.made == ["first#baseline"], (report.failed, report.warnings)
    held = dataset._git(["ls-tree", "--name-only", "HEAD", "--", "results/baseline/"], cwd=root)
    assert held.split() == [
        "results/baseline/first.csv",
        "results/baseline/first.notes",
        "results/baseline/first.txt",
    ]
    assert (root / "results/baseline/first.txt").read_text() == "alpha\n"
    assert notes.read_text() == "kept beside the result by hand\n"
    assert not dataset.status(root)


_BESIDE = """
spec_version: "0.1.0"

outputs:
  fit:
    type: data
    format: txt
    recipe:
      command: echo fit > {output} && echo index > {output}.idx
"""


def test_a_file_a_recipe_writes_beside_its_result_is_not_the_results(
    analysis: Callable[..., Path], inline: None
) -> None:
    """A result is the one file at its place. What else its recipe writes
    there no output declares and no record names, so it is neither
    committed as the result's nor removed as it: the run says it was left."""
    root = analysis(_BESIDE)

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.made == ["fit"], (report.failed, report.warnings)
    committed = dataset._git(["show", "--name-only", "--format=", "HEAD"], cwd=root).split()
    assert sorted(committed) == [".astra/records/fit.json", "results/fit.txt"]
    assert (root / "results/fit.txt.idx").read_text() == "index\n"
    assert any(
        "results/fit.txt.idx" in w and "left uncommitted" in w for w in report.warnings
    ), report.warnings


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


def test_a_bytes_free_clone_fetches_the_inputs_a_remade_result_reads(
    analysis: Callable[..., Path], inline: None, tmp_path: Path
) -> None:
    """lc fetches declared inputs rather than telling anyone to — a clone
    holding only pointers makes what is stale from the bytes the origin
    had. Without the fetch the worker's hash refuses the pointer file."""
    root = analysis(_FETCH_SPEC, files={"data/catalog.fits": "stars\n"})
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok
    clone = clone_of(root, tmp_path)
    spec = clone / "astra.yaml"
    spec.write_text(
        spec.read_text().replace(
            "cat {inputs.catalog} > {output}", "cat {inputs.catalog} > {output} && true"
        )
    )
    dataset.save(clone, [spec], "change the recipe")
    with pytest.raises(content.ContentNotFetchedError):
        content.require_fetched(clone / "data" / "catalog.fits")

    report = engine.materialize(clone, [], cluster_id=CLUSTER_ID)

    assert report.made == ["copy"], (report.failed, report.warnings)
    content.require_fetched(clone / "data" / "catalog.fits")
    assert (clone / "results/copy.txt").read_text() == "stars\n"
    assert not dataset.status(clone)


def test_a_bytes_free_clone_fetches_a_result_a_remade_one_reads(
    root: Path, inline: None, tmp_path: Path
) -> None:
    """A result left alone is still read by one that is remade, so its
    bytes are fetched like a declared input's. Without the fetch the
    remade task refused the pointer it found in their place."""
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    clone = clone_of(root, tmp_path)
    spec = clone / "astra.yaml"
    spec.write_text(
        spec.read_text().replace(
            "cat {inputs.first} > {output}", "cat {inputs.first} > {output} && true"
        )
    )
    dataset.save(clone, [spec], "change only the second recipe")

    report = engine.materialize(clone, [], cluster_id=CLUSTER_ID)

    assert report.made == ["second#baseline"], (report.failed, report.warnings)
    assert report.unchanged == ["first#baseline"]
    assert (clone / "results/baseline/second.txt").read_text() == "alpha\n"


def test_content_another_tool_keeps_is_refused_before_anything_runs(
    analysis: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ASTRA's checker says which tool keeps what a recipe reads. lc
    fetches only from git-annex, and a recipe handed a Git LFS pointer
    would read three lines of text for its data: the run is refused,
    naming the command that fetches it, before any cluster is asked."""
    pointer = f"version https://git-lfs.github.com/spec/v1\noid sha256:{'a' * 64}\nsize 12\n"
    root = analysis(_FETCH_SPEC, files={"data/catalog.fits": pointer})

    def unexpected(*args: object) -> None:
        pytest.fail("a refused run contacted the cluster")

    monkeypatch.setattr(engine, "cluster_for_run", unexpected)

    with pytest.raises(ProjectError) as raised:
        engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert "data/catalog.fits — kept by Git LFS; `git lfs pull` fetches it" in str(raised.value)
    assert not (root / "results/copy.txt").exists()
    assert not dataset.status(root)


def test_text_that_only_opens_like_a_git_lfs_pointer_is_read(
    analysis: Callable[..., Path], inline: None
) -> None:
    """ASTRA reads a Git LFS pointer by all of it, and so does lc: a file
    that only opens like one is content, which its recipe reads."""
    text = "version https://git-lfs.github.com/spec/v1\nis how a pointer opens\n"
    root = analysis(_FETCH_SPEC, files={"data/catalog.fits": text})

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.made == ["copy"], (report.failed, report.warnings)
    assert (root / "results/copy.txt").read_text() == text


#: A Git LFS pointer, as printf's format: each `\\n` a line break.
_LFS = "version https://git-lfs.github.com/spec/v1\\noid sha256:" + "a" * 64 + "\\nsize 12\\n"

_POINTER = f"""
spec_version: "0.1.0"

outputs:
  pointer:
    type: data
    format: txt
    recipe:
      command: printf '{_LFS}' > {{output}}
"""


def test_a_result_that_reads_as_a_stand_in_is_not_recorded(
    analysis: Callable[..., Path], inline: None
) -> None:
    """ASTRA reads a file shaped like a Git LFS pointer as one, for bytes a
    record of the file itself does not give: recorded, the result could
    never be checked, and every check would plan it again."""
    root = analysis(_POINTER)

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.failed == ["pointer"]
    assert any("Git LFS stand-in" in w for w in report.warnings), report.warnings
    assert not (root / ".astra/records/pointer.json").exists()
    assert not dataset.status(root)


def test_a_fetched_result_whose_bytes_are_not_its_records_is_read_by_nothing(
    root: Path, inline: None, tmp_path: Path
) -> None:
    """A git-annex key of another backend than SHA256 gives no SHA-256, so
    ASTRA's checker takes it to stand for the recorded bytes until they are
    here. Bytes committed over a result that way, and fetched for what reads
    it, are hashed before anything reads them: nothing is recorded as reading
    the record's bytes while it read others, and with them in the clone, the
    next run makes the result again."""
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok
    # git-annex keeps a modified file's backend, so the new bytes go in as
    # a new file.
    first = root / "results/baseline/first.txt"
    first.unlink()
    dataset.save(root, [first], "remove first")
    first.write_text("written over by hand\n")
    dataset._git(["config", "annex.backend", "MD5E"], cwd=root)
    dataset.save(root, [first], "write over first")
    clone = clone_of(root, tmp_path)
    spec = clone / "astra.yaml"
    spec.write_text(
        spec.read_text().replace(
            "cat {inputs.first} > {output}", "cat {inputs.first} > {output} && true"
        )
    )
    dataset.save(clone, [spec], "change only the second recipe")
    assert set(engine.check(clone, []).planned) == {"second#baseline"}

    report = engine.materialize(clone, [], cluster_id=CLUSTER_ID)

    assert (report.failed, report.blocked) == (["first#baseline"], ["second#baseline"])
    assert any("not the ones its record gives" in w for w in report.warnings)
    assert not dataset.status(clone)
    assert set(engine.check(clone, []).planned) == {"first#baseline", "second#baseline"}
    again = engine.materialize(clone, [], cluster_id=CLUSTER_ID)
    assert again.made == ["first#baseline", "second#baseline"], (again.failed, again.warnings)
    assert (clone / "results/baseline/second.txt").read_text() == "alpha\n"


def test_on_a_bytes_free_clone_check_and_status_fetch_nothing_and_plan_what_a_run_makes(
    analysis: Callable[..., Path], inline: None, tmp_path: Path
) -> None:
    """Records are in git and ASTRA's checker reads each pointer as a
    stand-in, so a clone holding only pointers is classified as it is:
    `status` and `--check` report an unfetched input as a fact, never a
    transfer, and what `--check` plans there is what a run then makes."""
    root = analysis(_FETCH_SPEC, files={"data/catalog.fits": "stars\n"})
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok
    clone = clone_of(root, tmp_path)
    spec = clone / "astra.yaml"
    spec.write_text(spec.read_text().replace("cat {inputs.catalog}", "cat -u {inputs.catalog}"))
    dataset.save(clone, [spec], "change the recipe")

    states = [(found.result, found.state) for found in engine.status(clone).results]
    planned = engine.check(clone, []).planned

    assert states == [("copy", "stale")]
    assert set(planned) == {"copy"}
    for unfetched in ("data/catalog.fits", "results/copy.txt"):
        with pytest.raises(content.ContentNotFetchedError):
            content.require_fetched(clone / unfetched)
    assert engine.materialize(clone, [], cluster_id=CLUSTER_ID).made == list(planned)


def test_a_bytes_free_clone_that_is_up_to_date_fetches_nothing(
    analysis: Callable[..., Path], inline: None, tmp_path: Path
) -> None:
    """A run fetches what its recipes read. With nothing to make, a clone
    holding only pointers is up to date as it is."""
    root = analysis(_FETCH_SPEC, files={"data/catalog.fits": "stars\n"})
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok

    clone = clone_of(root, tmp_path)
    assert engine.check(clone, []).planned == {}
    again = engine.materialize(clone, [], cluster_id=CLUSTER_ID)

    assert again.up_to_date, (again.failed, again.warnings)
    with pytest.raises(content.ContentNotFetchedError):
        content.require_fetched(clone / "data" / "catalog.fits")
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
    clone = clone_of(root, tmp_path)
    spec = clone / "astra.yaml"
    spec.write_text(
        spec.read_text()
        .replace("sort {inputs.catalog}", "sort  {inputs.catalog}")
        .replace("cat {inputs.second}", "cat -u {inputs.second}")
    )
    dataset.save(clone, [spec], "the same bytes upstream, another recipe downstream")

    report = engine.materialize(clone, [], cluster_id=CLUSTER_ID)

    assert report.ok, report.warnings
    assert (report.made, report.unchanged) == (["first", "third"], ["second"])
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
    clone = clone_of(root, tmp_path)
    dataset._git(["remote", "remove", "origin"], cwd=clone)
    spec = clone / "astra.yaml"
    spec.write_text(spec.read_text().replace("cat {inputs.catalog}", "cat -u {inputs.catalog}"))
    dataset.save(clone, [spec], "edit the recipe")

    report = engine.materialize(clone, [], cluster_id=CLUSTER_ID)

    assert not report.ok
    assert report.failed == ["copy"]
    assert any("could not be fetched" in w for w in report.warnings)



# ---- where the bytes are -----------------------------------------------------
#
# A clone holds each result as a stand-in until its bytes are fetched.
# Whether a remote holds them is what decides between a fetch and a run.

_FIRST = "results/baseline/first.txt"
_SECOND = "results/baseline/second.txt"

#: :data:`_SPEC`, `second` made of other bytes than `first`: git-annex
#: stores one object for the same bytes, and fetches them for every file.
_TWO = _SPEC.replace("cat {inputs.first} >", "cat {inputs.first} {inputs.first} >")


def test_a_result_a_remote_holds_is_ok_and_unfetched(
    root: Path, inline: None, tmp_path: Path
) -> None:
    """What is current needs a transfer, not compute: status says where its
    bytes are, `--check` plans nothing, and a run fetches nothing. Asking
    git-annex leaves the branch and the tree as they were."""
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok
    clone = clone_of(root, tmp_path)
    head = dataset.head(clone)

    states = [(r.result, r.state, r.unfetched, r.remotes) for r in engine.status(clone).results]

    assert states == [
        ("first#baseline", "ok", True, ["origin"]),
        ("second#baseline", "ok", True, ["origin"]),
    ]
    assert engine.check(clone, []).up_to_date
    assert engine.materialize(clone, [], cluster_id=CLUSTER_ID).up_to_date
    with pytest.raises(content.ContentNotFetchedError):
        content.require_fetched(clone / _FIRST)
    assert dataset.head(clone) == head and not dataset.status(clone)


def test_a_file_git_left_a_stand_in_for_bytes_this_clone_holds_is_filled_in(
    analysis: Callable[..., Path], inline: None, tmp_path: Path
) -> None:
    """`git reset --hard` leaves an annexed file a stand-in even where the
    clone holds its bytes, and git-annex's hooks do not fill it in
    (measured): read as it is, the result would be one to fetch from where
    it already is."""
    root = analysis(_TWO)
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok
    clone = clone_of(root, tmp_path)
    assert engine.fetch(clone, []).ok
    (clone / _FIRST).unlink()
    dataset._git(["reset", "-q", "--hard"], cwd=clone)
    with pytest.raises(content.ContentNotFetchedError):
        content.require_fetched(clone / _FIRST)

    states = [(r.state, r.unfetched) for r in engine.status(clone).results]

    assert states == [("ok", False), ("ok", False)]
    assert (clone / _FIRST).read_text() == "alpha\n"
    assert not dataset.status(clone)


def test_a_result_no_remote_holds_is_missing_and_made_again(
    analysis: Callable[..., Path], inline: None, tmp_path: Path
) -> None:
    """A colleague pushed the commit and not the bytes: the log names the
    repository that made them, which is no remote of this clone, so a run
    can only make them again."""
    root = analysis(_TWO)
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok
    clone = clone_of(root, tmp_path)
    dataset._git(["remote", "remove", "origin"], cwd=clone)

    found = {r.result: (r.state, r.why) for r in engine.status(clone).results}
    planned = engine.check(clone, []).planned
    report = engine.materialize(clone, [], cluster_id=CLUSTER_ID)

    assert found == {
        "first#baseline": ("missing", dataset.NO_REMOTE),
        "second#baseline": ("missing", dataset.NO_REMOTE),
    }
    assert set(planned) == {"first#baseline", "second#baseline"}
    assert report.made == ["first#baseline", "second#baseline"], report.warnings
    assert (clone / _SECOND).read_text() == "alpha\nalpha\n"


def test_fetch_brings_the_bytes_of_current_results_and_not_of_stale_ones(
    analysis: Callable[..., Path], inline: None, tmp_path: Path
) -> None:
    """A stale result's recorded bytes are not what the analysis asks for,
    and a run makes it again: fetching them is a transfer for nothing."""
    root = analysis(_TWO)
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok
    clone = clone_of(root, tmp_path)
    spec = clone / "astra.yaml"
    spec.write_text(spec.read_text().replace("cat {inputs.first}", "cat -u {inputs.first}"))
    dataset.save(clone, [spec], "change the second recipe")

    report = engine.fetch(clone, [])

    assert (report.fetched, report.left, report.failed) == (
        ["first#baseline"], {"second#baseline": "stale"}, {}
    )
    assert (clone / _FIRST).read_text() == "alpha\n"
    with pytest.raises(content.ContentNotFetchedError):
        content.require_fetched(clone / _SECOND)
    assert [(r.state, r.unfetched) for r in engine.status(clone).results] == [
        ("ok", False), ("stale", False)
    ]
    assert not dataset.status(clone)
    assert engine.fetch(clone, ["first"]).present == ["first#baseline"]


def test_fetch_of_a_target_fetches_it_and_not_what_it_reads(
    analysis: Callable[..., Path], inline: None, tmp_path: Path
) -> None:
    root = analysis(_TWO)
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok
    clone = clone_of(root, tmp_path)

    assert engine.fetch(clone, ["second"]).fetched == ["second#baseline"]
    assert [r.unfetched for r in engine.status(clone).results] == [True, False]


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
      command: cat {inputs.catalog} {inputs.catalog} > {output}
"""


def test_fetch_brings_an_input_named_by_its_id(
    analysis: Callable[..., Path], inline: None, tmp_path: Path
) -> None:
    """An input is what its commit names, whatever reads it: fetched when
    named, to read it, and never with a result or with no target."""
    root = analysis(_READS, {"data/catalog.txt": "catalog v1\n"})
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok
    clone = clone_of(root, tmp_path)

    assert engine.fetch(clone, []).fetched == ["fit"]
    with pytest.raises(content.ContentNotFetchedError):
        content.require_fetched(clone / "data/catalog.txt")

    report = engine.fetch(clone, ["catalog"])

    assert (report.fetched, report.inputs, report.ok) == (["catalog"], ["catalog"], True)
    assert (clone / "data/catalog.txt").read_text() == "catalog v1\n"
    again = engine.fetch(clone, ["catalog", "fit"])
    assert (again.fetched, again.present) == ([], ["catalog", "fit"])
    assert not dataset.status(clone)


def test_fetch_of_what_names_nothing_says_what_there_is(
    analysis: Callable[..., Path], inline: None
) -> None:
    root = analysis(_READS, {"data/catalog.txt": "catalog v1\n"})

    with pytest.raises(ProjectError) as refused:
        engine.fetch(root, ["catlog"])

    assert "no result or input matches `catlog`" in str(refused.value)
    assert "Results: fit. Inputs: catalog." in str(refused.value)


def test_an_input_nobody_holds_is_not_said_to_be_one_a_run_makes(
    analysis: Callable[..., Path], inline: None, tmp_path: Path
) -> None:
    """A clone of a clone that never fetched the input: the log names the
    first repository, which is no remote here. A result in that state is
    made again; nothing makes an input, so nothing says so."""
    root = analysis(_READS, {"data/catalog.txt": "catalog v1\n"})
    (tmp_path / "second").mkdir()
    second = clone_of(clone_of(root, tmp_path), tmp_path / "second")

    report = engine.fetch(second, ["catalog"])

    assert report.failed == {"catalog": dataset.NO_REMOTE}


def test_fetching_an_input_does_not_set_git_annex_up_behind_lc_inits_back(
    analysis: Callable[..., Path], inline: None, tmp_path: Path
) -> None:
    """Asked to fetch in a clone it was never set up in, git-annex sets
    itself up (measured), without the flag that makes a missing filter
    loud: the next `git add` could then put raw bytes in git."""
    root = analysis(_READS, {"data/catalog.txt": "catalog v1\n"})
    clone = tmp_path / "plain"
    dataset._git(["clone", "-q", str(root), str(clone)], cwd=tmp_path)

    with pytest.raises(ProjectError, match="`lc init` sets it up"):
        engine.fetch(clone, ["catalog"])

    assert not dataset.is_annexed(clone)


def test_status_says_whether_the_project_is_online_at_all(root: Path) -> None:
    """From the clone's `origin` alone, with no network: a repository on
    GitHub, or not."""
    assert not engine.status(root).online

    dataset._git(["remote", "add", "origin", "/a/colleague/analysis"], cwd=root)
    assert not engine.status(root).online

    dataset._git(["remote", "set-url", "origin", "git@github.com:lcr/analysis.git"], cwd=root)
    assert engine.status(root).online and engine.status(root).as_dict()["online"]


def test_a_result_its_remote_lost_is_missing_once_a_fetch_finds_out(
    analysis: Callable[..., Path], inline: None, tmp_path: Path
) -> None:
    """The log says what was last heard of a remote, and `git annex get`
    fails alike on a remote that lost a copy and on one it cannot reach.
    Asked, the remote says which: what it lost is missing from then on."""
    root = analysis(_TWO)
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok
    clone = clone_of(root, tmp_path)
    key = dataset._git(["annex", "lookupkey", _FIRST], cwd=root).strip()
    stored = root / dataset._git(["annex", "contentlocation", key], cwd=root).strip()
    stored.parent.chmod(0o755)
    stored.unlink()  # behind git-annex's back: its log still says root holds it

    report = engine.fetch(clone, [])

    assert (list(report.failed), report.fetched) == (["first#baseline"], ["second#baseline"])
    assert report.failed["first#baseline"].startswith(dataset.NO_REMOTE)
    assert "lc materialize" in report.failed["first#baseline"]
    states = [(r.result, r.state) for r in engine.status(clone).results]
    assert states == [("first#baseline", "missing"), ("second#baseline", "ok")]


def test_a_remote_that_cannot_be_reached_is_left_as_the_log_has_it(
    root: Path, inline: None, tmp_path: Path
) -> None:
    """Unreachable is not lost: what the remote holds may be fetched later,
    and calling it missing would have a run make it again."""
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok
    clone = clone_of(root, tmp_path)
    dataset._git(["remote", "set-url", "origin", str(tmp_path / "gone")], cwd=clone)

    report = engine.fetch(clone, ["first"])

    assert list(report.failed) == ["first#baseline"]
    assert "origin" in report.failed["first#baseline"]
    assert not report.ok
    first = engine.status(clone).results[0]
    assert (first.state, first.unfetched, first.remotes) == ("ok", True, ["origin"])


def test_status_on_a_clone_where_git_annex_was_never_set_up_says_to_set_it_up(
    root: Path, inline: None, tmp_path: Path
) -> None:
    """Where its bytes are is git-annex's to say, and asking it in a clone
    it was never set up in would set it up as a side effect."""
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok
    clone = tmp_path / "clone"
    dataset._git(["clone", "-q", str(root), str(clone)], cwd=tmp_path)

    with pytest.raises(ProjectError, match="`lc init` sets it up"):
        engine.status(clone)
    assert not dataset.is_annexed(clone)


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


def test_the_recorded_command_reproduces_the_result(
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
    assert (rerun["content_hash"], rerun["size"]) == content.digest_file(
        root / "results/baseline/first.txt"
    )
    assert rerun["generated_by"]["commit"] == made_at
    assert rerun["generated_by"]["finished"] != original["generated_by"]["finished"]
    assert dataset.head(root) != made_at
    assert not dataset.status(root)


_IMPORTS = """
spec_version: "0.1.0"

outputs:
  version:
    type: data
    format: txt
    recipe:
      command: python -c 'import idna; print(idna.__version__)' > {output}
"""


def test_the_recorded_command_holds_on_a_fresh_clone(
    analysis: Callable[..., Path],
    inline: None,
    tmp_path: Path,
    engine_dist: tuple[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clone checks out the lock but never `.venv`, and `uv run --no-sync`
    against a missing environment silently creates an *empty* one — so the
    record only reproduces the output because the worker converges the
    environment for itself. The in-place rerun above cannot catch a missing
    sync; this is the test that does. The recipe imports what the lock
    pins and writes its version, which no empty environment holds."""
    pytest.importorskip("datalad")
    version, dist = engine_dist
    monkeypatch.setattr(engine, "_engine_requirement", lambda: f"lightcone-cli=={version}")
    root = with_idna(analysis(_IMPORTS))
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok
    original = json.loads((root / ".astra/records/version.json").read_text())

    clone = clone_of(root, tmp_path)
    assert not (clone / ".venv").exists()

    proc = subprocess.run(
        [sys.executable, "-c", "from datalad.api import rerun; rerun('HEAD')"],
        cwd=clone,
        capture_output=True,
        text=True,
        env={**child_env(), "UV_FIND_LINKS": str(dist)},
    )

    assert proc.returncode == 0, proc.stderr
    rerun = json.loads((clone / ".astra/records/version.json").read_text())
    assert rerun["content_hash"] == original["content_hash"]
    made = (clone / "results/version.txt").read_text().strip()
    assert made == locked_version(clone, "idna")


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
    assert len(engine.status(root).results) == 2
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



def test_an_unchanged_gpu_result_needs_no_gpu_to_build_a_cpu_dependent(
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
    assert report.unchanged == ["first#baseline"]
    assert report.made == ["second#baseline"]
    assert (root / _FIRST_RECORD).read_bytes() == original
    assert not dataset.status(root)


def test_unchanged_results_are_not_submitted_to_dask(
    root: Path, inline: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine.materialize(root, [], cluster_id=CLUSTER_ID)

    class NoExecution(_Inline):
        def validate(self, tasks: Any) -> dict[Any, Any]:
            assert not list(tasks)
            return {}

        def submit(self, *args: Any, **kwargs: Any) -> Any:
            pytest.fail("an unchanged result was submitted to Dask")

    _cluster(monkeypatch, NoExecution())
    assert len(engine.materialize(root, [], cluster_id=CLUSTER_ID).unchanged) == 2

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
    (`worker.materialize`, `Task`, the checker's `Status`, `TaskOutcome`) and that
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


# ---- bytes that are not the record's ---------------------------------------


def _forge(path: Path, text: str) -> None:
    """Overwrite a committed result the way a hand edit would — unlinking
    first, because results are committed thin: an in-place truncate would
    rewrite the shared annex object and dirty every file hard-linked to it
    (the recorded thin-write hazard, demonstrated by this very test suite
    when it forged in place)."""
    path.unlink()
    path.write_text(text)


def test_bytes_that_are_not_the_records_make_the_result_stale(
    root: Path, inline: None
) -> None:
    """Written over after its run and committed: ASTRA's checker finds the
    record still holds and warns that the bytes at its place are not the
    ones it gives. The result is not the one its recipe made, and nothing
    can fetch the recorded bytes: it is stale, in ASTRA's words, `--check`
    plans it, and a run makes it. What reads it read the recorded bytes,
    so it stands until a rebuild gives others."""
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    forged = root / "results" / "baseline" / "first.txt"
    _forge(forged, "curated by hand\n")
    dataset.save(root, [forged], "tweak colors")

    first, second = engine.status(root).results
    assert first.state == "stale"
    assert "aren't the ones its record gives" in first.why
    assert [d["code"] for d in first.astra["why"]] == ["ABSENT_RESULT"]
    assert (second.state, second.why) == ("ok", "")

    planned = engine.check(root, [])
    assert "aren't the ones its record gives" in planned.planned["first#baseline"]
    assert planned.planned["second#baseline"] == "`first#baseline`, which it reads, would be remade"
    assert not planned.up_to_date


def test_the_run_makes_them_again_and_what_reads_them_stands(
    root: Path, inline: None
) -> None:
    """The recipe gives back the bytes its record named, so what reads the
    result is left alone: the cascade stops where the bytes do."""
    engine.materialize(root, [], cluster_id=CLUSTER_ID)
    forged = root / "results" / "baseline" / "first.txt"
    _forge(forged, "curated by hand\n")
    dataset.save(root, [forged], "tweak colors")

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert (report.made, report.unchanged) == (["first#baseline"], ["second#baseline"])
    assert forged.read_text() == "alpha\n"  # the recipe's bytes, not the hand's
    assert all(r.why == "" for r in engine.status(root).results)


# ---- the rule over what ASTRA's checker finds in a real project ------------

_CATALOG_CHAIN = """
spec_version: "0.1.0"
inputs:
  catalog: {path: data/catalog.txt}
decisions:
  method:
    options:
      mcmc: {}
      nested: {}
universes:
  baseline: {decisions: {method: mcmc}}
outputs:
  summary:
    type: data
    format: txt
    inputs: [catalog]
    recipe:
      command: cp {inputs.catalog} {output}
  fit:
    type: data
    format: txt
    inputs: [summary]
    decisions: [method]
    recipe:
      command: cp {inputs.summary} {output}
"""


def test_before_any_run_every_planned_result_is_missing(
    analysis: Callable[..., Path],
) -> None:
    root = analysis(_CATALOG_CHAIN, {"data/catalog.txt": "measured\n"})

    statuses = plan.build(root).checked.statuses

    assert set(statuses) == {"summary", "fit#baseline"}
    for found in statuses.values():
        verdict = plan.classify(found=found, environment=identity.environment(root))
        assert verdict.state == "missing" and verdict.why


def test_a_made_result_is_unchanged_until_something_it_is_made_from_moves(
    analysis: Callable[..., Path], inline: None
) -> None:
    """The rule over the real checker, end to end: unchanged after its own
    run, behind against another environment, stale once its input changes
    — and the result reading it stale only through it, until it is made
    again with the bytes it had."""
    root = analysis(_CATALOG_CHAIN, {"data/catalog.txt": "measured\n"})
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok
    environment = identity.environment(root)

    graph = plan.build(root)
    for key, found in graph.checked.statuses.items():
        assert (found.state, found.bytes, found.why) == ("unchanged", Bytes("present"), ())
        assert found.record is not None
        assert found.record.generated_by.environment.iri == environment
        # The record gives the bytes as lc hashes them, which is how
        # ASTRA's checker found them present.
        made = graph.tasks[key].path
        assert content.digest_file(made) == (found.record.content_hash, found.record.size)
        assert plan.classify(found=found, environment=environment).state == "ok"
        assert plan.classify(found=found, environment=environment + "0").state == "behind"
    summary_made = graph.checked.statuses["summary"].record
    assert summary_made is not None
    recorded = (summary_made.content_hash, summary_made.size)

    # Unlinked first: the results are copies of the catalog, so all three
    # share one annex object, and a write in place would change them too.
    (root / "data" / "catalog.txt").unlink()
    (root / "data" / "catalog.txt").write_text("measured again\n")
    dataset.save(root, [root / "data"], "new catalog")

    statuses = plan.build(root).checked.statuses
    summary = plan.classify(found=statuses["summary"], environment=environment)
    assert summary.state == "stale" and "catalog" in summary.why
    # `fit` moved only because `summary` did: ASTRA finds no rule it
    # breaks itself, and names the entry. Whether it is remade turns on
    # the bytes `summary` comes out with.
    fit = statuses["fit#baseline"]
    assert (fit.state, fit.why, fit.stale_inputs) == ("stale", (), ("summary",))
    assert plan.classify(found=fit, environment=environment).state == "stale"
    kept = plan.classify(found=fit, environment=environment, read={"summary": recorded})
    assert kept.state == "ok"
    remade = plan.classify(
        found=fit, environment=environment, read={"summary": ("sha256:" + "0" * 64, 15)}
    )
    assert (remade.state, remade.why) == (
        "stale",
        "`summary` was remade with other bytes than this result read",
    )
