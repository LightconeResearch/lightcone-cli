"""Workflow Run RO-Crate (WRROC) exporter.

Walks a project's per-output ``.lightcone-manifest.json`` sidecars and
emits a `Workflow Run RO-Crate <https://www.researchobject.org/workflow-run-crate/>`_
bundle suitable for upload to WorkflowHub, Zenodo, or any RO-Crate-aware
archive.

The lightcone manifest layer remains the canonical internal format. WRROC
is the **publication** view, generated on demand. We target the deepest
of the three WRROC profiles — *Provenance Run Crate* — because lightcone
already captures the per-step data it requires.

Entity mapping
--------------

==============================  =====================================
lightcone concept               WRROC entity
==============================  =====================================
``astra.yaml``                  ``ComputationalWorkflow``
each universe                   ``PropertyValue`` set on the workflow
each materialized output dir    ``Dataset`` (data files inside)
each recipe execution           ``CreateAction``
  ``object``                    upstream Datasets / external Files
  ``result``                    the output Dataset
  ``instrument``                the recipe ``SoftwareApplication``
  ``agent``                     the human author (``Person``)
each container image            ``SoftwareApplication``
each decision value             ``PropertyValue`` on the workflow
==============================  =====================================

The exporter is one-shot: ``export_wrroc()`` produces a directory (or
``--zip`` archive). We do not maintain a live crate; the user invokes
this when they're ready to publish.
"""
from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astra.helpers import get_decisions, load_yaml, resolve_analysis_tree

from lightcone.engine.manifest import MANIFEST_FILENAME, read_manifest
from lightcone.engine.tree import (
    TreeOutput,
    collect_tree_outputs,
    resolve_output_path,
    resolve_universe_decisions,
)

logger = logging.getLogger(__name__)

#: WRROC profile we target. Pinned explicitly — bump when upgrading.
PROVENANCE_RUN_CRATE_PROFILE = (
    "https://w3id.org/ro/wfrun/provenance/0.5"
)
WORKFLOW_RUN_CRATE_PROFILE = (
    "https://w3id.org/ro/wfrun/workflow/0.5"
)
PROCESS_RUN_CRATE_PROFILE = (
    "https://w3id.org/ro/wfrun/process/0.5"
)


@dataclass
class ExportResult:
    """Returned by :func:`export_wrroc` so callers can act on the outcome."""

    bundle_path: Path
    runs_included: int
    universes_included: list[str]
    is_zip: bool


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


#: Default license URL when none is supplied. CC-BY-4.0 is widely accepted
#: by Zenodo and WorkflowHub for research outputs and is permissive enough
#: not to surprise users who didn't think about licensing. Can be
#: overridden via the ``license`` argument or ``--license`` CLI flag.
DEFAULT_LICENSE = "https://creativecommons.org/licenses/by/4.0/"


def export_wrroc(
    project_path: Path,
    output_path: Path,
    *,
    universes: list[str] | None = None,
    author: str | None = None,
    license: str | None = None,
    zip_bundle: bool = False,
    include_data: bool = True,
) -> ExportResult:
    """Walk manifests in *project_path* and emit a WRROC bundle.

    Parameters
    ----------
    project_path:
        Project root containing ``astra.yaml``.
    output_path:
        Where to write the bundle. If *zip_bundle* is True, this is the
        ``.zip`` file; otherwise it is the bundle directory.
    universes:
        Restrict to a subset of universes. ``None`` includes all
        universes that have at least one materialized output.
    author:
        Override the author. If ``None``, falls back to ``git config
        user.name``/``user.email`` then to ``LIGHTCONE_AUTHOR`` env var.
    license:
        License URL or SPDX-style identifier for the bundle. Required by
        the Workflow RO-Crate profile; defaults to :data:`DEFAULT_LICENSE`
        (CC-BY-4.0) when ``None``.
    zip_bundle:
        Package as a single zip after building. The zip contains the
        bundle directory at its root.
    include_data:
        When False, only the manifests, ``astra.yaml`` and universe files
        are bundled — useful for archiving provenance without re-uploading
        large data files.
    """
    # Lazy import to keep the module importable without rocrate installed.
    from rocrate.rocrate import ROCrate

    project_path = Path(project_path).resolve()
    spec_path = project_path / "astra.yaml"
    if not spec_path.is_file():
        raise FileNotFoundError(
            f"No astra.yaml at {project_path}; cannot export."
        )

    spec = resolve_analysis_tree(load_yaml(spec_path), project_path)
    project_name = (spec.get("name") or project_path.name).lower().replace(" ", "-")

    # Resolve which universes to include. If the caller didn't restrict,
    # discover from the universes/ directory.
    if universes is None:
        universes = _discover_all_universes(project_path)

    crate = ROCrate()
    crate.name = spec.get("name") or project_name
    # RO-Crate REQUIRES a description on the root. Fall back to a
    # generated one when astra.yaml doesn't define one.
    crate.description = spec.get("description") or (
        f"WRROC bundle exported from {crate.name} on "
        f"{_format_finished_at(_now())}."
    )
    crate.creativeWorkStatus = "Published"

    # License — required by Workflow RO-Crate. Caller supplies it
    # explicitly or we fall back to a permissive default.
    license_url = license or spec.get("license") or DEFAULT_LICENSE
    crate.root_dataset["license"] = {"@id": license_url}

    # Mark the root as a workflow run crate.
    crate.root_dataset["conformsTo"] = [
        {"@id": PROCESS_RUN_CRATE_PROFILE},
        {"@id": WORKFLOW_RUN_CRATE_PROFILE},
        {"@id": PROVENANCE_RUN_CRATE_PROFILE},
    ]
    # The validator expects each profile URL referenced by conformsTo to
    # also exist as a CreativeWork entity in the @graph — declare them.
    from rocrate.model import ContextEntity
    for profile_url, profile_name in [
        (PROCESS_RUN_CRATE_PROFILE, "Process Run Crate"),
        (WORKFLOW_RUN_CRATE_PROFILE, "Workflow Run Crate"),
        (PROVENANCE_RUN_CRATE_PROFILE, "Provenance Run Crate"),
    ]:
        crate.add(ContextEntity(crate, profile_url, properties={
            "@type": "CreativeWork",
            "name": f"{profile_name} 0.5",
            "version": "0.5",
        }))
    # The metadata file descriptor itself must conformsTo a specific
    # RO-Crate spec version. rocrate-py 0.15 emits a 1.2 @context but
    # the validator looks for a conformsTo on the descriptor — set both
    # 1.1 (for backward-compat validators) and 1.2 explicitly.
    crate.metadata["conformsTo"] = [
        {"@id": "https://w3id.org/ro/crate/1.1"},
        {"@id": "https://w3id.org/ro/crate/1.2"},
    ]

    builder = WRROCBuilder(
        crate=crate,
        project_path=project_path,
        spec=spec,
        author_str=author or _detect_author(project_path),
        include_data=include_data,
    )

    # Add the workflow definition (astra.yaml).
    builder.add_workflow()

    # Walk each universe's outputs and emit Datasets + CreateActions.
    runs_added = 0
    included_universes: list[str] = []
    tree_outputs = collect_tree_outputs(spec)

    for universe_id in universes:
        universe_runs = builder.add_universe_runs(universe_id, tree_outputs)
        if universe_runs > 0:
            runs_added += universe_runs
            included_universes.append(universe_id)

    if runs_added == 0:
        logger.warning(
            "No materialized outputs found for universes %s — bundle will "
            "contain only the workflow definition.",
            universes,
        )

    # Render the bundle.
    output_path = Path(output_path).resolve()

    if zip_bundle:
        if output_path.exists():
            if output_path.is_dir():
                raise FileExistsError(
                    f"{output_path} is an existing directory; cannot overwrite with a zip. "
                    "Pass a file path (e.g. bundle.zip) or remove the existing directory."
                )
            output_path.unlink()
        crate.write_zip(output_path)
        result_path = output_path
    else:
        if output_path.exists() and any(output_path.iterdir()):
            raise FileExistsError(
                f"{output_path} is non-empty; refuse to clobber. "
                "Pass a fresh path or remove the existing one."
            )
        crate.write(output_path)
        result_path = output_path

    return ExportResult(
        bundle_path=result_path,
        runs_included=runs_added,
        universes_included=included_universes,
        is_zip=zip_bundle,
    )


# ---------------------------------------------------------------------------
# Builder — accumulates entities into a single ROCrate instance
# ---------------------------------------------------------------------------


class WRROCBuilder:
    """Accumulates lightcone state into a WRROC ``ROCrate`` instance.

    The builder owns the @id minting strategy and the de-duplication of
    repeated entities (e.g. the same recipe ``SoftwareApplication`` is
    used by multiple ``CreateAction`` runs).
    """

    def __init__(
        self,
        crate: Any,  # rocrate.rocrate.ROCrate
        project_path: Path,
        spec: dict[str, Any],
        author_str: str | None,
        include_data: bool,
    ) -> None:
        self.crate = crate
        self.project_path = project_path
        self.spec = spec
        self.include_data = include_data

        self._workflow_id: str | None = None
        self._dataset_ids: dict[tuple[str, str], str] = {}  # (universe, output) → @id
        self._software_ids: dict[str, str] = {}  # recipe text → @id
        self._container_ids: dict[str, str] = {}  # image tag → @id
        self._person_id: str | None = None
        self._code_repo_id: str | None = None  # set lazily from manifest's git_remote

        if author_str:
            self._person_id = self._add_person(author_str)

    # ----- Workflow + universes -----

    def add_workflow(self) -> str:
        """Add the astra.yaml as a ``ComputationalWorkflow`` entity."""
        from rocrate.model import ContextEntity

        spec_path = self.project_path / "astra.yaml"
        wf_id = "astra.yaml"
        # Also bundle the file itself (always — it's small and central).
        # WRROC requires a known ComputerLanguage. astra.yaml is a spec
        # over Snakemake (the actual executor), so we tag the workflow
        # language as "snakemake" — the truthful description of what runs.
        wf = self.crate.add_workflow(
            spec_path,
            wf_id,
            main=True,
            lang="snakemake",
        )
        wf["name"] = self.spec.get("name") or "ASTRA analysis"
        if "description" in self.spec:
            wf["description"] = self.spec["description"]

        # Decisions: declare each decision (root + sub-analysis) as a
        # FormalParameter of the workflow. Per-universe values get
        # attached as PropertyValue on the CreateAction (see
        # add_universe_runs). ASTRA's decisions are a dict keyed by id;
        # use get_decisions() so sub-analysis decisions are merged in.
        for decision_id, decision in get_decisions(self.spec).items():
            param_id = f"#param-{decision_id}"
            # additionalType is REQUIRED by the WRROC FormalParameter
            # shape. Infer from the default value (or first option) so
            # we report the right schema.org primitive.
            sample_val = _decision_sample_value(decision)
            param = ContextEntity(
                self.crate,
                param_id,
                properties={
                    "@type": "FormalParameter",
                    "name": decision_id,
                    "description": (
                        decision.get("rationale")
                        or decision.get("label")
                        or decision.get("description", "")
                    ),
                    "additionalType": _infer_additional_type(sample_val),
                },
            )
            self.crate.add(param)
            wf.append_to("input", param)

        # Bundle universe files for full reproducibility.
        universes_dir = self.project_path / "universes"
        if universes_dir.is_dir():
            for u_file in sorted(universes_dir.glob("*.yaml")):
                rel = u_file.relative_to(self.project_path)
                self.crate.add_file(u_file, str(rel))

        self._workflow_id = wf_id
        return wf_id

    def add_universe_runs(
        self,
        universe_id: str,
        tree_outputs: list[TreeOutput],
    ) -> int:
        """Add Datasets + CreateActions for every materialized output in the
        given universe. Returns the count of CreateActions added.
        """
        decisions = _safe_load_universe_decisions(
            self.project_path, self.spec, universe_id
        )
        runs_added = 0

        for tree_out in tree_outputs:
            recipe = tree_out.output_def.get("recipe")
            if recipe is None:  # alias output, no own materialization
                continue

            out_dir = (
                resolve_output_path(self.project_path, tree_out, universe_id)
                / tree_out.output_id
            )
            # Best-effort manifest read: skip outputs whose directory
            # is unreadable (permission-denied scratch entries, broken
            # symlinks, mid-rsync states). For `lc export`, partial
            # bundles are more useful than a total abort.
            try:
                manifest = read_manifest(out_dir)
            except OSError as exc:
                logger.warning(
                    "Skipping %s/%s: cannot read manifest (%s)",
                    universe_id, tree_out.output_id, exc,
                )
                continue
            if manifest is None:
                continue  # not yet materialized in this universe

            dataset_id = self._add_output_dataset(
                tree_out, universe_id, out_dir, manifest
            )
            self._add_create_action(
                tree_out=tree_out,
                universe_id=universe_id,
                dataset_id=dataset_id,
                manifest=manifest,
                decisions=decisions,
                tree_outputs=tree_outputs,
            )
            runs_added += 1

        return runs_added

    # ----- Datasets / runs -----

    def _add_output_dataset(
        self,
        tree_out: TreeOutput,
        universe_id: str,
        out_dir: Path,
        manifest: dict[str, Any],
    ) -> str:
        """Add a ``Dataset`` for an output directory and return its @id."""
        rel_dir = out_dir.relative_to(self.project_path).as_posix()
        dataset_id = rel_dir + "/"

        # If this dataset was already added (which can happen if the user
        # passes overlapping universes), return the existing @id.
        cache_key = (universe_id, tree_out.output_id)
        if cache_key in self._dataset_ids:
            return self._dataset_ids[cache_key]

        if self.include_data:
            self.crate.add_dataset(out_dir, dataset_id)
        else:
            # Metadata-only mode: we still want the dataset in the graph
            # for chain integrity, but we don't copy data files. Bundle
            # only the manifest itself.
            self.crate.add_dataset(None, dataset_id)
            manifest_src = out_dir / MANIFEST_FILENAME
            if manifest_src.exists():
                self.crate.add_file(
                    manifest_src,
                    f"{dataset_id}{MANIFEST_FILENAME}",
                )

        ds = self.crate.dereference(dataset_id)
        ds["name"] = f"{tree_out.output_id} (universe={universe_id})"
        # schema.org's `version` is the standard place for a content hash
        # / version identifier on a Dataset. dataVersion (lightcone term)
        # is not in the RO-Crate context so the validator rejects it.
        if data_version := manifest.get("data_version"):
            ds["version"] = data_version

        self._dataset_ids[cache_key] = dataset_id
        return dataset_id

    def _add_create_action(
        self,
        *,
        tree_out: TreeOutput,
        universe_id: str,
        dataset_id: str,
        manifest: dict[str, Any],
        decisions: dict[str, Any],
        tree_outputs: list[TreeOutput],
    ) -> str:
        """Add a ``CreateAction`` linking inputs → instrument → output."""
        from rocrate.model import ContextEntity

        action_id = f"#run-{universe_id}-{_qualified_id(tree_out)}"
        recipe_cmd = (tree_out.output_def.get("recipe") or {}).get("command", "")

        instrument_id = self._add_recipe_software(
            recipe_cmd,
            manifest.get("container_image"),
            tool_name=(tree_out.output_def.get("recipe") or {}).get("tool_name"),
            output_id=tree_out.output_id,
        )

        # If the manifest carries a git_remote URL, surface it as a
        # CodeRepository entity once (de-duplicated across all actions).
        self._link_code_repository(manifest.get("git_remote"))

        # Resolve `object` (inputs to the action). Each upstream input
        # references the producing dataset's @id (Provenance chain).
        # External inputs we represent as ContextEntity File-with-fingerprint.
        objects: list[dict[str, str]] = []
        for inp_id, version_str in (manifest.get("input_versions") or {}).items():
            obj_ref = self._resolve_input_reference(
                inp_id=inp_id,
                version_str=version_str,
                consumer=tree_out,
                universe_id=universe_id,
                tree_outputs=tree_outputs,
            )
            if obj_ref is not None:
                objects.append(obj_ref)

        properties: dict[str, Any] = {
            "@type": "CreateAction",
            "name": f"Run of {tree_out.output_id} (universe={universe_id})",
            "instrument": {"@id": instrument_id},
            "object": objects,
            "result": [{"@id": dataset_id}],
            "endTime": _format_finished_at(manifest.get("finished_at")),
            "actionStatus": {"@id": "http://schema.org/CompletedActionStatus"},
        }
        if self._person_id:
            properties["agent"] = {"@id": self._person_id}

        action = ContextEntity(self.crate, action_id, properties=properties)
        self.crate.add(action)

        # Per-decision PropertyValue entities, attached to the action as
        # parameter values rather than to the workflow (so multiple
        # universes don't trample each other).
        for d_id, d_value in decisions.items():
            pv_id = f"#pv-{universe_id}-{_qualified_id(tree_out)}-{d_id}"
            pv = ContextEntity(
                self.crate,
                pv_id,
                properties={
                    "@type": "PropertyValue",
                    "name": d_id,
                    "value": _coerce_value(d_value),
                },
            )
            self.crate.add(pv)
            action.append_to("object", pv)

        # Workflow & manifest provenance metadata
        action.append_to(
            "object",
            self._add_property_value(
                f"#pv-{universe_id}-{_qualified_id(tree_out)}-code_version",
                "code_version",
                manifest.get("code_version", ""),
            ),
        )
        action.append_to(
            "object",
            self._add_property_value(
                f"#pv-{universe_id}-{_qualified_id(tree_out)}-data_version",
                "data_version",
                manifest.get("data_version", ""),
            ),
        )

        return action_id

    # ----- Resolved references -----

    def _resolve_input_reference(
        self,
        *,
        inp_id: str,
        version_str: str,
        consumer: TreeOutput,
        universe_id: str,
        tree_outputs: list[TreeOutput],
    ) -> dict[str, str] | None:
        """Return a ``{"@id": ...}`` reference for an action's input.

        For upstream-produced inputs, the @id points at the producing
        dataset. For external inputs, we synthesize a File ContextEntity
        with the fingerprint as its sha256 / mtime-size note.
        """
        from lightcone.engine.tree import find_upstream_output

        upstream = find_upstream_output(consumer, inp_id, tree_outputs)
        if upstream is not None:
            # Reference an existing dataset @id (must already have been
            # added — order-preserving collect_tree_outputs handles the
            # common case; out-of-order DAGs still work because rocrate
            # tolerates forward refs at write time).
            cache_key = (universe_id, upstream.output_id)
            if cache_key in self._dataset_ids:
                return {"@id": self._dataset_ids[cache_key]}
            # Forward reference: predict the @id deterministically.
            out_dir = (
                resolve_output_path(self.project_path, upstream, universe_id)
                / upstream.output_id
            )
            return {
                "@id": out_dir.relative_to(self.project_path).as_posix() + "/"
            }

        # External input. Synthesize a stable @id from the input id +
        # fingerprint so identical files de-duplicate across runs.
        external_id = f"#ext-{inp_id}"
        if not self.crate.dereference(external_id):
            from rocrate.model import ContextEntity

            ext = ContextEntity(
                self.crate,
                external_id,
                properties={
                    "@type": "File",
                    "name": inp_id,
                    "description": f"External input fingerprint: {version_str}",
                },
            )
            # Encode the fingerprint as a checksum/contentSize note.
            if version_str.startswith("sha256:"):
                ext["sha256"] = version_str.removeprefix("sha256:")
            else:
                ext["fingerprint"] = version_str
            self.crate.add(ext)
        return {"@id": external_id}

    def _link_code_repository(self, git_remote: str | None) -> None:
        """Idempotently add a CodeRepository entity for the project repo.

        Called once per manifest read; later calls with the same URL are
        no-ops. The repository entity is also linked from the workflow
        via ``codeRepository`` so consumers can discover the source.
        """
        if not git_remote:
            return
        if self._code_repo_id is not None:
            return  # already added
        from rocrate.model import ContextEntity

        repo = ContextEntity(self.crate, git_remote, properties={
            "@type": ["CodeRepository", "SoftwareSourceCode"],
            "name": git_remote.rsplit("/", 1)[-1] or git_remote,
            "url": git_remote,
        })
        self.crate.add(repo)
        if self._workflow_id is not None:
            wf = self.crate.dereference(self._workflow_id)
            if wf is not None:
                wf["codeRepository"] = {"@id": git_remote}
        self._code_repo_id = git_remote

    def _add_recipe_software(
        self,
        recipe_cmd: str,
        container_image: str | None,
        *,
        tool_name: str | None = None,
        output_id: str | None = None,
    ) -> str:
        """De-duplicate recipes — return the @id of the SoftwareApplication.

        ``SoftwareApplication.name`` resolution order:

        1. Explicit ``recipe.tool_name`` from astra.yaml (best — author-chosen).
        2. Heuristic from the command (e.g. ``scripts/analyze.py``).
        3. The output id (always available, always meaningful).

        The full command is always preserved as ``description``.
        """
        from rocrate.model import ContextEntity

        key = recipe_cmd or "<empty>"
        if key in self._software_ids:
            return self._software_ids[key]

        sw_id = f"#recipe-{len(self._software_ids)}"
        name = (
            tool_name
            or _heuristic_tool_name(recipe_cmd)
            or output_id
            or "(empty recipe)"
        )
        props: dict[str, Any] = {
            "@type": "SoftwareApplication",
            "name": name,
            "description": recipe_cmd,
        }
        if container_image:
            props["softwareRequirements"] = {
                "@id": self._add_container_software(container_image)
            }
        sw = ContextEntity(self.crate, sw_id, properties=props)
        self.crate.add(sw)

        # WRROC: ComputationalWorkflow MUST refer to its orchestrated
        # tools via hasPart. Link each recipe back to the workflow.
        if self._workflow_id is not None:
            wf = self.crate.dereference(self._workflow_id)
            if wf is not None:
                wf.append_to("hasPart", sw)

        self._software_ids[key] = sw_id
        return sw_id

    def _add_container_software(self, image_tag: str) -> str:
        """De-duplicate container images."""
        from rocrate.model import ContextEntity

        if image_tag in self._container_ids:
            return self._container_ids[image_tag]
        cid = f"#container-{len(self._container_ids)}"
        sw = ContextEntity(
            self.crate,
            cid,
            properties={
                "@type": ["SoftwareApplication", "ContainerImage"],
                "name": image_tag,
                "softwareVersion": image_tag,
            },
        )
        self.crate.add(sw)
        self._container_ids[image_tag] = cid
        return cid

    def _add_property_value(self, pv_id: str, name: str, value: Any) -> Any:
        from rocrate.model import ContextEntity

        existing = self.crate.dereference(pv_id)
        if existing:
            return existing
        pv = ContextEntity(
            self.crate,
            pv_id,
            properties={
                "@type": "PropertyValue",
                "name": name,
                "value": _coerce_value(value),
            },
        )
        self.crate.add(pv)
        return pv

    def _add_person(self, author_str: str) -> str:
        from rocrate.model import ContextEntity

        name, email = _parse_author(author_str)
        person_id = f"#author-{(email or name).replace('@', '_at_')}"
        if self.crate.dereference(person_id):
            return person_id
        props: dict[str, Any] = {"@type": "Person", "name": name}
        if email:
            props["email"] = email
        person = ContextEntity(self.crate, person_id, properties=props)
        self.crate.add(person)
        return person_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _qualified_id(tree_out: TreeOutput) -> str:
    """A filesystem-safe qualified id for use in @id minting."""
    if tree_out.analysis_id:
        return f"{tree_out.analysis_id}.{tree_out.output_id}"
    return tree_out.output_id


def _discover_all_universes(project_path: Path) -> list[str]:
    """List every universe with at least one universe yaml present."""
    universes_dir = project_path / "universes"
    if not universes_dir.exists():
        return ["baseline"]
    ids = sorted(p.stem for p in universes_dir.glob("*.yaml"))
    return ids or ["baseline"]


def _safe_load_universe_decisions(
    project_path: Path,
    spec: dict[str, Any],
    universe_id: str,
) -> dict[str, Any]:
    """Like resolve_universe_decisions but tolerant of missing/unreadable files."""
    universe_yaml = project_path / "universes" / f"{universe_id}.yaml"
    try:
        if not universe_yaml.exists():
            return {}
        return resolve_universe_decisions(project_path, spec, universe_id)
    except (FileNotFoundError, KeyError, OSError):
        return {}


def _decision_sample_value(decision: dict[str, Any]) -> Any:
    """Pick a representative value for a decision to infer its type.

    ASTRA's decisions schema uses a dict of options keyed by option id
    (e.g. ``options: {bins_8: {label: '8 bins'}, ...}``). The default is
    typically one of those keys. For type inference, prefer:

    1. The `default` value if set (always a primitive).
    2. The first option key if options is a dict.
    3. The first option's `value` field if options is a list.
    """
    if "default" in decision:
        return decision["default"]
    opts = decision.get("options")
    if isinstance(opts, dict) and opts:
        return next(iter(opts))
    if isinstance(opts, list) and opts:
        first = opts[0]
        if isinstance(first, dict):
            return first.get("value")
        return first
    return None


def _heuristic_tool_name(recipe_cmd: str) -> str | None:
    """Best-effort SoftwareApplication.name from a bash command.

    Looks for the first script-like token (``foo.py``, ``./bin/foo``,
    ``foo.sh``) and returns just that. Returns None if no obvious tool
    can be extracted, falling through to the output_id fallback.
    """
    if not recipe_cmd:
        return None
    for token in recipe_cmd.split():
        # Skip env assignments, redirects, shell builtins
        if "=" in token and not token.startswith("-"):
            continue
        if token in {"python", "python3", "bash", "sh", "uv", "run"}:
            continue
        # Must look like a path with an extension or a leading ./
        if "/" in token or token.startswith("./"):
            return token
        if "." in token and not token.startswith("-"):
            ext = token.rsplit(".", 1)[-1]
            if ext in {"py", "sh", "R", "jl", "rb", "pl"}:
                return token
    return None


def _infer_additional_type(value: Any) -> dict[str, str]:
    """Map a sample decision value to a schema.org primitive type @id.

    WRROC's FormalParameter shape requires ``additionalType`` to indicate
    the parameter's expected value type — Text/Integer/Float/Boolean.
    """
    if isinstance(value, bool):
        return {"@id": "http://schema.org/Boolean"}
    if isinstance(value, int):
        return {"@id": "http://schema.org/Integer"}
    if isinstance(value, float):
        return {"@id": "http://schema.org/Float"}
    return {"@id": "http://schema.org/Text"}


def _coerce_value(value: Any) -> Any:
    """schema.org PropertyValue.value should be a primitive (str/num/bool).

    Lightcone decisions can be arbitrary YAML — coerce non-primitives to
    a JSON string so the PropertyValue stays valid.
    """
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    import json as _json
    return _json.dumps(value, sort_keys=True)


def _format_finished_at(ts: float | None) -> str | None:
    """Convert a unix timestamp to ISO 8601 for schema:endTime."""
    if ts is None:
        return None
    from datetime import UTC, datetime
    return datetime.fromtimestamp(ts, tz=UTC).isoformat()


def _now() -> float:
    import time
    return time.time()


def _parse_author(s: str) -> tuple[str, str | None]:
    """Parse ``"Name <email@host>"`` or just ``"Name"`` into (name, email)."""
    s = s.strip()
    if "<" in s and s.endswith(">"):
        name, _, rest = s.rpartition("<")
        return name.strip(), rest.removesuffix(">").strip() or None
    return s, None


def _detect_author(project_path: Path) -> str | None:
    """Pull author from git config or environment, else return None."""
    if env := os.environ.get("LIGHTCONE_AUTHOR"):
        return env
    try:
        name = subprocess.run(
            ["git", "config", "user.name"],
            cwd=project_path, capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        email = subprocess.run(
            ["git", "config", "user.email"],
            cwd=project_path, capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        if name and email:
            return f"{name} <{email}>"
        return name or None
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None


__all__ = [
    "ExportResult",
    "PROCESS_RUN_CRATE_PROFILE",
    "PROVENANCE_RUN_CRATE_PROFILE",
    "WORKFLOW_RUN_CRATE_PROFILE",
    "WRROCBuilder",
    "export_wrroc",
]
