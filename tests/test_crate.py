"""Tests for `lightcone.engine.crate` — the publication view, rendered pure.

Everything here is disk-only: fixture results under ``tmp_path``, a
hand-built graph holding what ASTRA's checker would find of each, and a
stub for "who last wrote this path" — no git, no subprocess. Structure
and ordering are asserted, never byte goldens; the one byte-level claim
is determinism, because convergence-by-materialize rests on rendering
twice at the same state yielding identical text.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from astra import api

from lightcone.engine import assets, crate
from lightcone.engine.dataset import LastWrite
from lightcone.engine.plan import Graph, Key, Task

_DSID = "4b7b5c1e-0000-4000-8000-000000000000"
_RUN = "a" * 40
_ENVIRONMENT = "urn:lightcone:env:sha256:" + "e" * 64

#: What describes `first` in `baseline`.
_RECORD = ".astra/records/baseline/first.json"

Writer = Callable[..., LastWrite]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    (root / "data").mkdir(parents=True)
    (root / "astra.yaml").write_text('spec_version: "0.1.0"\n')
    (root / "pyproject.toml").write_text('[project]\nname = "demo"\nlicense = "MIT"\n')
    (root / "uv.lock").write_text("version = 1\n")
    (root / ".python-version").write_text("3.12.5\n")
    (root / "data" / "catalog.csv").write_text("a,b\n")
    return root


def _task(
    root: Path,
    name: str,
    universe: str = "",
    *,
    reads: dict[str, str] | None = None,
    made_by: dict[str, Key] | None = None,
    decisions: dict[str, str] | None = None,
) -> Task:
    """A task as `plan.build` makes one: ASTRA's result, and what lc looks
    up for it. *made_by* names the entries of *reads* another task
    produces; the others read a declared input."""
    place = api.Place(
        f"{name}#{universe}" if universe else name,
        str(Path(".astra", "records", universe, f"{name}.json")),
        str(Path("results", universe, f"{name}.txt")),
    )
    reads, made_by = reads or {}, made_by or {}
    result = api.Result(
        place.address,
        f"outputs.{name}",
        (place,),
        f"universes.{universe}" if universe else None,
        tuple(
            api.Source(entry, result=made_by[entry])
            if entry in made_by
            else api.Source(entry, input=f"inputs.{entry}")
            for entry in reads
        ),
        f"make {name}",
    )
    return Task(
        result,
        root / place.file_path,
        (root / place.record_path,),
        {entry: root / path for entry, path in reads.items()},
        {"decisions.method": "alpha"} if decisions is None else decisions,
    )


def _missing(task: Task) -> api.Status:
    """What the checker finds of a result nobody made."""
    place = task.result.places[0]
    broken = api.Diagnostic(
        "MISSING_RECORD",
        "error",
        f"no record is at {place.record_path}",
        api.Location(place.record_path, "", None, None),
    )
    return api.Status("missing", "absent", why=(broken,))


def _made(
    root: Path,
    graph: Graph,
    key: Key,
    *,
    commit: str = _RUN,
    finished: str = "2026-08-19T10:05:00.000Z",
) -> None:
    """Leave what one run of a task leaves — bytes and record — and, in
    the graph, what ASTRA's checker then finds of it."""
    task = graph.tasks[key]
    place = task.result.places[0]
    output = root / place.file_path
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(f"{place.address}\n")
    content_hash, size = assets.digest_file(output)
    run = api.Generation(
        commit,
        api.Environment(_ENVIRONMENT),
        api.Runner("lightcone-cli", "0.4.2"),
        tuple(
            api.RecordedInput(entry, *assets.digest_file(path))
            for entry, path in task.inputs.items()
        ),
        started="2026-08-19T10:00:00.000Z",
        finished=finished,
    )
    api.write_records(root, task.result, content_hash, size, run)
    record = api.ProvenanceRecord(place.address, content_hash, size, run)
    graph.found[key] = api.Status("unchanged", "present", record)


def _graph_of(*tasks: Task) -> Graph:
    """A graph of *tasks*, none of them made, titled as the spec titles it."""
    return Graph(
        {task.key: task for task in tasks},
        {task.key: _missing(task) for task in tasks},
        label="Demo analysis",
        description="A demo analysis.",
    )


def _graph(root: Path, universes: tuple[str, ...] = ("baseline",)) -> Graph:
    tasks: list[Task] = []
    for universe in universes:
        first = _task(root, "first", universe, reads={"catalog": "data/catalog.csv"})
        second = _task(
            root,
            "second",
            universe,
            reads={"first": first.result.places[0].file_path},
            made_by={"first": first.key},
        )
        tasks += [first, second]
    return _graph_of(*tasks)


def _materialized(root: Path, *keys: Key, universes: tuple[str, ...] = ("baseline",)) -> Graph:
    """The two-step graph, with *keys* (every task when none) already made."""
    graph = _graph(root, universes)
    for key in keys or graph.tasks:
        _made(root, graph, key)
    return graph


def _writer(*paths: str | Path) -> LastWrite:
    return LastWrite("a" * 40, "irrelevant", "Ada Lovelace", "ada@example.org", "2026-08-19")


def _recorded(graph: Graph) -> dict[Key, api.ProvenanceRecord]:
    """Every result the graph holds a record of — what the driver hands
    the crate where nothing is stale."""
    return {key: found.record for key, found in graph.found.items() if found.record is not None}


def _render(
    root: Path,
    graph: Graph,
    writer: Writer = _writer,
    keys: dict[str, str] | None = None,
    made: dict[Key, api.ProvenanceRecord] | None = None,
) -> dict[str, Any]:
    text = crate.render(
        root,
        graph,
        _recorded(graph) if made is None else made,
        license="MIT",
        dsid=_DSID,
        writer=writer,
        keys=keys or {},
    )
    loaded = json.loads(text)
    assert isinstance(loaded, dict)
    return loaded


def _entities(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entity["@id"]: entity for entity in document["@graph"]}


# ---- determinism, the property convergence rests on ------------------------


def test_rendering_twice_at_the_same_state_is_byte_identical(project: Path) -> None:
    graph = _materialized(project)

    keys = {"results/baseline/first.txt": "SHA256E-s24--" + "c" * 64 + ".txt"}
    first, second = (
        crate.render(
            project, graph, _recorded(graph), license="MIT", dsid=_DSID, writer=_writer, keys=keys
        )
        for _ in range(2)
    )

    assert first == second


def test_a_single_value_is_never_a_singleton_array(project: Path) -> None:
    """Compacted JSON-LD: one author, one parameter, one result are plain
    values — RO-Crate 1.1's recommendation, and what its validator checks.
    The arrays come from three builders, so this pins the serialization."""
    graph = _materialized(project, "first#baseline")
    document = _render(project, graph)
    for entity in document["@graph"]:
        for name, value in entity.items():
            assert not (isinstance(value, list) and len(value) == 1), (entity["@id"], name)
    entities = _entities(document)
    assert entities["./"]["author"] == {"@id": "mailto:ada@example.org"}


def test_the_clock_never_enters_the_document(project: Path) -> None:
    """`datePublished` is the newest recorded instant — rocrate's own
    default stamps the current time, and this pins the override."""
    graph = _materialized(project, "first#baseline")
    _made(project, graph, "second#baseline", finished="2026-08-20T09:00:00.000Z")

    root_entity = _entities(_render(project, graph))["./"]

    assert root_entity["datePublished"] == "2026-08-20T09:00:00.000Z"


# ---- the workflow-run context and the profiles -----------------------------


def test_the_context_carries_the_workflow_run_vocabulary(project: Path) -> None:
    """Without it, `sha256` is an undefined term that JSON-LD silently
    drops on expansion — typed but inert."""
    graph = _materialized(project, "first#baseline")
    document = _render(project, graph)

    assert "https://w3id.org/ro/terms/workflow-run" in document["@context"]


def test_the_crate_version_is_pinned_not_rocrates_default(project: Path) -> None:
    """rocrate 0.16 moved its default to 1.3, whose context maps
    `ComputationalWorkflow` to a term the workflow profiles do not
    recognise — so the version is ours, and both halves must agree."""
    graph = _materialized(project, "first#baseline")
    document = _render(project, graph)
    entities = _entities(document)

    assert "https://w3id.org/ro/crate/1.1/context" in document["@context"]
    claimed = {ref["@id"] for ref in entities["ro-crate-metadata.json"]["conformsTo"]}
    assert claimed == {
        "https://w3id.org/ro/crate/1.1",
        "https://w3id.org/workflowhub/workflow-ro-crate/1.0",
    }


def test_the_root_conforms_to_every_claimed_profile_and_declares_each(
    project: Path,
) -> None:
    graph = _materialized(project, "first#baseline")
    entities = _entities(_render(project, graph))

    claimed = {ref["@id"] for ref in entities["./"]["conformsTo"]}
    assert "https://w3id.org/ro/wfrun/provenance/0.5" in claimed
    for profile in claimed:
        assert entities[profile]["@type"] == "CreativeWork"


# ---- the Provenance layer, structurally ------------------------------------


def test_one_step_per_output_and_one_action_per_universe(project: Path) -> None:
    """A step is spec structure and an action is one execution — which is
    exactly how a multiverse maps onto the Provenance profile."""
    graph = _materialized(project, universes=("baseline", "alt"))
    entities = _entities(_render(project, graph))

    steps = [e for e in entities.values() if e["@type"] == "HowToStep"]
    actions = [e for e in entities.values() if e["@type"] == "CreateAction"]
    workflow = entities["astra.yaml"]
    assert len(steps) == 2
    assert {ref["@id"] for ref in workflow["step"]} == {"#step-first", "#step-second"}
    # four tool-level actions, plus one workflow-level action for the run
    assert len(actions) == 5
    assert "HowTo" in workflow["@type"]


def test_outputs_sharing_a_commit_are_one_run(project: Path) -> None:
    """The driver reads HEAD once and hands it down, so a record's
    `commit` groups results into runs — the whole Provenance layer's
    identity."""
    graph = _materialized(project, "first#baseline")
    _made(project, graph, "second#baseline", commit="b" * 40)
    entities = _entities(_render(project, graph))

    organizers = {i: e for i, e in entities.items() if e["@type"] == "OrganizeAction"}
    assert set(organizers) == {"#organize-aaaaaaa", "#organize-bbbbbbb"}
    for organizer in organizers.values():
        run = entities[organizer["result"]["@id"]]
        assert run["@type"] == "CreateAction"
        assert run["instrument"]["@id"] == "astra.yaml"
    assert organizers["#organize-aaaaaaa"]["object"] == {"@id": "#control-baseline-first"}
    assert entities["#control-baseline-first"]["instrument"] == {"@id": "#step-first"}


def test_a_run_is_timed_and_attributed_from_its_records(project: Path) -> None:
    """Start, end and the engine are the ones the run recorded — never
    the clock's or the installed engine's, or two collaborators would
    re-commit the crate at each other forever."""
    graph = _materialized(project, "first#baseline")
    _made(project, graph, "second#baseline", finished="2026-08-20T09:00:00.000Z")
    entities = _entities(_render(project, graph))

    organizer = entities["#organize-aaaaaaa"]
    engine = entities[organizer["instrument"]["@id"]]
    assert engine["softwareVersion"] == "0.4.2"
    assert engine["@id"] == "https://pypi.org/project/lightcone-cli/0.4.2/"
    run = entities[organizer["result"]["@id"]]
    # A numeric offset, which is the form the Run Crate shapes read.
    assert run["startTime"] == "2026-08-19T10:00:00.000+00:00"
    assert run["endTime"] == "2026-08-20T09:00:00.000+00:00"
    assert _RUN in run["description"]


def test_an_action_chains_its_inputs_and_its_environment(project: Path) -> None:
    graph = _materialized(project)
    entities = _entities(_render(project, graph))

    actions = {e["name"]: e for e in entities.values() if e["@type"] == "CreateAction"}
    first = actions["run of `first` in universe `baseline`"]
    second = actions["run of `second` in universe `baseline`"]
    first_objects = {ref["@id"] for ref in first["object"]}
    assert {"uv.lock", ".python-version", "pyproject.toml", "data/catalog.csv"} <= first_objects
    assert "results/baseline/first.txt" in {ref["@id"] for ref in second["object"]}
    assert second["result"] == {"@id": "results/baseline/second.txt"}
    assert second["description"] == graph.tasks["second#baseline"].recipe
    content_hash, _ = assets.digest_file(project / "results/baseline/second.txt")
    assert entities["results/baseline/second.txt"]["version"] == content_hash


def test_the_record_is_in_the_crate_and_about_its_result(project: Path) -> None:
    graph = _materialized(project, "first#baseline")
    entities = _entities(_render(project, graph))

    assert entities[_RECORD]["about"] == {"@id": "results/baseline/first.txt"}
    assert entities[_RECORD]["encodingFormat"] == "application/json"
    result = entities["results/baseline/first.txt"]
    assert result["name"] == "first (universe baseline)"
    assert result["subjectOf"] == {"@id": _RECORD}


def test_a_result_without_a_valid_record_is_not_described_as_made(project: Path) -> None:
    """Bytes alone are not a run: the record is what says which commit,
    when and under what. Whether one is the result's own is ASTRA's to
    say, so where its checker found none there is no action to state —
    whatever file sits at the record's place."""
    graph = _materialized(project, "first#baseline")
    (project / ".astra/records/baseline/first.json").write_text('{"address": "first#baseline"}\n')
    graph.found["first#baseline"] = _missing(graph.tasks["first#baseline"])
    entities = _entities(_render(project, graph))

    assert "results/baseline/first.txt" not in entities
    assert not [e for e in entities.values() if e["@type"] == "CreateAction"]


def test_only_what_it_is_handed_as_made_is_described_as_made(project: Path) -> None:
    """Which results the project still bears out is the driver's to say
    (`materialize._published`). Any other is a step of the workflow, with
    no run and no file: its record describes something else."""
    graph = _materialized(project)
    made = _recorded(graph)

    def actions(handed: dict[Key, api.ProvenanceRecord]) -> int:
        entities = _entities(_render(project, graph, made=handed))
        return len([e for e in entities.values() if e["@type"] == "CreateAction"])

    del made["second#baseline"]

    assert actions(made) == actions(_recorded(graph)) - 1
    entities = _entities(_render(project, graph, made=made))
    assert "results/baseline/second.txt" not in entities
    assert any(e.get("name") == "second" for e in entities.values())


def test_a_result_built_once_has_no_universe_in_its_ids(project: Path) -> None:
    graph = _graph_of(_task(project, "summary", decisions={}))
    _made(project, graph, "summary")
    entities = _entities(_render(project, graph))

    result = entities["results/summary.txt"]
    assert result["name"] == "summary"
    assert result["subjectOf"] == {"@id": ".astra/records/summary.json"}
    control = entities["#control-summary"]
    assert control["instrument"] == {"@id": "#step-summary"}
    assert entities[control["object"]["@id"]]["name"] == "run of `summary`"


def test_the_person_is_the_saving_commits_author(project: Path) -> None:
    """The record's `commit` is the one the run *started* at — the
    author comes from the commit that saved the output, via the writer."""
    graph = _materialized(project, "first#baseline")
    entities = _entities(_render(project, graph))

    person = entities["mailto:ada@example.org"]
    assert person["name"] == "Ada Lovelace"
    action = next(e for e in entities.values() if e["@type"] == "CreateAction")
    assert action["agent"] == {"@id": "mailto:ada@example.org"}
    assert {"@id": "mailto:ada@example.org"} in _as_list(entities["./"]["author"])


def test_decision_values_point_back_at_their_parameter(project: Path) -> None:
    graph = _materialized(project, "first#baseline")
    entities = _entities(_render(project, graph))

    value = entities["#value-baseline-first-decisions.method"]
    assert (value["name"], value["value"]) == ("decisions.method", "alpha")
    assert value["exampleOfWork"] == {"@id": "#param-decisions.method"}
    workflow = entities["astra.yaml"]
    assert {"@id": "#param-decisions.method"} in _as_list(workflow["input"])
    assert entities["#param-decisions.method"]["name"] == "decisions.method"


def test_a_never_materialized_project_still_describes_its_workflow(project: Path) -> None:
    """No outputs, no runs — the crate is the workflow and the environment,
    dated by the spec's own last commit rather than the clock, and titled
    by the spec's `label`."""
    entities = _entities(_render(project, _graph(project)))

    assert entities["./"]["datePublished"] == "2026-08-19"
    assert not [e for e in entities.values() if e["@type"] == "CreateAction"]
    assert entities["astra.yaml"]["name"] == "Demo analysis"
    assert entities["./"]["name"] == "Demo analysis"
    assert entities["./"]["description"] == "A demo analysis."


def test_an_analysis_that_gives_no_label_is_titled_by_its_folder(project: Path) -> None:
    """Its words about itself come with the graph, as ASTRA read them; the
    crate invents a title only where the file gave none."""
    graph = Graph({}, {})
    entities = _entities(_render(project, graph))

    assert entities["./"]["name"] == "demo"
    assert "demo" in entities["./"]["description"]
    assert "description" not in entities["astra.yaml"]


def test_the_license_is_a_local_entity_never_a_minted_url(project: Path) -> None:
    """The declaration is not validated against the SPDX list, so a minted
    spdx.org URL could be a fabricated dead link — a local CreativeWork
    carries the declared string instead, and a URL license is used as
    given."""
    entities = _entities(_render(project, _graph(project)))

    assert entities["./"]["license"] == {"@id": "#license"}
    assert entities["#license"] == {
        "@id": "#license",
        "@type": "CreativeWork",
        "name": "MIT",
    }


def test_nothing_is_read_from_the_clones_own_configuration(project: Path) -> None:
    """Two clones of one commit render one document, whatever `origin`
    each was cloned from: a URL read off the clone would make each commit
    the crate at the other's, and publish whichever clone ran last. A
    recipe is named under the dataset's own id instead."""
    entities = _entities(_render(project, _materialized(project)))

    assert "url" not in entities["astra.yaml"]
    tools = [e for e in entities.values() if e.get("@type") == "SoftwareApplication"]
    recipes = [t for t in tools if t["name"] in ("first", "second")]
    assert len(recipes) == 2
    assert all(t["@id"].startswith("urn:uuid:") and "url" not in t for t in recipes)


def test_an_output_carries_the_checksum_from_its_annex_key(project: Path) -> None:
    """The keys are repository state, so a bytes-free clone renders the
    same claims — and the hex in a SHA256E key is the raw sha256 an
    archive can verify with `sha256sum` after a `git archive` deposit."""
    graph = _materialized(project, "first#baseline")
    digest = "d" * 64
    keys = {"results/baseline/first.txt": f"SHA256E-s21--{digest}.txt"}
    entities = _entities(_render(project, graph, keys=keys))

    output = entities["results/baseline/first.txt"]
    assert output["@type"] == "File"
    assert output["sha256"] == digest
    assert output["contentSize"] == "21"


def test_an_output_is_one_file_not_a_dataset_of_parts(project: Path) -> None:
    """One output is one file, so there is nothing to enumerate — and its
    record hangs off it as `subjectOf` rather than as a part of it."""
    graph = _materialized(project, "first#baseline")
    entities = _entities(_render(project, graph))

    output = entities["results/baseline/first.txt"]
    assert "hasPart" not in output
    assert output["subjectOf"] == {"@id": _RECORD}


def test_a_non_sha256_key_yields_size_and_no_digest(project: Path) -> None:
    """`annex.backend` is the researcher's to set, and a wrong checksum
    is worse than none — the publish-neither discipline."""
    graph = _materialized(project, "first#baseline")
    keys = {"results/baseline/first.txt": "MD5E-s21--" + "e" * 32 + ".txt"}
    entities = _entities(_render(project, graph, keys=keys))

    part = entities["results/baseline/first.txt"]
    assert part["contentSize"] == "21"
    assert "sha256" not in part


def test_pointer_shaped_bytes_never_become_a_checksum(project: Path) -> None:
    """`annex_keys` answers empty for the whole repository whenever
    git-annex cannot answer at all, and an unlocked pointer file reads
    perfectly well — so the byte fallback re-checks the pointer shape
    instead of publishing a well-formed digest of the pointer text."""
    graph = _materialized(project, "first#baseline")
    pointer = "/annex/objects/SHA256E-s300--" + "a" * 64 + ".csv\n"
    (project / "data" / "catalog.csv").write_text(pointer)

    entities = _entities(_render(project, graph, keys={}))

    catalog = entities["data/catalog.csv"]
    assert "sha256" not in catalog
    assert "contentSize" not in catalog


def test_a_git_lfs_pointer_never_becomes_a_checksum(project: Path) -> None:
    """Git LFS leaves a small text file where content it has not fetched
    would be, and no annex key answers for it: hashed, it would publish a
    well-formed digest of three lines of text."""
    graph = _materialized(project)
    (project / "data" / "catalog.csv").write_text(
        "version https://git-lfs.github.com/spec/v1\n"
        "oid sha256:4d7a214614ab2935c943f9e0ff69d22eadbb8f32b1258daaa5e2ca24d17e2393\n"
        "size 12345\n"
    )

    entities = _entities(_render(project, graph))

    assert "sha256" not in entities["data/catalog.csv"]
    assert "contentSize" not in entities["data/catalog.csv"]


def test_git_carried_files_are_hashed_by_their_bytes(project: Path) -> None:
    """The lock and its companions are in git, so their working-tree
    bytes are the content — repository state, and the render stays
    pure."""
    import hashlib

    graph = _materialized(project, "first#baseline")
    entities = _entities(_render(project, graph))

    body = (project / "uv.lock").read_bytes()
    assert entities["uv.lock"]["sha256"] == hashlib.sha256(body).hexdigest()
    assert entities["uv.lock"]["contentSize"] == str(len(body))


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]
