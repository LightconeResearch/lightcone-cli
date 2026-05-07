"""Tests for engine/wrroc.py — Workflow Run RO-Crate exporter."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from lightcone.cli.commands import main
from lightcone.engine.manifest import code_version, write_manifest
from lightcone.engine.wrroc import (
    PROVENANCE_RUN_CRATE_PROFILE,
    ExportResult,
    export_wrroc,
)

# ---------------------------------------------------------------------------
# Fixtures: tiny project + materialized outputs
# ---------------------------------------------------------------------------


def _write_spec(project: Path, spec: dict[str, Any]) -> None:
    project.mkdir(parents=True, exist_ok=True)
    (project / "astra.yaml").write_text(yaml.safe_dump(spec))


def _write_universe(project: Path, universe_id: str, decisions: dict[str, Any]) -> None:
    udir = project / "universes"
    udir.mkdir(parents=True, exist_ok=True)
    (udir / f"{universe_id}.yaml").write_text(
        yaml.safe_dump({"decisions": decisions})
    )


def _materialize(
    project: Path,
    output_id: str,
    universe_id: str,
    *,
    recipe: str,
    decisions: dict[str, Any] | None = None,
    container_image: str | None = None,
    inputs: dict[str, Path] | None = None,
    body: str = "output bytes",
) -> Path:
    out = project / "results" / universe_id / output_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "data.txt").write_text(body)
    cv = code_version(
        recipe=recipe,
        container_image=container_image,
        decisions=decisions or {},
    )
    write_manifest(
        output_dir=out,
        inputs=inputs or {},
        cfg={
            "output_id": output_id,
            "universe_id": universe_id,
            "recipe": recipe,
            "container_image": container_image,
            "decisions": decisions or {},
            "code_version": cv,
            "git_sha": "abc1234",
            "lc_version": "0.0.1",
        },
    )
    return out


@pytest.fixture
def minimal_project(tmp_path: Path) -> Path:
    """A project with one universe and one materialized output."""
    _write_spec(
        tmp_path,
        {
            "name": "minimal",
            "description": "test",
            "outputs": [
                {"id": "foo", "recipe": {"command": "echo foo > data.txt"}},
            ],
        },
    )
    _write_universe(tmp_path, "baseline", {})
    _materialize(tmp_path, "foo", "baseline", recipe="echo foo > data.txt")
    return tmp_path


@pytest.fixture
def chained_project(tmp_path: Path) -> Path:
    """Two-step DAG: step_b depends on step_a."""
    _write_spec(
        tmp_path,
        {
            "name": "chained",
            "description": "Two-step chained DAG for WRROC tests.",
            # ASTRA's decisions schema: dict keyed by decision id, with
            # options also a dict keyed by option id.
            "decisions": {
                "method": {
                    "label": "Method",
                    "default": "A",
                    "options": {
                        "A": {"label": "Option A"},
                        "B": {"label": "Option B"},
                    },
                },
            },
            "outputs": [
                {"id": "step_a", "recipe": {"command": "echo a > data.txt"}},
                {
                    "id": "step_b",
                    "inputs": ["step_a"],
                    "recipe": {"command": "cat data/step_a/data.txt > data.txt"},
                },
            ],
        },
    )
    _write_universe(tmp_path, "baseline", {"method": "A"})

    out_a = _materialize(
        tmp_path, "step_a", "baseline",
        recipe="echo a > data.txt",
        decisions={"method": "A"},
    )
    _materialize(
        tmp_path, "step_b", "baseline",
        recipe="cat data/step_a/data.txt > data.txt",
        decisions={"method": "A"},
        inputs={"step_a": out_a},
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Module-level tests
# ---------------------------------------------------------------------------


class TestMinimalExport:
    def test_returns_export_result(self, minimal_project: Path) -> None:
        out = minimal_project / "wrroc"
        result = export_wrroc(minimal_project, out, author="Tester <t@x>")
        assert isinstance(result, ExportResult)
        assert result.bundle_path == out
        assert result.runs_included == 1
        assert result.universes_included == ["baseline"]
        assert result.is_zip is False

    def test_bundle_has_metadata_file(self, minimal_project: Path) -> None:
        out = minimal_project / "wrroc"
        export_wrroc(minimal_project, out, author="Tester <t@x>")
        assert (out / "ro-crate-metadata.json").is_file()
        meta = json.loads((out / "ro-crate-metadata.json").read_text())
        assert "@context" in meta
        assert "@graph" in meta

    def test_bundle_includes_astra_yaml(self, minimal_project: Path) -> None:
        out = minimal_project / "wrroc"
        export_wrroc(minimal_project, out, author="Tester <t@x>")
        assert (out / "astra.yaml").is_file()

    def test_root_conforms_to_wrroc_profiles(self, minimal_project: Path) -> None:
        out = minimal_project / "wrroc"
        export_wrroc(minimal_project, out, author="Tester <t@x>")
        meta = json.loads((out / "ro-crate-metadata.json").read_text())
        root = next(g for g in meta["@graph"] if g["@id"] == "./")
        conforms_ids = [c["@id"] for c in root["conformsTo"]]
        assert PROVENANCE_RUN_CRATE_PROFILE in conforms_ids


class TestChainPreserved:
    def test_step_b_object_references_step_a_dataset(
        self, chained_project: Path
    ) -> None:
        out = chained_project / "wrroc"
        export_wrroc(chained_project, out, author="Tester <t@x>")
        meta = json.loads((out / "ro-crate-metadata.json").read_text())

        # Find step_b's CreateAction
        actions = [g for g in meta["@graph"] if g.get("@type") == "CreateAction"]
        step_b_action = next(a for a in actions if "step_b" in a["@id"])

        # Its `object` list should include step_a's dataset @id
        object_ids = [o["@id"] for o in step_b_action["object"]]
        assert "results/baseline/step_a/" in object_ids

    def test_both_steps_have_create_actions(self, chained_project: Path) -> None:
        out = chained_project / "wrroc"
        result = export_wrroc(chained_project, out, author="X <x@y>")
        assert result.runs_included == 2

        meta = json.loads((out / "ro-crate-metadata.json").read_text())
        actions = [g for g in meta["@graph"] if g.get("@type") == "CreateAction"]
        ids = {a["@id"] for a in actions}
        assert any("step_a" in i for i in ids)
        assert any("step_b" in i for i in ids)


class TestDecisionsAttached:
    def test_decisions_emitted_as_property_values(
        self, chained_project: Path
    ) -> None:
        out = chained_project / "wrroc"
        export_wrroc(chained_project, out, author="X <x@y>")
        meta = json.loads((out / "ro-crate-metadata.json").read_text())

        pvs = [g for g in meta["@graph"] if g.get("@type") == "PropertyValue"]
        method_pvs = [p for p in pvs if p.get("name") == "method"]
        assert len(method_pvs) >= 1
        assert all(p["value"] == "A" for p in method_pvs)

    def test_complex_decision_value_is_serialized(self, tmp_path: Path) -> None:
        """Non-primitive decision values must be JSON-serialized for
        PropertyValue.value compatibility.
        """
        _write_spec(
            tmp_path,
            {
                "outputs": [
                    {"id": "foo", "recipe": {"command": "echo foo"}},
                ]
            },
        )
        _write_universe(tmp_path, "u1", {"opts": {"a": 1, "b": [2, 3]}})
        _materialize(
            tmp_path, "foo", "u1",
            recipe="echo foo",
            decisions={"opts": {"a": 1, "b": [2, 3]}},
        )

        export_wrroc(tmp_path, tmp_path / "wrroc", author="X <x@y>")
        meta = json.loads((tmp_path / "wrroc" / "ro-crate-metadata.json").read_text())
        opts_pv = next(
            g for g in meta["@graph"]
            if g.get("@type") == "PropertyValue" and g.get("name") == "opts"
        )
        # Coerced to a JSON string
        assert isinstance(opts_pv["value"], str)
        assert json.loads(opts_pv["value"]) == {"a": 1, "b": [2, 3]}


class TestRoundTrip:
    def test_load_via_rocrate_py(self, chained_project: Path) -> None:
        """A bundle we wrote must be loadable by rocrate.Crate(path)."""
        from rocrate.rocrate import ROCrate

        out = chained_project / "wrroc"
        export_wrroc(chained_project, out, author="X <x@y>")

        crate = ROCrate(out)
        assert crate.name == "chained"
        actions = crate.get_by_type("CreateAction")
        assert len(actions) == 2

    def test_workflow_is_main_entity(self, chained_project: Path) -> None:
        from rocrate.rocrate import ROCrate

        out = chained_project / "wrroc"
        export_wrroc(chained_project, out, author="X <x@y>")
        crate = ROCrate(out)
        assert crate.mainEntity is not None
        assert crate.mainEntity.id == "astra.yaml"


class TestMetadataOnly:
    def test_skips_data_files(self, chained_project: Path) -> None:
        out = chained_project / "wrroc"
        export_wrroc(
            chained_project, out, author="X <x@y>", include_data=False,
        )
        # data.txt files should NOT be copied
        assert not (out / "results" / "baseline" / "step_a" / "data.txt").exists()
        # but manifests SHOULD be
        assert (
            out / "results" / "baseline" / "step_a" / ".lightcone-manifest.json"
        ).is_file()

    def test_chain_still_valid_in_metadata(self, chained_project: Path) -> None:
        """Even without data files, the @id chain must still link upstream."""
        out = chained_project / "wrroc"
        export_wrroc(
            chained_project, out, author="X <x@y>", include_data=False,
        )
        meta = json.loads((out / "ro-crate-metadata.json").read_text())
        actions = [g for g in meta["@graph"] if g.get("@type") == "CreateAction"]
        step_b = next(a for a in actions if "step_b" in a["@id"])
        ids = [o["@id"] for o in step_b["object"]]
        assert "results/baseline/step_a/" in ids


class TestZipBundle:
    def test_produces_zip(self, minimal_project: Path) -> None:
        zip_path = minimal_project / "bundle.zip"
        result = export_wrroc(
            minimal_project, zip_path, author="X <x@y>", zip_bundle=True,
        )
        assert result.is_zip is True
        assert zip_path.is_file()
        # Contains ro-crate-metadata.json
        import zipfile
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            assert any("ro-crate-metadata.json" in n for n in names)

    def test_zip_raises_on_existing_directory(self, minimal_project: Path) -> None:
        dir_path = minimal_project / "existing_dir"
        dir_path.mkdir()
        with pytest.raises(FileExistsError, match="existing directory"):
            export_wrroc(minimal_project, dir_path, author="X <x@y>", zip_bundle=True)


class TestAuthor:
    def test_explicit_author_overrides(self, minimal_project: Path) -> None:
        out = minimal_project / "wrroc"
        export_wrroc(minimal_project, out, author="Alice <a@b.c>")
        meta = json.loads((out / "ro-crate-metadata.json").read_text())
        persons = [g for g in meta["@graph"] if g.get("@type") == "Person"]
        assert len(persons) == 1
        assert persons[0]["name"] == "Alice"
        assert persons[0]["email"] == "a@b.c"

    def test_author_used_as_action_agent(self, minimal_project: Path) -> None:
        out = minimal_project / "wrroc"
        export_wrroc(minimal_project, out, author="Alice <a@b.c>")
        meta = json.loads((out / "ro-crate-metadata.json").read_text())
        action = next(g for g in meta["@graph"] if g.get("@type") == "CreateAction")
        assert action["agent"]["@id"] == "#author-a_at_b.c"


class TestUniverseFilter:
    def test_restricts_to_listed_universes(self, tmp_path: Path) -> None:
        _write_spec(
            tmp_path,
            {
                "outputs": [
                    {"id": "foo", "recipe": {"command": "echo foo"}},
                ]
            },
        )
        _write_universe(tmp_path, "u1", {})
        _write_universe(tmp_path, "u2", {})
        _materialize(tmp_path, "foo", "u1", recipe="echo foo")
        _materialize(tmp_path, "foo", "u2", recipe="echo foo")

        result = export_wrroc(
            tmp_path, tmp_path / "wrroc",
            universes=["u1"], author="X <x@y>",
        )
        assert result.universes_included == ["u1"]
        assert result.runs_included == 1


class TestEmptyProject:
    def test_no_materializations_warns_but_succeeds(
        self, tmp_path: Path,
    ) -> None:
        _write_spec(
            tmp_path,
            {
                "outputs": [
                    {"id": "foo", "recipe": {"command": "echo foo"}},
                ]
            },
        )
        _write_universe(tmp_path, "u1", {})
        # No materialization

        result = export_wrroc(tmp_path, tmp_path / "wrroc", author="X <x@y>")
        assert result.runs_included == 0
        # Bundle still has the workflow definition
        assert (tmp_path / "wrroc" / "astra.yaml").is_file()


class TestRefuseClobber:
    def test_non_empty_target_dir_errors(self, minimal_project: Path) -> None:
        out = minimal_project / "wrroc"
        out.mkdir()
        (out / "existing.txt").write_text("hi")
        with pytest.raises(FileExistsError):
            export_wrroc(minimal_project, out, author="X <x@y>")


class TestSubAnalyses:
    """Sub-analysis outputs must be captured with the correct path-rooted
    `@id`s and the chain back to root outputs preserved.
    """

    @pytest.fixture
    def subanalysis_project(self, tmp_path: Path) -> Path:
        # Root project declares a sub-analysis at analyses/sub/
        _write_spec(
            tmp_path,
            {
                "name": "with-subs",
                "description": "Project with one sub-analysis.",
                "outputs": [
                    {"id": "root_out", "recipe": {"command": "echo r"}},
                ],
                "analyses": {
                    "sub": {"path": "./analyses/sub"},
                },
            },
        )
        # Sub-analysis has its own astra.yaml.
        sub_dir = tmp_path / "analyses" / "sub"
        sub_dir.mkdir(parents=True)
        (sub_dir / "astra.yaml").write_text(yaml.safe_dump({
            "name": "sub",
            "description": "Sub-analysis.",
            "outputs": [
                {"id": "sub_out", "recipe": {"command": "echo s"}},
            ],
        }))
        _write_universe(tmp_path, "baseline", {})

        # Materialize root output at <root>/results/baseline/root_out/
        _materialize(tmp_path, "root_out", "baseline", recipe="echo r")

        # Materialize sub-analysis output at <sub>/results/baseline/sub_out/
        sub_out_dir = sub_dir / "results" / "baseline" / "sub_out"
        sub_out_dir.mkdir(parents=True)
        (sub_out_dir / "data.txt").write_text("sub bytes")
        cv = code_version(recipe="echo s", container_image=None, decisions={})
        write_manifest(
            output_dir=sub_out_dir,
            inputs={},
            cfg={"output_id": "sub_out", "universe_id": "baseline",
                 "recipe": "echo s", "container_image": None,
                 "decisions": {}, "code_version": cv,
                 "git_sha": "abc", "lc_version": "0.0.1"},
        )
        return tmp_path

    def test_both_root_and_sub_outputs_captured(
        self, subanalysis_project: Path,
    ) -> None:
        out = subanalysis_project / "wrroc"
        result = export_wrroc(subanalysis_project, out, author="X <x@y>")
        assert result.runs_included == 2

    def test_sub_dataset_id_includes_sub_path(
        self, subanalysis_project: Path,
    ) -> None:
        out = subanalysis_project / "wrroc"
        export_wrroc(subanalysis_project, out, author="X <x@y>")
        meta = json.loads((out / "ro-crate-metadata.json").read_text())
        ids = {g["@id"] for g in meta["@graph"]}
        # Root dataset uses results/<u>/<out>/
        assert "results/baseline/root_out/" in ids
        # Sub-analysis dataset uses <sub_path>/results/<u>/<out>/
        assert "analyses/sub/results/baseline/sub_out/" in ids

    def test_sub_create_action_id_qualified(
        self, subanalysis_project: Path,
    ) -> None:
        """CreateAction @ids include the analysis_id qualifier so sub
        and root outputs with the same id never collide.
        """
        out = subanalysis_project / "wrroc"
        export_wrroc(subanalysis_project, out, author="X <x@y>")
        meta = json.loads((out / "ro-crate-metadata.json").read_text())
        action_ids = {
            g["@id"] for g in meta["@graph"]
            if g.get("@type") == "CreateAction"
        }
        assert "#run-baseline-root_out" in action_ids
        assert "#run-baseline-sub.sub_out" in action_ids

    def test_sub_data_files_bundled(
        self, subanalysis_project: Path,
    ) -> None:
        out = subanalysis_project / "wrroc"
        export_wrroc(subanalysis_project, out, author="X <x@y>")
        # Sub-analysis data file should be copied at the corresponding
        # path inside the bundle.
        assert (
            out / "analyses" / "sub" / "results" / "baseline"
            / "sub_out" / "data.txt"
        ).is_file()


class TestToolName:
    """SoftwareApplication.name resolution: tool_name > heuristic > output_id."""

    def _project_with_recipe(
        self,
        tmp_path: Path,
        *,
        recipe_command: str,
        tool_name: str | None = None,
    ) -> Path:
        recipe: dict[str, Any] = {"command": recipe_command}
        if tool_name:
            recipe["tool_name"] = tool_name
        _write_spec(
            tmp_path,
            {
                "outputs": [{"id": "foo", "recipe": recipe}],
            },
        )
        _write_universe(tmp_path, "u1", {})
        _materialize(tmp_path, "foo", "u1", recipe=recipe_command)
        return tmp_path

    def _software_app(self, bundle: Path) -> dict[str, Any]:
        meta = json.loads((bundle / "ro-crate-metadata.json").read_text())
        return next(
            g for g in meta["@graph"]
            if g.get("@type") == "SoftwareApplication"
            and g["@id"].startswith("#recipe")
        )

    def test_explicit_tool_name_wins(self, tmp_path: Path) -> None:
        project = self._project_with_recipe(
            tmp_path,
            recipe_command="python scripts/analyze.py --x 1",
            tool_name="analyze (chi-squared)",
        )
        out = project / "wrroc"
        export_wrroc(project, out, author="X <x@y>")
        sw = self._software_app(out)
        assert sw["name"] == "analyze (chi-squared)"
        assert sw["description"] == "python scripts/analyze.py --x 1"

    def test_heuristic_extracts_script_path(self, tmp_path: Path) -> None:
        project = self._project_with_recipe(
            tmp_path,
            recipe_command="python scripts/analyze.py --x 1",
        )
        out = project / "wrroc"
        export_wrroc(project, out, author="X <x@y>")
        sw = self._software_app(out)
        assert sw["name"] == "scripts/analyze.py"

    def test_falls_back_to_output_id(self, tmp_path: Path) -> None:
        # Recipe with no script-like token in it
        project = self._project_with_recipe(
            tmp_path,
            recipe_command="echo hello",
        )
        out = project / "wrroc"
        export_wrroc(project, out, author="X <x@y>")
        sw = self._software_app(out)
        assert sw["name"] == "foo"  # the output id


class TestGitRemote:
    def test_emits_code_repository_entity(self, tmp_path: Path) -> None:
        """When manifests carry git_remote, the bundle gets a CodeRepository."""
        _write_spec(
            tmp_path,
            {"outputs": [{"id": "foo", "recipe": {"command": "echo foo"}}]},
        )
        _write_universe(tmp_path, "u1", {})
        out = tmp_path / "results" / "u1" / "foo"
        out.mkdir(parents=True)
        (out / "data.txt").write_text("bytes")
        cv = code_version(recipe="echo foo", container_image=None, decisions={})
        write_manifest(
            output_dir=out, inputs={},
            cfg={
                "output_id": "foo", "universe_id": "u1",
                "recipe": "echo foo", "container_image": None,
                "decisions": {}, "code_version": cv,
                "git_sha": "abc",
                "git_remote": "https://github.com/dkn16/test-repo",
                "lc_version": "0.0.1",
            },
        )

        bundle = tmp_path / "wrroc"
        export_wrroc(tmp_path, bundle, author="X <x@y>")
        meta = json.loads((bundle / "ro-crate-metadata.json").read_text())

        repos = [
            g for g in meta["@graph"]
            if "CodeRepository" in (
                g["@type"] if isinstance(g["@type"], list) else [g["@type"]]
            )
        ]
        assert len(repos) == 1
        assert repos[0]["@id"] == "https://github.com/dkn16/test-repo"
        assert repos[0]["url"] == "https://github.com/dkn16/test-repo"

        wf = next(
            g for g in meta["@graph"]
            if "ComputationalWorkflow" in (
                g["@type"] if isinstance(g["@type"], list) else [g["@type"]]
            )
        )
        assert wf.get("codeRepository", {}).get("@id") == \
            "https://github.com/dkn16/test-repo"

    def test_no_git_remote_no_repo_entity(self, minimal_project: Path) -> None:
        """Without git_remote in manifests, no CodeRepository is emitted."""
        out = minimal_project / "wrroc"
        export_wrroc(minimal_project, out, author="X <x@y>")
        meta = json.loads((out / "ro-crate-metadata.json").read_text())
        for g in meta["@graph"]:
            t = g.get("@type")
            tlist = t if isinstance(t, list) else [t]
            assert "CodeRepository" not in tlist


class TestUnreadableManifest:
    def test_skips_permission_denied(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If a manifest read raises OSError (permission, broken symlink),
        the exporter must warn and skip rather than abort the whole run.
        Mirrors what happens with cross-user symlinked results dirs.
        """
        from lightcone.engine import wrroc as wrroc_mod

        _write_spec(
            tmp_path,
            {"outputs": [{"id": "foo", "recipe": {"command": "echo foo"}}]},
        )
        _write_universe(tmp_path, "u1", {})
        # No manifest exists, but inject one that raises PermissionError.
        from lightcone.engine import manifest as manifest_mod

        def boom(out_dir: Path) -> dict[str, Any] | None:
            raise PermissionError(f"denied: {out_dir}")

        monkeypatch.setattr(manifest_mod, "read_manifest", boom)
        # wrroc.py imports read_manifest by name, so patch there too.
        monkeypatch.setattr(wrroc_mod, "read_manifest", boom)

        # Should not raise; should warn and produce a (mostly empty) bundle.
        result = export_wrroc(tmp_path, tmp_path / "wrroc", author="X <x@y>")
        assert result.runs_included == 0


class TestProfileConformance:
    """The bundle's @graph must declare profile CreativeWork entities,
    set a license, and include FormalParameter additionalType — the
    Provenance Run Crate 0.5 validator's REQUIRED checks all hinge on
    these.
    """

    def test_root_has_license(self, minimal_project: Path) -> None:
        out = minimal_project / "wrroc"
        export_wrroc(minimal_project, out, author="X <x@y>")
        meta = json.loads((out / "ro-crate-metadata.json").read_text())
        root = next(g for g in meta["@graph"] if g["@id"] == "./")
        assert "license" in root
        assert root["license"]["@id"].startswith("http")

    def test_explicit_license_passed_through(self, minimal_project: Path) -> None:
        out = minimal_project / "wrroc"
        export_wrroc(
            minimal_project, out, author="X <x@y>",
            license="https://opensource.org/licenses/MIT",
        )
        meta = json.loads((out / "ro-crate-metadata.json").read_text())
        root = next(g for g in meta["@graph"] if g["@id"] == "./")
        assert root["license"]["@id"] == "https://opensource.org/licenses/MIT"

    def test_profile_creativework_entities_declared(
        self, minimal_project: Path,
    ) -> None:
        """conformsTo profile URLs must each have a CreativeWork entity."""
        out = minimal_project / "wrroc"
        export_wrroc(minimal_project, out, author="X <x@y>")
        meta = json.loads((out / "ro-crate-metadata.json").read_text())
        ids = {g["@id"] for g in meta["@graph"]}
        assert PROVENANCE_RUN_CRATE_PROFILE in ids

    def test_formal_parameters_have_additional_type(
        self, chained_project: Path,
    ) -> None:
        out = chained_project / "wrroc"
        export_wrroc(chained_project, out, author="X <x@y>")
        meta = json.loads((out / "ro-crate-metadata.json").read_text())
        params = [g for g in meta["@graph"] if g.get("@type") == "FormalParameter"]
        assert len(params) >= 1
        for p in params:
            assert "additionalType" in p
            assert p["additionalType"]["@id"].startswith("http://schema.org/")

    def test_workflow_haspart_recipes(self, chained_project: Path) -> None:
        """ComputationalWorkflow MUST link recipes via hasPart."""
        out = chained_project / "wrroc"
        export_wrroc(chained_project, out, author="X <x@y>")
        meta = json.loads((out / "ro-crate-metadata.json").read_text())
        wf = next(
            g for g in meta["@graph"]
            if "ComputationalWorkflow" in (
                g["@type"] if isinstance(g["@type"], list) else [g["@type"]]
            )
        )
        has_part = wf.get("hasPart") or []
        recipe_refs = [hp["@id"] for hp in has_part if hp["@id"].startswith("#recipe-")]
        assert len(recipe_refs) >= 2  # both step_a and step_b recipes


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


class TestCli:
    def test_export_wrroc_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["export", "wrroc", "--help"])
        assert result.exit_code == 0
        assert "WRROC" in result.output
        assert "--zip" in result.output
        assert "--metadata-only" in result.output

    def test_export_wrroc_runs(
        self, minimal_project: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(minimal_project)
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "export", "wrroc",
                "-o", "out-dir",
                "--author", "Tester <t@x>",
            ],
        )
        assert result.exit_code == 0, result.output
        assert (minimal_project / "out-dir" / "ro-crate-metadata.json").is_file()
        assert "Wrote WRROC" in result.output

    def test_export_wrroc_zip(
        self, minimal_project: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(minimal_project)
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "export", "wrroc",
                "-o", "bundle.zip",
                "--zip",
                "--author", "Tester <t@x>",
            ],
        )
        assert result.exit_code == 0, result.output
        assert (minimal_project / "bundle.zip").is_file()

    def test_export_wrroc_metadata_only(
        self, minimal_project: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(minimal_project)
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "export", "wrroc",
                "-o", "meta",
                "--metadata-only",
                "--author", "Tester <t@x>",
            ],
        )
        assert result.exit_code == 0, result.output
        # Manifest yes, data no
        out_dir = minimal_project / "meta" / "results" / "baseline" / "foo"
        assert (out_dir / ".lightcone-manifest.json").is_file()
        assert not (out_dir / "data.txt").exists()

    def test_export_wrroc_no_runs_warns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_spec(
            tmp_path,
            {"outputs": [{"id": "foo", "recipe": {"command": "echo foo"}}]},
        )
        _write_universe(tmp_path, "u1", {})
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["export", "wrroc", "-o", "out", "--author", "X <x@y>"],
        )
        assert result.exit_code == 0, result.output
        assert "no materialized outputs" in result.output.lower()
