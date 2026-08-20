"""Tests for `lightcone.engine.crate` — the publication view, rendered pure.

Everything here is disk-only: fixture manifests under ``tmp_path``, a
hand-built graph, and a stub for "who last wrote this path" — no git, no
subprocess. Structure and ordering are asserted, never byte goldens; the
one byte-level claim is determinism, because convergence-by-materialize
rests on rendering twice at the same state yielding identical text.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from lightcone.engine import assets, crate
from lightcone.engine.dataset import LastWrite
from lightcone.engine.plan import Graph, Key, Task

_DSID = "4b7b5c1e-0000-4000-8000-000000000000"

Writer = Callable[[Path], LastWrite]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    (root / "universes").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "astra.yaml").write_text("name: demo\ndescription: A demo analysis.\n")
    (root / "universes" / "baseline.yaml").write_text("id: baseline\n")
    (root / "universes" / "alt.yaml").write_text("id: alt\n")
    (root / "pyproject.toml").write_text('[project]\nname = "demo"\nlicense = "MIT"\n')
    (root / "uv.lock").write_text("version = 1\n")
    (root / ".python-version").write_text("3.12.5\n")
    (root / "data" / "catalog.csv").write_text("a,b\n")
    return root


def _made(
    root: Path,
    universe_id: str,
    output_id: str,
    *,
    git_sha: str,
    image: dict[str, str] | None = None,
    inputs: dict[str, str] | None = None,
    finished_at: str = "2026-08-19T10:05:00.000+00:00",
) -> Path:
    directory = root / "results" / universe_id / output_id
    directory.mkdir(parents=True)
    (directory / "out.txt").write_text(f"{universe_id}/{output_id}\n")
    assets.write(
        directory,
        assets.Manifest(
            output_id=output_id,
            universe_id=universe_id,
            recipe=f"make {output_id}",
            definition_version="sha256:def",
            env_version="sha256:env",
            data_version=f"sha256:{universe_id}-{output_id}",
            decisions={"method": "alpha"},
            input_versions=inputs or {},
            git_sha=git_sha,
            git_remote="https://github.com/example/demo.git",
            lc_version="0.4.2",
            hermeticity={"mechanism": "landlock"},
            started_at="2026-08-19T10:00:00.000+00:00",
            finished_at=finished_at,
            image=image,
        ),
    )
    return directory


def _graph(root: Path, universes: tuple[str, ...] = ("baseline",)) -> Graph:
    tasks: dict[Key, Task] = {}
    for universe_id in universes:
        first_dir = root / "results" / universe_id / "first"
        tasks[(universe_id, "first")] = Task(
            universe_id,
            "first",
            first_dir,
            "make first",
            {"catalog": root / "data" / "catalog.csv"},
            {},
            {"method": "alpha"},
            "sha256:def",
        )
        tasks[(universe_id, "second")] = Task(
            universe_id,
            "second",
            root / "results" / universe_id / "second",
            "make second",
            {"first": first_dir},
            {"first": (universe_id, "first")},
            {"method": "alpha"},
            "sha256:def",
        )
    return Graph(tasks)


def _writer(path: Path) -> LastWrite:
    return LastWrite("a" * 40, "irrelevant", "Ada Lovelace", "ada@example.org", "2026-08-19")


def _render(root: Path, graph: Graph, writer: Writer = _writer) -> dict[str, Any]:
    text = crate.render(root, graph, license="MIT", dsid=_DSID, writer=writer)
    loaded = json.loads(text)
    assert isinstance(loaded, dict)
    return loaded


def _entities(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entity["@id"]: entity for entity in document["@graph"]}


# ---- determinism, the property convergence rests on ------------------------


def test_rendering_twice_at_the_same_state_is_byte_identical(project: Path) -> None:
    _made(project, "baseline", "first", git_sha="aaa111")
    _made(project, "baseline", "second", git_sha="aaa111")
    graph = _graph(project)

    first = crate.render(project, graph, license="MIT", dsid=_DSID, writer=_writer)
    second = crate.render(project, graph, license="MIT", dsid=_DSID, writer=_writer)

    assert first == second


def test_the_clock_never_enters_the_document(project: Path) -> None:
    """`datePublished` is the newest recorded instant — rocrate's own
    default stamps the current time, and this pins the override."""
    _made(project, "baseline", "first", git_sha="aaa111")
    _made(
        project,
        "baseline",
        "second",
        git_sha="aaa111",
        finished_at="2026-08-20T09:00:00.000+00:00",
    )

    root_entity = _entities(_render(project, _graph(project)))["./"]

    assert root_entity["datePublished"] == "2026-08-20T09:00:00.000+00:00"


# ---- the workflow-run context and the profiles -----------------------------


def test_the_context_carries_the_workflow_run_vocabulary(project: Path) -> None:
    """Without it, `containerImage` and `sha256` are undefined terms that
    JSON-LD silently drops on expansion — typed but inert."""
    _made(project, "baseline", "first", git_sha="aaa111")
    document = _render(project, _graph(project))

    assert "https://w3id.org/ro/terms/workflow-run" in document["@context"]


def test_the_root_conforms_to_every_claimed_profile_and_declares_each(
    project: Path,
) -> None:
    _made(project, "baseline", "first", git_sha="aaa111")
    entities = _entities(_render(project, _graph(project)))

    claimed = {ref["@id"] for ref in entities["./"]["conformsTo"]}
    assert "https://w3id.org/ro/wfrun/provenance/0.5" in claimed
    for profile in claimed:
        assert entities[profile]["@type"] == "CreativeWork"


# ---- the Provenance layer, structurally ------------------------------------


def test_one_step_per_output_and_one_action_per_universe(project: Path) -> None:
    """A step is spec structure and an action is one execution — which is
    exactly how a multiverse maps onto the Provenance profile."""
    for universe_id in ("baseline", "alt"):
        _made(project, universe_id, "first", git_sha="aaa111")
        _made(project, universe_id, "second", git_sha="aaa111")
    entities = _entities(_render(project, _graph(project, ("baseline", "alt"))))

    steps = [e for e in entities.values() if e["@type"] == "HowToStep"]
    actions = [e for e in entities.values() if e["@type"] == "CreateAction"]
    workflow = entities["astra.yaml"]
    assert len(steps) == 2
    assert {ref["@id"] for ref in workflow["step"]} == {"#step-first", "#step-second"}
    # four tool-level actions, plus one workflow-level action for the run
    assert len(actions) == 5
    assert "HowTo" in workflow["@type"]


def test_outputs_sharing_a_commit_are_one_run(project: Path) -> None:
    """The driver reads HEAD once and hands it down, so `git_sha` groups
    manifests into runs — the whole Provenance layer's identity."""
    _made(project, "baseline", "first", git_sha="aaa111")
    _made(project, "baseline", "second", git_sha="bbb222")
    entities = _entities(_render(project, _graph(project)))

    organizers = {i: e for i, e in entities.items() if e["@type"] == "OrganizeAction"}
    assert set(organizers) == {"#organize-aaa111", "#organize-bbb222"}
    for organizer in organizers.values():
        run = entities[organizer["result"]["@id"]]
        assert run["@type"] == "CreateAction"
        assert run["instrument"]["@id"] == "astra.yaml"


def test_an_action_chains_its_inputs_and_its_environment(project: Path) -> None:
    _made(project, "baseline", "first", git_sha="aaa111", inputs={"catalog": "sha256:cafe"})
    _made(project, "baseline", "second", git_sha="aaa111")
    entities = _entities(_render(project, _graph(project)))

    actions = {
        e["name"]: e for e in entities.values() if e["@type"] == "CreateAction"
    }
    first = actions["run of `first` in universe `baseline`"]
    second = actions["run of `second` in universe `baseline`"]
    first_objects = {ref["@id"] for ref in first["object"]}
    assert {"uv.lock", ".python-version", "pyproject.toml", "data/catalog.csv"} <= first_objects
    assert entities["data/catalog.csv"]["sha256"] == "cafe"
    assert "results/baseline/first/" in {ref["@id"] for ref in second["object"]}
    assert second["result"] == [{"@id": "results/baseline/second/"}]
    assert second["description"] == "make second"
    assert entities["results/baseline/second/"]["version"] == "sha256:baseline-second"


def test_the_manifest_is_in_the_crate_and_about_its_dataset(project: Path) -> None:
    _made(project, "baseline", "first", git_sha="aaa111")
    entities = _entities(_render(project, _graph(project)))

    manifest = entities["results/baseline/first/.lightcone-manifest.json"]
    assert manifest["about"] == {"@id": "results/baseline/first/"}
    assert entities["results/baseline/first/"]["subjectOf"] == {
        "@id": "results/baseline/first/.lightcone-manifest.json"
    }


def test_a_containerized_output_names_its_committed_archive(project: Path) -> None:
    """Identity and payload as one entity: the archive file in the crate,
    carrying the config-blob id the execution pinned."""
    _made(
        project,
        "baseline",
        "first",
        git_sha="aaa111",
        image={
            "tag": "lc-env-x",
            "id": "sha256:beef",
            "archive": ".datalad/environments/lc-env-x/image",
            "arch": "amd64",
        },
    )
    entities = _entities(_render(project, _graph(project)))

    archive = entities[".datalad/environments/lc-env-x/image"]
    assert set(archive["@type"]) == {"File", "ContainerImage"}
    assert archive["sha256"] == "beef"
    action = next(e for e in entities.values() if e["@type"] == "CreateAction")
    assert action["containerImage"] == {"@id": ".datalad/environments/lc-env-x/image"}


def test_the_person_is_the_saving_commits_author(project: Path) -> None:
    """The manifest's `git_sha` is the commit the run *started* at — the
    author comes from the commit that saved the output, via the writer."""
    _made(project, "baseline", "first", git_sha="aaa111")
    entities = _entities(_render(project, _graph(project)))

    person = entities["mailto:ada@example.org"]
    assert person["name"] == "Ada Lovelace"
    action = next(e for e in entities.values() if e["@type"] == "CreateAction")
    assert action["agent"] == {"@id": "mailto:ada@example.org"}
    assert {"@id": "mailto:ada@example.org"} in entities["./"]["author"]


def test_decision_values_point_back_at_their_parameter(project: Path) -> None:
    _made(project, "baseline", "first", git_sha="aaa111")
    entities = _entities(_render(project, _graph(project)))

    value = entities["#value-baseline-first-method"]
    assert value["value"] == "alpha"
    assert value["exampleOfWork"] == {"@id": "#param-method"}
    workflow = entities["astra.yaml"]
    assert {"@id": "#param-method"} in _as_list(workflow["input"])


def test_a_never_materialized_project_still_describes_its_workflow(project: Path) -> None:
    """No outputs, no runs — the crate is the workflow and the environment,
    dated by the spec's own last commit rather than the clock."""
    entities = _entities(_render(project, _graph(project)))

    assert entities["./"]["datePublished"] == "2026-08-19"
    assert not [e for e in entities.values() if e["@type"] == "CreateAction"]
    assert "astra.yaml" in entities


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


def test_license_of_reads_every_spelling(tmp_path: Path) -> None:
    cases = {
        'license = "MIT"': "MIT",
        'license = { text = "BSD-3-Clause" }': "BSD-3-Clause",
        'license = { file = "LICENSE" }': "LICENSE",
        "": "",
    }
    for spelling, expected in cases.items():
        (tmp_path / "pyproject.toml").write_text(f'[project]\nname = "x"\n{spelling}\n')
        assert crate.license_of(tmp_path) == expected, spelling


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]
