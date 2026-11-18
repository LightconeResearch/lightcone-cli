"""Tests for `lightcone.engine.plan` — the analysis, read as a graph.

What the analysis *means* is ASTRA's and is tested there. What is pinned
here is what lc adds: that each planned result becomes one task, keyed by
ASTRA's id for it, with the file each entry reads and the options it is
made with, beside ASTRA's checker's report; that everything lc
cannot build, or a target that matches nothing, is an error rather than a
guess.
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest
from astra import api

from lightcone.engine import plan
from lightcone.engine.content import Digest
from lightcone.engine.plan import Graph, classify
from lightcone.engine.project import ProjectError

_SPEC = """
spec_version: "0.1.0"
label: demo

inputs:
  catalog: {path: data/catalog.fits}

decisions:
  method:
    options:
      mcmc: {}
      nested: {}

outputs:
  fit:
    type: metric
    format: json
    inputs: [catalog]
    decisions: [method]
    recipe:
      command: python src/fit.py {inputs.catalog} --method {decisions.method} {output}

  report:
    type: report
    format: md
    inputs: [fit]
    recipe:
      command: python src/report.py {inputs.fit} {output}

  summary:
    type: table
    format: tar.gz
    inputs: [fit#baseline]
    recipe:
      command: python src/summary.py {inputs} {output}

universes:
  baseline: {decisions: {method: mcmc}}
"""

#: A second universe, selecting the other option.
_TWO = _SPEC + "  alternative: {decisions: {method: nested}}\n"


def _project(root: Path, spec: str = _SPEC) -> Path:
    """Write the analysis into a repository, its input tracked: ASTRA reads
    an analysis in a git working tree, and an input git does not track
    is no input."""
    (root / "data").mkdir(exist_ok=True)
    (root / "astra.yaml").write_text(textwrap.dedent(spec))
    (root / "data" / "catalog.fits").write_text("catalog\n")
    for argv in (["init", "-q"], ["add", "."]):
        subprocess.run(["git", *argv], cwd=root, check=True, capture_output=True)
    return root


def _build(root: Path) -> Graph:
    return plan.build(root)


# ---- what the graph contains -----------------------------------------------


def test_one_task_per_planned_result_keyed_by_its_id(tmp_path: Path) -> None:
    """Per universe, or once for an output that reads only what is pinned.
    The key is ASTRA's id for the result, never a second naming of lc's."""
    graph = _build(_project(tmp_path))
    assert sorted(graph.tasks) == ["fit#baseline", "report#baseline", "summary"]


def test_every_universe_gets_its_own_task(tmp_path: Path) -> None:
    graph = _build(_project(tmp_path, _TWO))
    assert [key for key, task in graph.tasks.items() if task.name == "fit"] == [
        "fit#baseline",
        "fit#alternative",
    ]


def test_a_result_is_at_the_place_astra_gives_it(tmp_path: Path) -> None:
    task = _build(_project(tmp_path)).tasks["fit#baseline"]
    assert task.path == tmp_path / "results" / "baseline" / "fit.json"
    assert task.record_paths == (tmp_path / ".astra" / "records" / "baseline" / "fit.json",)
    assert "results/baseline/fit.json" in task.command


def test_a_result_built_once_has_no_universe_folder(tmp_path: Path) -> None:
    """And a format holding a dot still leaves the record named after the
    result alone."""
    task = _build(_project(tmp_path)).tasks["summary"]
    assert task.name == "summary"
    assert task.path == tmp_path / "results" / "summary.tar.gz"
    assert task.record_paths == (tmp_path / ".astra" / "records" / "summary.json",)


def test_recipe_resources_survive_graph_resolution(tmp_path: Path) -> None:
    spec = _SPEC.replace(
        "command: python src/fit.py",
        "resources: {cpus: 4, memory: 6Gi, time_limit: 1h30m}\n"
        "      command: python src/fit.py",
    )
    graph = _build(_project(tmp_path, spec))
    assert graph.tasks["fit#baseline"].resources == {
        "cpus": 4,
        "memory": "6Gi",
        "time_limit": "1h30m",
    }
    assert graph.tasks["report#baseline"].resources == {}


@pytest.mark.parametrize(
    ("declaration", "expected"),
    [
        ("gpus: 1", {"gpus": 1}),
        ("disk: 10Gi", {"disk": "10Gi"}),
        ("cpus: 0.5", {"cpus": 0.5}),
        ("cpus: 0", {"cpus": 0}),
    ],
)
def test_execution_support_does_not_limit_graph_construction(
    tmp_path: Path, declaration: str, expected: dict[str, object],
) -> None:
    spec = _SPEC.replace(
        "command: python src/fit.py",
        f"resources: {{{declaration}}}\n      command: python src/fit.py",
    )
    task = _build(_project(tmp_path, spec)).tasks["fit#baseline"]
    assert task.resources == expected


def test_a_declared_input_resolves_to_its_path(tmp_path: Path) -> None:
    task = _build(_project(tmp_path)).tasks["fit#baseline"]
    assert task.inputs == {"catalog": tmp_path / "data" / "catalog.fits"}
    assert task.produced_by == {}
    assert "data/catalog.fits" in task.command


def test_an_input_another_output_produces_becomes_an_edge(tmp_path: Path) -> None:
    task = _build(_project(tmp_path)).tasks["report#baseline"]
    assert task.produced_by == {"fit": "fit#baseline"}
    assert task.depends_on == ("fit#baseline",)
    assert task.inputs == {"fit": tmp_path / "results" / "baseline" / "fit.json"}
    assert "results/baseline/fit.json" in task.command


def test_a_pinned_input_is_an_edge_to_that_universe(tmp_path: Path) -> None:
    """The entry is named as the output writes it, pin and all."""
    task = _build(_project(tmp_path, _TWO)).tasks["summary"]
    assert task.produced_by == {"fit#baseline": "fit#baseline"}


def test_an_analysis_astra_finds_invalid_is_refused_in_its_words(tmp_path: Path) -> None:
    """What makes an analysis invalid is ASTRA's to say: lc refuses before
    anything runs, with every broken rule ASTRA names, each at its place."""
    spec = _SPEC.replace("    format: json\n", "").replace("    format: md\n", "")
    with pytest.raises(ProjectError, match="(?s)does not validate.*MISSING_FIELD") as raised:
        _build(_project(tmp_path, spec))
    assert "fit" in str(raised.value) and "report" in str(raised.value)


def test_what_astra_notes_is_kept_for_the_run_to_report(tmp_path: Path) -> None:
    """A command that runs code from outside its analysis breaks no rule,
    and a run would otherwise live with it silently."""
    spec = _SPEC.replace("python src/report.py", "python ../elsewhere/report.py")
    notes = _build(_project(tmp_path, spec)).notes
    assert any("elsewhere" in note for note in notes)


# ---- ordering, closure, targets --------------------------------------------


def test_each_task_comes_after_the_tasks_it_reads(tmp_path: Path) -> None:
    order = list(_build(_project(tmp_path)).tasks)
    assert order.index("fit#baseline") < order.index("report#baseline")
    assert order.index("fit#baseline") < order.index("summary")


def test_asking_for_an_output_asks_for_what_it_is_made_of(tmp_path: Path) -> None:
    graph = _build(_project(tmp_path))
    closure = graph.closure(graph.resolve(["report"]))
    assert list(closure.tasks) == ["fit#baseline", "report#baseline"]


def test_a_bare_target_means_every_universe(tmp_path: Path) -> None:
    graph = _build(_project(tmp_path, _TWO))
    assert sorted(graph.resolve(["fit"])) == ["fit#alternative", "fit#baseline"]


def test_a_task_target_means_exactly_one(tmp_path: Path) -> None:
    graph = _build(_project(tmp_path, _TWO))
    assert graph.resolve(["fit#baseline"]) == ["fit#baseline"]


def test_an_unknown_target_is_an_error_not_an_empty_run(tmp_path: Path) -> None:
    """Quietly making nothing is the least useful thing a build tool can
    do, so the message lists what there was."""
    graph = _build(_project(tmp_path))
    with pytest.raises(ProjectError, match="no result matches `typo`.*fit#baseline"):
        graph.resolve(["typo"])


# ---- refusals --------------------------------------------------------------


def test_no_spec_is_a_clean_error(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="no astra.yaml"):
        _build(tmp_path)


def test_an_analysis_that_declares_another_is_refused_by_name(tmp_path: Path) -> None:
    """Its results would run in another folder, under names lc does not
    place — refused whole rather than run in part."""
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "astra.yaml").write_text('spec_version: "0.1.0"\n')
    spec = _SPEC.replace('label: demo', "label: demo\nanalyses:\n  stage: {path: stage}")
    with pytest.raises(ProjectError, match="declares another analysis \\(`stage`\\)"):
        _build(_project(tmp_path, spec))


def test_an_analysis_with_no_universe_builds_what_is_built_once(tmp_path: Path) -> None:
    spec = """
    spec_version: "0.1.0"
    outputs:
      hello:
        type: report
        format: txt
        recipe:
          command: echo hello > {output}
    """
    graph = _build(_project(tmp_path, spec))
    assert list(graph.tasks) == ["hello"]


# ---- ASTRA's checker's report, kept beside the tasks ------------------------


def test_every_task_has_what_the_checker_found_of_it(tmp_path: Path) -> None:
    """One status per task: the state a run starts from, read once and
    handed to whoever classifies. Before any run that is ASTRA's
    `missing`, with the rule broken said in its own words."""
    graph = _build(_project(tmp_path))
    assert set(graph.checked.statuses) == set(graph.tasks)
    for found in graph.checked.statuses.values():
        assert (found.state, found.record) == ("missing", None)
        assert [broken.code for broken in found.why] == ["MISSING_RECORD"]


def test_the_graph_asks_astra_for_the_analysis_and_its_status_once_each(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the results are and how each stands are two answers, each asked
    once: validity comes with either, so nothing asks for it."""
    asked: list[str] = []
    resolve, status = api.resolve, api.status

    def resolved(path: Path) -> api.ResolvedAnalysis:
        asked.append("resolve")
        return resolve(path)

    def checked(path: Path) -> api.StatusReport:
        asked.append("status")
        return status(path)

    def refused(*args: object, **kwargs: object) -> None:
        raise AssertionError("the graph is read through `resolve` and `status` alone")

    monkeypatch.setattr(api, "resolve", resolved)
    monkeypatch.setattr(api, "status", checked)
    monkeypatch.setattr(api, "validate", refused)
    root = _project(tmp_path)

    assert _build(root).tasks
    assert asked == ["resolve", "status"]


def test_statuses_read_at_another_commit_than_the_analysis_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each answer names the commit it was read at: where a commit landed
    between the two, the statuses are not of the results resolved."""
    from dataclasses import replace

    status = api.status
    monkeypatch.setattr(api, "status", lambda path: replace(status(path), head="0" * 40))

    with pytest.raises(ProjectError, match="moved from .* while lc was reading"):
        _build(_project(tmp_path))


def test_narrowing_keeps_the_checkers_report(tmp_path: Path) -> None:
    graph = _build(_project(tmp_path))
    closure = graph.closure(["summary"])
    assert list(closure.tasks) == ["fit#baseline", "summary"]
    assert closure.checked is graph.checked


# ---- where a result stands -------------------------------------------------
#
# What a record still describes is ASTRA's checker's to say, so the rule is
# driven with the statuses that checker returns, built by hand; what is
# pinned is what lc makes of them. Every case names the environment it is
# classified against, because that argument is what decides `behind`.

_ENV = "urn:lightcone:env:sha256:env"

_AT = api.Location(".astra/records/baseline/best_fit.json", "", None, None)

#: What the record says the recipe read of the result it reads, and the
#: same result made again with other bytes.
_READ: Digest = ("sha256:summary", 9)
_OTHER: Digest = ("sha256:other", 9)

_OTHER_BYTES = api.Diagnostic(
    "ABSENT_RESULT",
    "warning",
    "the bytes at results/baseline/best_fit.txt aren't the ones its record gives",
    _AT,
)


def _record(environment: str = _ENV) -> api.ProvenanceRecord:
    return api.ProvenanceRecord(
        "best_fit#baseline",
        "sha256:data",
        8,
        api.Generation(
            "c" * 40,
            api.Environment(environment),
            api.Runner("lightcone-cli", "1.0"),
            (api.RecordedInput("summary", *_READ),),
        ),
    )


def _unchanged(environment: str = _ENV, *, fetched: bool = True) -> api.Status:
    """A result as its run left it, its bytes here or, on a clone that has
    not fetched them, stood in for by git-annex — which ASTRA reads as the
    recorded bytes, with no diagnostic."""
    held = api.Bytes("present") if fetched else api.Bytes("absent", "git-annex")
    return api.Status("unchanged", held, _record(environment))


def _changed(*messages: str, environment: str = _ENV) -> api.Status:
    """Stale in itself: something of its own declaration moved."""
    why = tuple(api.Diagnostic("STALE_RESULT", "error", message, _AT) for message in messages)
    return api.Status("stale", api.Bytes("present"), _record(environment), why)


#: Stale only through what it reads: ASTRA found `summary` not unchanged,
#: which is no rule this result breaks — so nothing is in `why`.
_READS_STALE = api.Status("stale", api.Bytes("present"), _record(), (), ("summary",))

_MISSING = api.Status(
    "missing",
    api.Bytes("absent"),
    None,
    (api.Diagnostic("MISSING_RECORD", "error", "no record is at the place", _AT),),
)


def test_a_result_with_no_record_is_missing_and_made() -> None:
    verdict = classify(found=_MISSING, environment=_ENV)
    assert (verdict.state, verdict.why) == ("missing", "no record is at the place")
    assert verdict.calls_for_a_remake(refresh=False)


def test_an_unchanged_result_is_left_alone() -> None:
    verdict = classify(found=_unchanged(), environment=_ENV)
    assert (verdict.state, verdict.why) == ("unchanged", "")
    assert not verdict.calls_for_a_remake(refresh=False)
    assert not verdict.calls_for_a_remake(refresh=True)


def test_a_result_astra_finds_changed_is_stale_in_astras_words() -> None:
    """Every reason, because a result can have moved in more than one way
    and the user fixes them in one pass."""
    verdict = classify(
        found=_changed("the command changed", "`catalog` changed"), environment=_ENV
    )
    assert (verdict.state, verdict.why) == ("stale", "the command changed; `catalog` changed")


def test_one_reason_found_at_two_places_is_said_once() -> None:
    """A result with two names has one status, and ASTRA says there what
    it found at each place — the same sentence twice, for the same edit."""
    found = _changed("the command changed", "the command changed")
    assert classify(found=found, environment=_ENV).why == "the command changed"


def test_bytes_this_clone_has_not_fetched_change_nothing() -> None:
    """A clone that has fetched no annex content holds stand-ins for
    results that are perfectly up to date, and a run fetches them."""
    verdict = classify(found=_unchanged(fetched=False), environment=_ENV)
    assert (verdict.state, verdict.unfetchable) == ("unchanged", False)


def test_bytes_nothing_can_fetch_are_made_again_and_change_no_state() -> None:
    """ASTRA only warns: the record breaks no rule, so the result stands as
    the checker says. What is at its place is not what the record
    describes, though — written over after its run, or never there — and
    no clone can fetch the bytes it names, so a run makes them again."""
    found = api.Status("unchanged", api.Bytes("absent"), _record(), (_OTHER_BYTES,))
    verdict = classify(found=found, environment=_ENV)
    assert (verdict.state, verdict.why) == ("unchanged", _OTHER_BYTES.message)
    assert verdict.unfetchable
    assert verdict.calls_for_a_remake(refresh=False)


def test_another_warning_is_no_reason_to_remake() -> None:
    """What stales is a broken rule; anything else ASTRA notes beside it
    is not lc's to act on."""
    noted = api.Diagnostic("SOME_NOTE", "warning", "worth knowing", _AT)
    found = api.Status("unchanged", api.Bytes("present"), _record(), (noted,))
    verdict = classify(found=found, environment=_ENV)
    assert (verdict.state, verdict.why) == ("unchanged", "")
    assert not verdict.calls_for_a_remake(refresh=False)


# ---- what it reads -----------------------------------------------------------
#
# ASTRA's checker says which entries read a result that is not unchanged, or
# that was made again with other bytes than the record gives: stale through
# them, with nothing of its own in `why`. Once a run has made them, their
# bytes are known, and a result that reads what its record gives is
# unchanged again. That is what stops a byte-identical rebuild cascading.


def test_a_result_stale_through_what_it_reads_says_which_entry() -> None:
    """And that this is all that is wrong with it: a run that makes
    `summary` again may leave it standing, so its bytes may still be
    read."""
    verdict = classify(found=_READS_STALE, environment=_ENV)
    assert (verdict.state, verdict.why) == (
        "stale",
        "`summary` has changed since this result read it",
    )
    assert verdict.through_reads


def test_a_rebuild_that_gives_the_recorded_bytes_leaves_the_result_standing() -> None:
    """The cascade stops here: `summary` was stale, was remade, and came
    out as the bytes this result's record says it read."""
    verdict = classify(found=_READS_STALE, environment=_ENV, read={"summary": _READ})
    assert verdict.state == "unchanged"
    assert not verdict.calls_for_a_remake(refresh=True)


def test_a_rebuild_that_gives_other_bytes_makes_the_result_stale() -> None:
    """Worded from the bytes it has now — what ASTRA found of `summary`
    before the run is no longer what is wrong."""
    verdict = classify(found=_READS_STALE, environment=_ENV, read={"summary": _OTHER})
    assert (verdict.state, verdict.why) == (
        "stale",
        "`summary` was remade with other bytes than this result read",
    )


def test_an_unchanged_result_is_stale_once_what_it_reads_comes_out_differently() -> None:
    """ASTRA found nothing wrong, and nothing was: `summary` was remade
    because a run asked for it, and no longer gives what was read."""
    verdict = classify(found=_unchanged(), environment=_ENV, read={"summary": _OTHER})
    assert verdict.state == "stale"
    assert classify(found=_unchanged(), environment=_ENV, read={"summary": _READ}).state == (
        "unchanged"
    )


def test_the_size_read_is_compared_as_well_as_the_hash() -> None:
    """A record gives both of what was read, and both are what was read."""
    resized = (_READ[0], _READ[1] + 1)
    verdict = classify(found=_unchanged(), environment=_ENV, read={"summary": resized})
    assert verdict.state == "stale"


def test_an_entry_the_record_never_listed_was_not_read_as_it_now_is() -> None:
    """Whatever bytes it has, they are not bytes this result's record
    says its recipe read."""
    verdict = classify(found=_unchanged(), environment=_ENV, read={"other": _READ})
    assert verdict.why == "`other` was remade with other bytes than this result read"


@pytest.mark.parametrize("read", [None, {}, {"summary": _READ}])
def test_a_missing_result_is_missing_whatever_it_reads(read: dict[str, Digest] | None) -> None:
    verdict = classify(found=_MISSING, environment=_ENV, read=read)
    assert (verdict.state, verdict.why) == ("missing", "no record is at the place")


def test_its_own_change_is_said_beside_what_it_reads() -> None:
    """Both are said: one is fixed in the spec, the other by the run."""
    both = api.Status(
        "stale",
        api.Bytes("present"),
        _record(),
        _changed("the command changed").why,
        ("summary",),
    )
    verdict = classify(found=both, environment=_ENV)
    assert verdict.why == "the command changed; `summary` has changed since this result read it"
    assert not verdict.through_reads
    assert classify(found=both, environment=_ENV, read={"summary": _OTHER}).why == (
        "the command changed; `summary` was remade with other bytes than this result read"
    )


# ---- behind, which is the whole point of the split -------------------------


def test_a_moved_environment_is_behind_and_not_stale() -> None:
    """The headline. One added dependency rewrites `uv.lock` for the whole
    project, and folding that into the rebuild trigger is what made a
    week-old result disappear over a plotting library."""
    verdict = classify(found=_unchanged("urn:lightcone:env:sha256:old"), environment=_ENV)
    assert (verdict.state, verdict.why) == ("behind", "made under an earlier environment")
    assert not verdict.calls_for_a_remake(refresh=False)
    assert verdict.calls_for_a_remake(refresh=True)


def test_behind_is_decided_whether_or_not_the_bytes_are_here() -> None:
    """A clone with no content still knows which environment made what."""
    found = _unchanged("urn:lightcone:env:sha256:old", fetched=False)
    assert classify(found=found, environment=_ENV).state == "behind"


def test_reading_what_was_recorded_leaves_a_behind_result_behind() -> None:
    """The bytes of what it reads settle `stale`; the environment is a
    separate question, asked after."""
    old = _record("urn:lightcone:env:sha256:old")
    found = api.Status("stale", api.Bytes("present"), old, (), ("summary",))
    assert classify(found=found, environment=_ENV, read={"summary": _READ}).state == "behind"


def test_stale_wins_over_behind() -> None:
    """Both moved. The result has to be remade either way, so the reason
    reported is the one that calls for the work — a `behind` verdict here
    would say "left alone" about something about to run."""
    verdict = classify(
        found=_changed("the command changed", environment="urn:lightcone:env:sha256:old"),
        environment=_ENV,
    )
    assert verdict.state == "stale"
    assert verdict.calls_for_a_remake(refresh=False)


def test_bytes_nothing_can_fetch_are_said_beside_what_it_reads() -> None:
    """Stale through what it reads, and its bytes lost besides: a rebuild
    upstream that gives the recorded bytes would not leave it standing,
    so it is not stale through what it reads alone."""
    found = api.Status(
        "stale", api.Bytes("absent"), _record(), (_OTHER_BYTES,), ("summary",)
    )
    verdict = classify(found=found, environment=_ENV)
    assert verdict.why == (
        f"`summary` has changed since this result read it; {_OTHER_BYTES.message}"
    )
    assert verdict.unfetchable and not verdict.through_reads


def test_bytes_nothing_can_fetch_are_said_beside_behind() -> None:
    found = api.Status(
        "unchanged",
        api.Bytes("absent"),
        _record("urn:lightcone:env:sha256:old"),
        (_OTHER_BYTES,),
    )
    verdict = classify(found=found, environment=_ENV)
    assert verdict.state == "behind"
    assert verdict.why == f"made under an earlier environment; {_OTHER_BYTES.message}"
    assert verdict.calls_for_a_remake(refresh=False)
