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
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from conftest import _Inline

from lightcone.engine import assets, dataset, identity
from lightcone.engine import materialize as engine
from lightcone.engine.project import ProjectError, child_env
from lightcone.engine.worker import TaskResult

_SPEC = """
version: "0.0.13"
name: analysis

inputs:
  - id: catalog
    type: data
    source: data/catalog.fits

outputs:
  - id: first
    type: metric
    decisions: [method]
    recipe:
      command: echo {decisions.method} > {output}/value.txt

  - id: second
    type: report
    inputs: [first]
    recipe:
      command: cat {inputs.first}/value.txt > {output}/copy.txt

decisions:
  method:
    label: Method
    default: alpha
    options:
      alpha: {label: alpha}
      beta: {label: beta}
"""

_UNIVERSE = "id: baseline\ndecisions:\n  method: alpha\n"


@pytest.fixture
def root(analysis: Callable[..., Path]) -> Path:
    return analysis(_SPEC, universes={"baseline": _UNIVERSE})


def _commits(root: Path) -> int:
    return len(dataset._git(["log", "--oneline"], cwd=root).splitlines())


# ---- a run, end to end -----------------------------------------------------


def test_every_output_is_made_and_committed(root: Path, inline: None) -> None:
    before = _commits(root)

    report = engine.materialize(root, [])

    assert report.made == ["baseline/first", "baseline/second"]
    assert report.ok and not report.up_to_date
    assert (root / "results/baseline/second/copy.txt").read_text() == "alpha\n"
    assert _commits(root) == before + 2
    assert not dataset.status(root)


def test_an_output_and_its_manifest_land_in_one_commit(root: Path, inline: None) -> None:
    """One commit is one complete, self-describing materialization — the
    manifest can never come to describe different bytes."""
    engine.materialize(root, ["first"])

    committed = dataset._git(
        ["show", "--name-only", "--format=", "HEAD"], cwd=root
    ).split()
    assert sorted(committed) == [
        "results/baseline/first/.lightcone-manifest.json",
        "results/baseline/first/value.txt",
    ]


def test_the_bytes_go_to_the_annex_and_the_manifest_to_git(root: Path, inline: None) -> None:
    """What git records is the test: a pointer for content, the real thing
    for a manifest. The working tree looks the same either way now."""
    engine.materialize(root, ["first"])

    def blob(rel: str) -> str:
        return dataset._git(["cat-file", "-p", f"HEAD:{rel}"], cwd=root)

    assert blob("results/baseline/first/value.txt").startswith("/annex/objects/")
    assert blob("results/baseline/first/.lightcone-manifest.json").startswith("{")


def test_a_second_run_does_nothing_and_commits_nothing(root: Path, inline: None) -> None:
    engine.materialize(root, [])
    after_first = _commits(root)

    report = engine.materialize(root, [])

    assert report.made == []
    assert report.current == ["baseline/first", "baseline/second"]
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
    engine.materialize(root, [])
    after_first = _commits(root)
    first = (root / "results/baseline/first/value.txt").read_text()
    _move_the_environment(root)

    report = engine.materialize(root, [])

    assert report.made == []
    assert set(report.behind) == {"baseline/first", "baseline/second"}
    assert "earlier environment" in report.behind["baseline/first"]
    assert report.up_to_date, "behind is not out of date"
    assert _commits(root) == after_first + 1, "only the environment edit"
    assert (root / "results/baseline/first/value.txt").read_text() == first


def test_refresh_remakes_what_is_behind_and_commits_it(root: Path, inline: None) -> None:
    """The other half: the report is not the only thing on offer, and asking
    is one flag."""
    engine.materialize(root, [])
    _move_the_environment(root)
    before = _commits(root)

    report = engine.materialize(root, [], refresh=True)

    assert set(report.made) == {"baseline/first", "baseline/second"}
    assert report.behind == {}
    assert _commits(root) == before + 2
    manifest = assets.read(root / "results/baseline/first")
    assert manifest is not None
    assert manifest.env_version == identity.env_version(root)


def test_the_manifest_records_the_uv_that_converged_the_environment(
    root: Path, inline: None
) -> None:
    """Probed once by the driver and handed to every task — attestation
    beside lc_version, never a rebuild signal."""
    from lightcone.engine import project

    engine.materialize(root, ["first"])

    manifest = assets.read(root / "results/baseline/first")
    assert manifest is not None
    assert manifest.uv_version == project.uv_version(root)
    assert manifest.uv_version.count(".") >= 1, "a real version token, not prose"


def test_check_reports_behind_without_planning_it(root: Path, inline: None) -> None:
    """`--check` is a gate, and `behind` must not close it — a project of
    curated results would never pass again."""
    engine.materialize(root, [])
    _move_the_environment(root)

    report = engine.check(root, [])

    assert report.planned == {}
    assert set(report.behind) == {"baseline/first", "baseline/second"}
    assert report.up_to_date


def test_check_with_refresh_plans_what_is_behind(root: Path, inline: None) -> None:
    engine.materialize(root, [])
    _move_the_environment(root)

    report = engine.check(root, [], refresh=True)

    assert set(report.planned) == {"baseline/first", "baseline/second"}
    assert report.behind == {}
    assert not report.up_to_date


def test_a_stale_output_is_stale_even_when_the_environment_also_moved(
    root: Path, inline: None
) -> None:
    """Both moved, and only one of them calls for work. Reporting `behind`
    here would say "left alone" about something the next run will remake."""
    engine.materialize(root, [])
    _move_the_environment(root)
    (root / "universes" / "baseline.yaml").write_text(
        "id: baseline\ndecisions:\n  method: beta\n"
    )
    dataset.save(root, [root], "switch method")

    report = engine.check(root, [])

    assert "the recipe or its decisions" in report.planned["baseline/first"]
    assert "baseline/first" not in report.behind


# ---- lc status -------------------------------------------------------------


def test_status_names_the_commit_each_output_came_from(root: Path, inline: None) -> None:
    """The verb's whole reason to exist: an output that is behind is not
    wrong, and this is where the code that produced it can be read back."""
    ran_against = dataset.head(root)[0]
    engine.materialize(root, [])

    report = engine.status(root)

    assert [o.output for o in report.outputs] == ["baseline/first", "baseline/second"]
    assert all(o.status == "current" for o in report.outputs)
    # The commit the tree was at when the run *started* — the code that
    # produced the output, not the commit the run itself went on to make.
    assert all(o.git_sha == ran_against for o in report.outputs)
    assert report.counts == {"current": 2, "behind": 0, "stale": 0}


def test_status_reports_behind_after_the_environment_moves(
    root: Path, inline: None
) -> None:
    made_at = dataset.head(root)[0]
    engine.materialize(root, [])
    _move_the_environment(root)

    report = engine.status(root)

    assert report.counts == {"current": 0, "behind": 2, "stale": 0}
    assert all(o.git_sha == made_at for o in report.outputs), "the commit it was made at"
    assert "earlier environment" in report.outputs[0].why


def test_status_leaves_a_never_materialized_output_without_a_commit(root: Path) -> None:
    """There is nothing to name — and an empty string rather than HEAD,
    which would claim the output came from a commit that never made it."""
    report = engine.status(root)

    assert report.counts == {"current": 0, "behind": 0, "stale": 2}
    assert all(o.git_sha == "" and o.data_version == "" for o in report.outputs)
    assert "never been materialized" in report.outputs[0].why


def test_status_does_not_mind_a_dirty_tree(root: Path, inline: None) -> None:
    """It reads. Refusing here would make the one verb that tells you what
    state you are in unavailable exactly when you need it."""
    engine.materialize(root, [])
    (root / "results/baseline/first/value.txt").write_text("edited by hand\n")

    assert engine.status(root).counts["current"] == 2


def test_asking_for_an_output_makes_what_it_is_made_of(root: Path, inline: None) -> None:
    report = engine.materialize(root, ["second"])

    assert report.made == ["baseline/first", "baseline/second"]


def test_a_changed_decision_remakes_the_output_and_its_dependents(
    root: Path, inline: None
) -> None:
    engine.materialize(root, [])
    (root / "universes" / "baseline.yaml").write_text("id: baseline\ndecisions:\n  method: beta\n")
    dataset.save(root, [root], "switch method")

    report = engine.materialize(root, [])

    assert report.made == ["baseline/first", "baseline/second"]
    assert (root / "results/baseline/second/copy.txt").read_text() == "beta\n"


def test_the_previous_bytes_are_still_there_at_the_previous_commit(
    root: Path, inline: None
) -> None:
    """The property the whole layer exists for: given a commit, recover the
    exact bytes it produced."""
    engine.materialize(root, ["first"])
    original = dataset._git(["rev-parse", "HEAD"], cwd=root).strip()
    (root / "universes" / "baseline.yaml").write_text("id: baseline\ndecisions:\n  method: beta\n")
    dataset.save(root, [root], "switch method")
    engine.materialize(root, ["first"])
    assert (root / "results/baseline/first/value.txt").read_text() == "beta\n"

    dataset._git(["checkout", original, "--", "results/baseline/first"], cwd=root)

    assert (root / "results/baseline/first/value.txt").read_text() == "alpha\n"


# ---- check mode ------------------------------------------------------------


def test_check_says_what_would_run_and_why(root: Path) -> None:
    report = engine.check(root, [])

    assert set(report.planned) == {"baseline/first", "baseline/second"}
    assert "never been materialized" in report.planned["baseline/first"]
    assert not report.up_to_date


def test_check_writes_nothing_and_commits_nothing(root: Path) -> None:
    before = _commits(root)

    engine.check(root, [])

    assert not (root / "results/baseline/first").exists()
    assert _commits(root) == before


def test_check_cascades_through_an_output_it_already_decided_to_rebuild(
    root: Path, inline: None
) -> None:
    """The `None` sentinel. Check mode cannot know whether a rebuild comes
    out byte-identical, so it assumes it will not — the one place it is
    deliberately more pessimistic than a worker."""
    engine.materialize(root, [])
    (root / "universes" / "baseline.yaml").write_text("id: baseline\ndecisions:\n  method: beta\n")
    dataset.save(root, [root], "switch method")

    report = engine.check(root, [])

    assert "the recipe or its decisions" in report.planned["baseline/first"]
    assert report.planned["baseline/second"] == "the input `first` changed"


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
    engine.materialize(root, ["first"])
    (root / "notes.md").write_text("in progress\n")
    (root / "results/baseline/first/stray.txt").write_text("by hand\n")

    with pytest.raises(ProjectError) as raised:
        engine.materialize(root, [])

    message = str(raised.value)
    assert "commit these" in message and "notes.md" in message
    assert "discard these" in message and "results/baseline/first/stray.txt" in message


def _consuming(source: str) -> str:
    """`_SPEC` with `first` actually reading the declared input, from *source*."""
    return _SPEC.replace("source: data/catalog.fits", f"source: {source}").replace(
        "    decisions: [method]", "    inputs: [catalog]\n    decisions: [method]"
    )


def test_an_unreadable_declared_input_does_not_traceback_out_of_a_read_only_verb(
    analysis: Callable[..., Path],
) -> None:
    """A declared input directory can hold a symlink pointing nowhere —
    the directory walk keeps dangling links deliberately, so that an
    unfetched annexed file cannot silently drop out of the digest. One that
    is not an annex link then reaches `open()`. `status` and `--check` read
    projects that are in a state, so neither may raise."""
    root = analysis(_consuming("data/inputs"), universes={"baseline": _UNIVERSE})
    (root / "data" / "inputs").mkdir()
    (root / "data" / "inputs" / "broken").symlink_to("nowhere.fits")

    assert engine.status(root).outputs
    assert engine.check(root, []).planned


def test_a_declared_input_outside_the_project_is_reported_as_unrecoverable(
    analysis: Callable[..., Path], tmp_path: Path
) -> None:
    """Its bytes are hashed into the manifest like any other input, so a
    change to it still cascades — but it is not in the repository, so the
    commit that records the output cannot bring it back."""
    outside = tmp_path / "shared" / "catalog.fits"
    outside.parent.mkdir()
    outside.write_text("elsewhere\n")
    root = analysis(_consuming(str(outside)), universes={"baseline": _UNIVERSE})

    report = engine.check(root, [])

    assert any(str(outside) in w and "cannot restore them" in w for w in report.warnings)


def test_a_declared_input_inside_the_project_draws_no_such_warning(
    analysis: Callable[..., Path],
) -> None:
    """The mutation check on the test above: the same spec with the source
    back under `data/` says nothing."""
    root = analysis(_consuming("data/catalog.fits"), universes={"baseline": _UNIVERSE})

    assert not any("cannot restore" in w for w in engine.check(root, []).warnings)


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

    report = engine.materialize(root, [])

    assert report.ok
    assert any("UV_NO_BINARY" in w for w in report.warnings)


def test_an_edit_while_the_graph_runs_is_reported(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dirty check runs at start of run and manifests are written
    per-output later, so an edit in between leaves manifests whose
    git_sha no longer describes the code that ran. The run ends with one
    status call and says so — the honest floor under the unwritten
    `git_dirty` field."""

    class Editing(_Inline):
        def completed(self, handles: list[object]) -> Iterator[object]:
            (root / "notes.md").write_text("scribbled while the graph ran\n")
            yield from handles

    @contextmanager
    def fake() -> Iterator[_Inline]:
        yield Editing()

    monkeypatch.setattr(engine, "cluster_for_run", fake)

    report = engine.materialize(root, [])

    assert report.ok
    assert any("notes.md" in w and "in flight" in w for w in report.warnings)


def test_a_mid_run_stage_is_not_swept_into_lcs_commits(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`dataset.save` stages scoped and commits scoped — a partial
    commit — so work the user staged while the graph ran ends the run
    exactly where they left it: staged, warned about, and in none of
    lc's commits (the per-output saves and the trailing crate commit
    alike)."""
    _declare_license(root)

    class Staging(_Inline):
        def completed(self, handles: list[object]) -> Iterator[object]:
            (root / "notes.py").write_text("draft = True\n")
            dataset._git(["add", "--", "notes.py"], cwd=root)
            yield from handles

    @contextmanager
    def fake() -> Iterator[_Inline]:
        yield Staging()

    monkeypatch.setattr(engine, "cluster_for_run", fake)

    report = engine.materialize(root, [])

    assert report.ok
    assert any("notes.py" in w and "in flight" in w for w in report.warnings)
    staged = dataset._git(["diff", "--cached", "--name-only"], cwd=root).split()
    assert staged == ["notes.py"]
    ever_committed = dataset._git(["log", "--name-only", "--format="], cwd=root).split()
    assert "notes.py" not in ever_committed


def test_a_clean_run_reports_no_in_flight_edit(root: Path, inline: None) -> None:
    report = engine.materialize(root, [])
    assert not any("in flight" in w for w in report.warnings)


# ---- leaving the tree as clean as it was found -----------------------------


def test_a_failing_recipe_commits_nothing_and_leaves_the_tree_clean(
    analysis: Callable[..., Path], inline: None
) -> None:
    """The invariant that makes the dirty-tree refusal survivable: the next
    run must not tell the user to commit truncated, manifest-less
    garbage."""
    spec = _SPEC.replace("echo {decisions.method} > {output}/value.txt", "exit 1")
    root = analysis(spec, universes={"baseline": _UNIVERSE})
    before = _commits(root)

    report = engine.materialize(root, [])

    assert report.failed == ["baseline/first"]
    assert report.blocked == ["baseline/second"]
    assert not report.ok
    assert _commits(root) == before
    assert not dataset.status(root)


def test_a_run_in_which_everything_failed_is_not_up_to_date(
    analysis: Callable[..., Path], inline: None
) -> None:
    """`made` stays empty when every recipe fails, so `up_to_date` alone
    read "nothing to do" over a list of failures — and it is the second key
    of the JSON report, which is what an agent branches on."""
    spec = _SPEC.replace("echo {decisions.method} > {output}/value.txt", "exit 1")
    root = analysis(spec, universes={"baseline": _UNIVERSE})

    report = engine.materialize(root, [])

    assert report.made == []
    assert not report.up_to_date
    assert json.loads(json.dumps(report.as_dict()))["up_to_date"] is False
    # The positive control is `test_a_second_run_does_nothing_and_commits_
    # nothing`, where the same empty `made` does mean up to date.


def test_a_rebuild_that_fails_puts_the_previous_output_back(
    root: Path, inline: None
) -> None:
    engine.materialize(root, ["first"])
    (root / "astra.yaml").write_text(
        _SPEC.replace("echo {decisions.method} > {output}/value.txt", "exit 1")
    )
    dataset.save(root, [root], "break the recipe")
    at_break = _commits(root)

    report = engine.materialize(root, ["first"])

    assert report.failed == ["baseline/first"]
    assert (root / "results/baseline/first/value.txt").read_text() == "alpha\n"
    assert _commits(root) == at_break
    assert not dataset.status(root)


def test_an_interrupted_run_restores_what_never_reported(
    root: Path, inline: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sibling that already saved keeps its commit; the output still in
    flight is put back, so the tree ends clean either way."""
    engine.materialize(root, [])
    (root / "astra.yaml").write_text(_SPEC.replace("echo {decisions.method}", "echo changed"))
    dataset.save(root, [root], "edit both recipes")

    class _Interrupted(_Inline):
        def completed(self, handles: list[Any]) -> Iterator[TaskResult]:
            yield handles[0]
            raise KeyboardInterrupt

    @contextmanager
    def fake() -> Iterator[_Interrupted]:
        yield _Interrupted()

    monkeypatch.setattr(engine, "cluster_for_run", fake)

    with pytest.raises(KeyboardInterrupt):
        engine.materialize(root, [])

    assert not dataset.status(root)


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
    engine.materialize(root, ["second"])
    message = dataset._git(["log", "-1", "--format=%B"], cwd=root)

    subject, info = get_run_info(Dataset(str(root)), message)

    assert subject == "second [baseline]"
    assert info is not None
    # Full-string, not endswith: a flag appearing or vanishing here must
    # fail this test, not slip past a suffix match.
    assert info["cmd"] == (
        "uv run --no-project --with 'lightcone-cli==1.2.3' -- "
        "python -m lightcone.engine.worker baseline/second"
    )
    assert info["inputs"] == ["results/baseline/first"]
    assert info["outputs"] == ["results/baseline/second"]
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
    version: "0.0.13"
    name: analysis
    inputs:
      - id: catalog
        type: data
        source: data/catalog.txt
    outputs:
      - id: fit
        type: metric
        inputs: [catalog]
        recipe:
          command: cat {inputs.catalog} > {output}/seen.txt
    """
    root = analysis(spec, files={"data/catalog.txt": "measured\n"})
    dataset.save(root, [root / "data"], "the catalog")

    engine.materialize(root, [])

    from datalad.api import Dataset
    from datalad.local.rerun import get_run_info

    _, info = get_run_info(Dataset(str(root)), dataset._git(["log", "-1", "--format=%B"], cwd=root))
    assert info is not None
    assert info["inputs"] == ["data/catalog.txt"]


def test_every_manifest_of_one_run_names_the_same_commit(root: Path, inline: None) -> None:
    """The driver commits each output as it lands, so HEAD moves during the
    run — and reading it per task would stamp later manifests with a commit
    this same run created, nondeterministically."""
    engine.materialize(root, [])

    shas = {
        assets.read(root / "results/baseline" / name).git_sha  # type: ignore[union-attr]
        for name in ("first", "second")
    }
    assert len(shas) == 1


def test_check_agrees_with_a_run_on_a_clone_with_no_annex_content(
    root: Path, inline: None, tmp_path: Path
) -> None:
    """Manifests are in git, so an output whose bytes were never fetched
    is still classifiable — check mode reads the recorded digest rather
    than the pointer file sitting in its place."""
    engine.materialize(root, [])
    clone = _clone(root, tmp_path)
    pointer = (clone / "results/baseline/first/value.txt").read_text()
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
version: "0.0.13"
name: analysis

inputs:
  - id: catalog
    type: data
    source: data/catalog.fits

outputs:
  - id: copy
    type: metric
    inputs: [catalog]
    recipe:
      command: cat {inputs.catalog} > {output}/copy.txt
"""


def test_a_bytes_free_clone_fetches_its_inputs_and_is_up_to_date(
    analysis: Callable[..., Path], inline: None, tmp_path: Path
) -> None:
    """lc fetches declared inputs rather than telling anyone to — a clone
    holding only pointers materializes straight to up-to-date, hashing
    the same bytes the origin recorded. Without the fetch this run
    *failed*: the worker's hash refused the pointer file."""
    root = analysis(_FETCH_SPEC, files={"data/catalog.fits": "stars\n"})
    assert engine.materialize(root, []).ok

    clone = _clone(root, tmp_path)
    with pytest.raises(assets.ContentNotFetchedError):
        assets.data_version(clone / "data" / "catalog.fits")

    again = engine.materialize(clone, [])

    assert again.up_to_date, (again.failed, again.warnings)
    assert assets.data_version(clone / "data" / "catalog.fits")  # the bytes came
    assert not dataset.status(clone)


def test_an_unreachable_input_is_a_warning_and_a_per_task_failure(
    analysis: Callable[..., Path], inline: None, tmp_path: Path
) -> None:
    """A failed fetch must not refuse the whole run — independent tasks
    still run, and the task whose input is unreachable reports its own
    failure. Reaching the state honestly: clone, then delete the origin
    the annex would fetch from."""
    root = analysis(_FETCH_SPEC, files={"data/catalog.fits": "stars\n"})
    assert engine.materialize(root, []).ok
    clone = _clone(root, tmp_path)
    dataset._git(["remote", "remove", "origin"], cwd=clone)

    report = engine.materialize(clone, [])

    assert not report.ok
    assert report.failed == ["baseline/copy"]
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
    not describe while every manifest recorded the new lock's
    `env_version`; doing it instead is shorter and impossible to ignore."""
    pyproject = root / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text().replace("dependencies = []", 'dependencies = ["idna"]')
    )
    from lightcone.engine import project as project_mod

    project_mod._run(["uv", "lock", "-q", "--project", str(root)], cwd=root)
    dataset.save(root, [root], "add a dependency without syncing")
    assert not project_mod._env_is_current(root)

    report = engine.materialize(root, ["first"])

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
    back — so the manifest is regenerated inside the same commit, by the
    pinned engine resolved into an ephemeral environment."""
    pytest.importorskip("datalad")
    version, dist = engine_dist
    # The requirement seam, not `lc_version`: a git pin can only ever
    # build committed code, so the suite pins the wheel built from the
    # working tree — the code actually under test.
    monkeypatch.setattr(engine, "_engine_requirement", lambda: f"lightcone-cli=={version}")
    engine.materialize(root, ["first"])
    original = assets.read(root / "results/baseline/first")
    assert original is not None

    proc = subprocess.run(
        [sys.executable, "-c", "from datalad.api import rerun; rerun('HEAD')"],
        cwd=root,
        capture_output=True,
        text=True,
        env={**child_env(), "UV_FIND_LINKS": str(dist)},
    )

    assert proc.returncode == 0, proc.stderr
    rerun = assets.read(root / "results/baseline/first")
    assert rerun is not None
    assert rerun.data_version == original.data_version
    assert rerun.data_version == assets.data_version(root / "results/baseline/first")
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
    engine.materialize(root, ["first"])
    original = assets.read(root / "results/baseline/first")
    assert original is not None

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
    rerun = assets.read(clone / "results/baseline/first")
    assert rerun is not None
    assert rerun.data_version == original.data_version
    assert (clone / ".venv").exists()


# ---- the scheduler seam ----------------------------------------------------


def test_a_real_cluster_still_fits_through_the_seam(root: Path) -> None:
    """The one test that starts Dask. The seam is only worth having if the
    thing it abstracts still goes through it."""
    report = engine.materialize(root, [])

    assert report.made == ["baseline/first", "baseline/second"]
    assert not dataset.status(root)


def test_a_processes_cluster_fits_through_the_seam(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Workers in other processes — the shape every venue beyond one
    machine has. Pins that the unit crosses the boundary by reference
    (`worker.materialize`, `Task`, `Versions`, `TaskResult`) and that
    results travel back whole."""

    @contextmanager
    def processes() -> Iterator[engine._Dask]:
        from distributed import Client, LocalCluster

        with LocalCluster(  # type: ignore[no-untyped-call]
            n_workers=2, threads_per_worker=1, processes=True, dashboard_address=None
        ) as cluster:
            with Client(cluster) as client:  # type: ignore[no-untyped-call]
                yield engine._Dask(client)

    monkeypatch.setattr(engine, "cluster_for_run", processes)
    report = engine.materialize(root, [])

    assert report.made == ["baseline/first", "baseline/second"]
    assert not dataset.status(root)


# ---- the report ------------------------------------------------------------


def test_the_report_is_json_ready(root: Path, inline: None) -> None:
    report = engine.materialize(root, ["first"])
    data = json.loads(json.dumps(report.as_dict()))

    assert data["ok"] is True
    assert data["made"] == ["baseline/first"]


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
    engine.materialize(root, [])

    assert all(not o.foreign_write for o in engine.status(root).outputs)


def test_a_foreign_write_is_stale_and_names_its_commit(root: Path, inline: None) -> None:
    """The agent-forged-file fact: a hand-edited-and-committed output would
    read `current` forever, because a skip returns the recorded digest —
    so a directory last written by anything but its own run record is a
    *contradiction*, and contradiction is what `stale` means."""
    engine.materialize(root, [])
    forged = root / "results" / "baseline" / "first" / "value.txt"
    _forge(forged, "curated by hand\n")
    dataset.save(root, [forged.parent], "tweak colors")

    outputs = {o.output: o for o in engine.status(root).outputs}

    assert outputs["baseline/first"].status == "stale"
    forged_sha = dataset.last_writer(root, root / "results/baseline/first").sha
    assert outputs["baseline/first"].foreign_write == forged_sha
    assert "tweak colors" in outputs["baseline/first"].why
    assert "git show" in outputs["baseline/first"].why
    assert not outputs["baseline/second"].foreign_write


def test_check_plans_the_remake_of_a_foreign_written_output(
    root: Path, inline: None
) -> None:
    """Status and `--check` answer from one walk, so they cannot disagree
    about a foreign write — and the gate exits nonzero over it."""
    engine.materialize(root, [])
    forged = root / "results" / "baseline" / "first" / "value.txt"
    _forge(forged, "curated by hand\n")
    dataset.save(root, [forged.parent], "tweak colors")

    report = engine.check(root, [])

    assert any("tweak colors" in why for why in report.planned.values())
    assert not report.up_to_date


def test_the_foreign_write_fact_survives_a_bytes_free_clone(
    root: Path, inline: None, tmp_path: Path
) -> None:
    """History-based on purpose: content changes move pointers in git, so
    the fact needs no annex content — where a rehash would have nothing to
    hash."""
    engine.materialize(root, [])
    forged = root / "results" / "baseline" / "first" / "value.txt"
    _forge(forged, "curated by hand\n")
    dataset.save(root, [forged.parent], "tweak colors")
    clone = _clone(root, tmp_path)

    outputs = {o.output: o for o in engine.status(clone).outputs}

    assert outputs["baseline/first"].status == "stale"
    assert "tweak colors" in outputs["baseline/first"].why
    assert outputs["baseline/first"].foreign_write
    assert not outputs["baseline/second"].foreign_write


def test_the_next_run_remakes_a_foreign_written_output(root: Path, inline: None) -> None:
    """`results/` is lc's to write — the same philosophy as the dirty-tree
    refusal's path split — so a committed hand edit is remade, and the
    rebuild's own run record becomes the last writer again."""
    engine.materialize(root, [])
    forged = root / "results" / "baseline" / "first" / "value.txt"
    _forge(forged, "curated by hand\n")
    dataset.save(root, [forged.parent], "tweak colors")

    report = engine.materialize(root, [])

    assert "baseline/first" in report.made
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
    report = engine.materialize(root, [])

    assert not (root / "ro-crate-metadata.json").exists()
    assert any("[project].license" in w for w in report.warnings)


def test_a_licensed_materialize_converges_the_crate_and_commits_it(
    root: Path, inline: None
) -> None:
    _declare_license(root)

    engine.materialize(root, [])

    crate_path = root / "ro-crate-metadata.json"
    assert crate_path.is_file()
    assert not dataset.status(root)  # committed, tree exactly as clean as before
    assert dataset.last_writer(root, crate_path).subject == "Update the RO-Crate publication view"
    graph = json.loads(crate_path.read_text())["@graph"]
    types = {e["@id"]: e["@type"] for e in graph}
    assert "OrganizeAction" in types.values()
    assert types["results/baseline/first/"] == "Dataset"


def test_an_idempotent_rerun_commits_nothing(root: Path, inline: None) -> None:
    """The document is a pure function of repository state — a re-render at
    the same state is a string compare, not a commit."""
    _declare_license(root)
    engine.materialize(root, [])
    before = _commits(root)

    engine.materialize(root, [])

    assert _commits(root) == before


def test_declaring_a_license_later_creates_the_crate_then(root: Path, inline: None) -> None:
    engine.materialize(root, [])
    assert not (root / "ro-crate-metadata.json").exists()

    _declare_license(root)
    engine.materialize(root, [])

    assert (root / "ro-crate-metadata.json").is_file()


def test_a_removed_license_stops_maintenance_but_keeps_the_file(
    root: Path, inline: None
) -> None:
    """The crate is in committed history either way; deleting a file over a
    possibly temporary edit is not convergence's call."""
    _declare_license(root)
    engine.materialize(root, [])
    pyproject = root / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace('license = "MIT"\n', ""))
    dataset.save(root, [pyproject], "drop the license")

    report = engine.materialize(root, [])

    assert (root / "ro-crate-metadata.json").is_file()
    assert any("no longer maintained" in w for w in report.warnings)


def test_status_places_the_publication_view(root: Path, inline: None) -> None:
    """The `crate:` header line, through its three plain states."""
    assert engine.status(root).crate == "not maintained — declare [project].license to enable it"

    _declare_license(root)
    assert engine.status(root).crate == "will be created by the next `lc materialize`"

    engine.materialize(root, [])
    assert engine.status(root).crate == "up to date with the outputs"


def test_status_sees_the_crate_lag_a_rerun_leaves(root: Path, inline: None) -> None:
    """The recorded residue made visible: a rerun rewrites a manifest but
    never regenerates the view. Status reads the mismatch off the
    document's own datePublished against the manifests it already read —
    no git, no rocrate import."""
    from dataclasses import replace

    _declare_license(root)
    engine.materialize(root, [])
    directory = root / "results/baseline/second"
    manifest = assets.read(directory)
    assert manifest is not None
    assets.write(directory, replace(manifest, finished_at="2027-01-01T00:00:00.000+00:00"))
    dataset.save(root, [directory], "a rerun-shaped manifest rewrite")

    assert engine.status(root).crate.startswith("behind")

    engine.materialize(root, [])
    assert engine.status(root).crate == "up to date with the outputs"


def test_an_output_the_spec_dropped_is_excluded_and_named(root: Path, inline: None) -> None:
    _declare_license(root)
    engine.materialize(root, [])
    spec_path = root / "astra.yaml"
    spec = spec_path.read_text()
    block = spec[spec.index("  - id: second") : spec.index("\ndecisions:") + 1]
    spec_path.write_text(spec.replace(block, ""))
    dataset.save(root, [spec_path], "drop the second output")

    report = engine.materialize(root, [])

    assert any("results/baseline/second" in w for w in report.warnings)
    document = (root / "ro-crate-metadata.json").read_text()
    assert "results/baseline/second/" not in document
