"""Tests for `lightcone.engine.crate` — the publication view, rendered pure.

Everything here is disk-only: fixture records and manifests under
``tmp_path``, a hand-built graph, and a stub for "who last wrote this
path" — no git, no subprocess. Structure and ordering are asserted, never
byte goldens; the one byte-level claim is determinism, because
convergence-by-materialize rests on rendering twice at the same state
yielding identical text.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from astra import records
from astra.hashing import digest_file
from astra.universes import Place

from lightcone.engine import assets, crate
from lightcone.engine.dataset import LastWrite
from lightcone.engine.plan import Graph, Key, Task

_DSID = "4b7b5c1e-0000-4000-8000-000000000000"
_RUN = "a" * 40
_ENVIRONMENT = "urn:lightcone:env:sha256:" + "e" * 64

#: What describes `first` in `baseline`, in the order a result lists them.
_SIDECARS = [".astra/records/baseline/first.json", "results/baseline/.first.manifest.json"]

Writer = Callable[..., LastWrite]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    (root / "data").mkdir(parents=True)
    (root / "astra.yaml").write_text(
        'spec_version: "0.1.0"\nlabel: Demo analysis\ndescription: A demo analysis.\n'
    )
    (root / "pyproject.toml").write_text('[project]\nname = "demo"\nlicense = "MIT"\n')
    (root / "uv.lock").write_text("version = 1\n")
    (root / ".python-version").write_text("3.12.5\n")
    (root / "data" / "catalog.csv").write_text("a,b\n")
    return root


def _place(name: str, universe: str = "", folder: str = "") -> Place:
    """One result's place, laid out as ASTRA lays it out."""
    return Place(
        name,
        f"{name}#{universe}" if universe else name,
        str(Path(folder, ".astra", "records", universe, f"{name}.json")),
        str(Path(folder, "results", universe, f"{name}.txt")),
    )


def _task(
    name: str,
    universe: str = "",
    *,
    inputs: dict[str, str] | None = None,
    produced_by: dict[str, Key] | None = None,
    decisions: dict[str, str] | None = None,
    workdir: str = "",
) -> Task:
    place = _place(name, universe)
    return Task(
        place.address,
        (place,),
        f"make {name}",
        workdir,
        inputs or {},
        produced_by or {},
        {"method": "alpha"} if decisions is None else decisions,
    )


def _made(
    root: Path,
    task: Task,
    *,
    commit: str = _RUN,
    image: dict[str, str] | None = None,
    finished: str = "2026-08-19T10:05:00.000Z",
) -> None:
    """Leave what one run of *task* leaves: bytes, record and manifest."""
    place = task.places[0]
    output = root / place.bytes
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(f"{place.address}\n")
    digest = digest_file(output)
    records.write_record(
        root / place.record,
        records.Record(
            address=place.address,
            content_hash=digest.content_hash,
            size=digest.size,
            commit=commit,
            environment=_ENVIRONMENT,
            runner=records.Runner("lightcone-cli", "0.4.2"),
            started="2026-08-19T10:00:00.000Z",
            finished=finished,
        ),
    )
    assets.write(
        assets.manifest_path(root, place),
        assets.Manifest(
            recipe=task.command,
            decisions=dict(task.decisions),
            git_remote="https://github.com/example/demo.git",
            uv_version="0.7.0",
            hermeticity={"mechanism": "landlock"},
            image=image,
        ),
    )


def _graph(universes: tuple[str, ...] = ("baseline",)) -> Graph:
    tasks: dict[Key, Task] = {}
    for universe in universes:
        first = _task("first", universe, inputs={"catalog": "data/catalog.csv"})
        second = _task(
            "second",
            universe,
            inputs={"first": first.places[0].bytes},
            produced_by={"first": first.key},
        )
        tasks |= {first.key: first, second.key: second}
    return Graph(tasks)


def _materialized(root: Path, *keys: Key, universes: tuple[str, ...] = ("baseline",)) -> Graph:
    """The two-step graph, with *keys* (every task when none) already made."""
    graph = _graph(universes)
    for key in keys or graph.tasks:
        _made(root, graph.tasks[key])
    return graph


def _writer(*paths: str | Path) -> LastWrite:
    return LastWrite("a" * 40, "irrelevant", "Ada Lovelace", "ada@example.org", "2026-08-19")


def _render(
    root: Path,
    graph: Graph,
    writer: Writer = _writer,
    keys: dict[str, str] | None = None,
) -> dict[str, Any]:
    text = crate.render(
        root, graph, license="MIT", dsid=_DSID, writer=writer, keys=keys or {}
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
    first = crate.render(project, graph, license="MIT", dsid=_DSID, writer=_writer, keys=keys)
    second = crate.render(project, graph, license="MIT", dsid=_DSID, writer=_writer, keys=keys)

    assert first == second


def test_a_single_value_is_never_a_singleton_array(project: Path) -> None:
    """Compacted JSON-LD: one author, one parameter, one result are plain
    values — RO-Crate 1.1's recommendation, and what its validator checks.
    The arrays come from three builders, so this pins the serialization."""
    graph = _materialized(project, "first#baseline")
    rendered = crate.render(project, graph, license="MIT", dsid=_DSID, writer=_writer, keys={})
    document = json.loads(rendered)
    for entity in document["@graph"]:
        for name, value in entity.items():
            assert not (isinstance(value, list) and len(value) == 1), (entity["@id"], name)
    entities = _entities(document)
    assert entities["./"]["author"] == {"@id": "mailto:ada@example.org"}


def test_the_clock_never_enters_the_document(project: Path) -> None:
    """`datePublished` is the newest recorded instant — rocrate's own
    default stamps the current time, and this pins the override."""
    graph = _materialized(project, "first#baseline")
    _made(project, graph.tasks["second#baseline"], finished="2026-08-20T09:00:00.000Z")

    root_entity = _entities(_render(project, graph))["./"]

    assert root_entity["datePublished"] == "2026-08-20T09:00:00.000Z"


# ---- the workflow-run context and the profiles -----------------------------


def test_the_context_carries_the_workflow_run_vocabulary(project: Path) -> None:
    """Without it, `containerImage` and `sha256` are undefined terms that
    JSON-LD silently drops on expansion — typed but inert."""
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
    _made(project, graph.tasks["second#baseline"], commit="b" * 40)
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
    _made(project, graph.tasks["second#baseline"], finished="2026-08-20T09:00:00.000Z")
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
    assert second["description"] == "make second"
    made = digest_file(project / "results/baseline/second.txt")
    assert entities["results/baseline/second.txt"]["version"] == made.content_hash


def test_the_record_and_the_manifest_are_in_the_crate_and_about_their_result(
    project: Path,
) -> None:
    graph = _materialized(project, "first#baseline")
    entities = _entities(_render(project, graph))

    for described in _SIDECARS:
        assert entities[described]["about"] == {"@id": "results/baseline/first.txt"}
        assert entities[described]["encodingFormat"] == "application/json"
    result = entities["results/baseline/first.txt"]
    assert result["name"] == "first#baseline"
    assert result["subjectOf"] == [{"@id": described} for described in _SIDECARS]


def test_a_result_without_a_valid_record_is_not_described_as_made(project: Path) -> None:
    """Bytes and a manifest alone are not a run: the record is what says
    which commit, when and under what, so without one that validates
    there is no action to state."""
    graph = _materialized(project, "first#baseline")
    (project / ".astra/records/baseline/first.json").write_text('{"address": "first#baseline"}\n')
    entities = _entities(_render(project, graph))

    assert "results/baseline/first.txt" not in entities
    assert not [e for e in entities.values() if e["@type"] == "CreateAction"]


def test_a_result_built_once_has_no_universe_in_its_ids(project: Path) -> None:
    task = _task("summary", decisions={})
    _made(project, task)
    entities = _entities(_render(project, Graph({task.key: task})))

    result = entities["results/summary.txt"]
    assert result["name"] == "summary"
    assert {ref["@id"] for ref in result["subjectOf"]} == {
        ".astra/records/summary.json",
        "results/.summary.manifest.json",
    }
    control = entities["#control-summary"]
    assert control["instrument"] == {"@id": "#step-summary"}
    assert entities[control["object"]["@id"]]["name"] == "run of `summary`"


def test_a_declared_analysis_that_runs_recipes_brings_its_spec(project: Path) -> None:
    """A driven analysis's result belongs to the project, under the name
    the project gives it — and the spec holding its recipe is part of
    what the deposit must carry."""
    (project / "stage").mkdir()
    (project / "stage" / "astra.yaml").write_text('spec_version: "0.1.0"\n')
    task = _task("stage.out", "baseline", workdir="stage")
    _made(project, task)
    entities = _entities(_render(project, Graph({task.key: task})))

    assert entities["stage/astra.yaml"]["encodingFormat"] == "application/yaml"
    assert entities["results/baseline/stage.out.txt"]["name"] == "stage.out#baseline"
    assert entities["#control-baseline-stage.out"]["instrument"] == {"@id": "#step-stage.out"}


def test_a_containerized_output_names_its_committed_archive(project: Path) -> None:
    """Identity and payload as one entity: the archive file in the crate,
    carrying the config-blob id the execution pinned."""
    graph = _graph()
    _made(
        project,
        graph.tasks["first#baseline"],
        image={
            "tag": "lc-env-x",
            "id": "sha256:beef",
            "archive": ".datalad/environments/lc-env-x/image",
            "arch": "amd64",
        },
    )
    entities = _entities(_render(project, graph))

    archive = entities[".datalad/environments/lc-env-x/image"]
    assert set(archive["@type"]) == {"File", "ContainerImage"}
    assert archive["sha256"] == "beef"
    action = next(e for e in entities.values() if e["@type"] == "CreateAction")
    assert action["containerImage"] == {"@id": ".datalad/environments/lc-env-x/image"}


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

    value = entities["#value-baseline-first-method"]
    assert value["value"] == "alpha"
    assert value["exampleOfWork"] == {"@id": "#param-method"}
    workflow = entities["astra.yaml"]
    assert {"@id": "#param-method"} in _as_list(workflow["input"])


def test_a_recorded_decision_the_spec_dropped_exemplifies_nothing(project: Path) -> None:
    """The action states what ran — the manifest's recipe and decisions —
    whatever the spec says today; a decision no output lists any more
    has no parameter left to point at."""
    ran = _task("first", "baseline", decisions={"method": "alpha", "binning": "coarse"})
    _made(project, ran)
    entities = _entities(_render(project, _graph()))

    assert entities["#value-baseline-first-binning"]["value"] == "coarse"
    assert "exampleOfWork" not in entities["#value-baseline-first-binning"]
    assert "#param-binning" not in entities


def test_a_never_materialized_project_still_describes_its_workflow(project: Path) -> None:
    """No outputs, no runs — the crate is the workflow and the environment,
    dated by the spec's own last commit rather than the clock, and titled
    by the spec's `label`."""
    entities = _entities(_render(project, _graph()))

    assert entities["./"]["datePublished"] == "2026-08-19"
    assert not [e for e in entities.values() if e["@type"] == "CreateAction"]
    assert entities["astra.yaml"]["name"] == "Demo analysis"
    assert entities["./"]["name"] == "Demo analysis"
    assert entities["./"]["description"] == "A demo analysis."


def test_the_license_is_a_local_entity_never_a_minted_url(project: Path) -> None:
    """The declaration is not validated against the SPDX list, so a minted
    spdx.org URL could be a fabricated dead link — a local CreativeWork
    carries the declared string instead, and a URL license is used as
    given."""
    entities = _entities(_render(project, _graph()))

    assert entities["./"]["license"] == {"@id": "#license"}
    assert entities["#license"] == {
        "@id": "#license",
        "@type": "CreativeWork",
        "name": "MIT",
    }


# ---- per-file integrity ----------------------------------------------------


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
    record and manifest hang off it as `subjectOf` rather than as parts
    of it."""
    graph = _materialized(project, "first#baseline")
    entities = _entities(_render(project, graph))

    output = entities["results/baseline/first.txt"]
    assert "hasPart" not in output
    assert output["subjectOf"] == [{"@id": described} for described in _SIDECARS]


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
