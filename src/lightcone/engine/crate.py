"""The publication view: the repository described as a Workflow Run RO-Crate.

The project *is* the crate. ``ro-crate-metadata.json`` sits at the root
and describes what the repository already holds — the spec, the lock, the
universes, each materialized output and the run that made it — so a
deposit is ``git archive`` on the repository, not an export step that
copies bytes. lc's manifests stay the canonical record; the crate is the
same facts in schema.org vocabulary, readable by archives and viewers
that will never run ``lc``.

The document is a pure function of repository state: :func:`render`
takes the graph, the license, and a way to ask git who last wrote each
output, and returns bytes that are identical when nothing changed. That
is what lets ``lc materialize`` converge the file the way it converges
``uv.lock`` — compare, and commit only a difference — with no verb for a
human to remember.

Profiles targeted: Process, Workflow and Provenance Run Crate 0.5, plus
Workflow RO-Crate 1.0. The Provenance layer is structural, not claimed:
one ``lc materialize`` invocation is one ``OrganizeAction`` (outputs of a
run share their manifests' ``git_sha``, because the driver reads HEAD
once and hands it down), each output's execution is a ``CreateAction``
steered by a ``ControlAction``, and the spec's outputs are the
workflow's ``HowToStep`` s.
"""

from __future__ import annotations

import json
import tomllib
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rocrate.model import ContextEntity
from rocrate.model.computerlanguage import ComputerLanguage
from rocrate.rocrate import ROCrate

from lightcone.engine import assets, plan
from lightcone.engine.plan import Graph, Key

CRATE_FILENAME = "ro-crate-metadata.json"

#: The vocabulary the run-level facts come from. Without it in the
#: ``@context``, terms like ``containerImage`` and ``sha256`` are
#: undefined and silently dropped on JSON-LD expansion — typed but inert.
_WORKFLOW_RUN_CONTEXT = "https://w3id.org/ro/terms/workflow-run"

#: What the root conforms to, each also declared as a CreativeWork in the
#: graph — consumers resolve the reference inside the crate, not on the
#: network.
_PROFILES = (
    ("https://w3id.org/ro/wfrun/process/0.5", "Process Run Crate", "0.5"),
    ("https://w3id.org/ro/wfrun/workflow/0.5", "Workflow Run Crate", "0.5"),
    ("https://w3id.org/ro/wfrun/provenance/0.5", "Provenance Run Crate", "0.5"),
    ("https://w3id.org/workflowhub/workflow-ro-crate/1.0", "Workflow RO-Crate", "1.0"),
)

#: What one commit that last touched a path looks like:
#: ``(sha, subject, author name, author email, author date)`` — the shape
#: :func:`lightcone.engine.dataset.last_writer` returns.
LastWrite = tuple[str, str, str, str, str]


def license_of(root: Path) -> str:
    """Read the project's declared license out of ``pyproject.toml``.

    Presence is what turns crate maintenance on: RO-Crate requires a
    license, a run must not refuse over a missing key, and inventing one
    would assert terms over someone's data — so declaring
    ``[project].license`` is declaring the intent to publish.

    Args:
        root: The project root.

    Returns:
        The license as declared — an SPDX expression, a URL, free text,
        or a file path for the table forms — or empty when undeclared.
    """
    try:
        data = tomllib.loads((root / "pyproject.toml").read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return ""
    declared = data.get("project", {}).get("license")
    if isinstance(declared, str):
        return declared
    if isinstance(declared, dict):
        return str(declared.get("text") or declared.get("file") or "")
    return ""


def render(
    root: Path,
    graph: Graph,
    *,
    license: str,
    dsid: str,
    writer: Callable[[Path], LastWrite],
    engine_url: str = "",
) -> str:
    """Build the crate document for the project as it stands.

    Pure given its arguments: reads the tree, runs nothing, and returns
    identical bytes for identical repository state — timestamps come from
    manifests and commits, never from the clock, and entities are built
    in sorted order.

    Args:
        root: The project root.
        graph: The full task graph — every universe, every output.
        license: The declared license, from :func:`license_of`.
        dsid: The dataset UUID, the namespace absolute entity ids are
            minted under so they are stable across clones.
        writer: Answers "which commit last touched this path" —
            :func:`dataset.last_writer` bound to the root, injected so
            the builder stays free of git.
        engine_url: The engine's repository URL, or empty.

    Returns:
        The ``ro-crate-metadata.json`` text, trailing newline included.
    """
    build = _Builder(root, graph, license, dsid, writer, engine_url)
    return build.document()


class _Builder:
    """One render: accumulates entities into a ``ROCrate``, in one order."""

    def __init__(
        self,
        root: Path,
        graph: Graph,
        license: str,
        dsid: str,
        writer: Callable[[Path], LastWrite],
        engine_url: str,
    ) -> None:
        self.root = root
        self.graph = graph
        self.license = license
        self.dsid = dsid
        self.writer = writer
        self.engine_url = engine_url
        self.crate = ROCrate()
        self.crate.metadata.extra_contexts.append(_WORKFLOW_RUN_CONTEXT)
        #: Every materialized task, sorted: the one iteration order.
        self.made: list[tuple[Key, assets.Manifest]] = sorted(
            (
                (key, manifest)
                for key, task in graph.tasks.items()
                if (manifest := assets.read(task.output_dir)) is not None
            ),
            key=lambda pair: pair[0],
        )
        self.persons: dict[str, str] = {}  # email/name → @id
        self.images: dict[str, str] = {}  # archive path → @id
        self.engines: dict[str, str] = {}  # lc_version → @id
        self.parameters: set[str] = set()  # decision ids declared on the workflow
        self.actions: list[ContextEntity] = []
        #: Loop invariants, asked once: the uuid5 namespace every minted
        #: id lives under, and the project's one recorded remote.
        self.namespace = uuid.uuid5(uuid.NAMESPACE_URL, dsid)
        remotes = {m.git_remote for _, m in self.made if m.git_remote}
        self.remote = remotes.pop() if len(remotes) == 1 else ""

    def document(self) -> str:
        """Assemble the graph and serialize it, deterministically."""
        spec = self._spec()
        workflow = self._workflow(spec)
        self._steps_and_tools(workflow)
        self._environment_files()
        datasets = {key: self._dataset(key, manifest) for key, manifest in self.made}
        for key, manifest in self.made:
            self._control(key, self._action(key, manifest, datasets))
        self._runs(workflow, datasets)
        self._root(spec)
        text: str = json.dumps(
            self.crate.metadata.generate(), indent=1, sort_keys=True, ensure_ascii=False
        )
        return text + "\n"

    # ----- the workflow and its structure -----

    def _spec(self) -> dict[str, Any]:
        from astra.helpers import load_yaml

        loaded = load_yaml(self.root / "astra.yaml")
        return dict(loaded) if isinstance(loaded, dict) else {}

    def _workflow(self, spec: dict[str, Any]) -> Any:
        # Before `add_workflow` rewrites the descriptor's conformsTo: 1.1
        # is what RO-Crate validators key on, 1.2 is the context actually
        # emitted — the crate is structurally both, and saying only the
        # newer one reads as conforming to neither.
        self.crate.metadata["conformsTo"] = [
            {"@id": "https://w3id.org/ro/crate/1.1"},
            {"@id": "https://w3id.org/ro/crate/1.2"},
        ]
        lang = ComputerLanguage(
            self.crate,
            "#astra",
            properties={
                "@type": "ComputerLanguage",
                "name": "ASTRA",
                "alternateName": "Agentic Schema for Transparent Research Analysis",
                "url": "https://pypi.org/project/astra-tools/",
            },
        )
        self.crate.add(lang)
        sha, _, author, email, date = self.writer(self.root / "astra.yaml")
        workflow = self.crate.add_workflow(
            self.root / "astra.yaml",
            "astra.yaml",
            main=True,
            lang=lang,
            properties={
                # `HowTo` is what licenses the `step` list below — the
                # Provenance profile's requirement, not decoration.
                "@type": ["File", "SoftwareSourceCode", "ComputationalWorkflow", "HowTo"],
                "name": str(spec.get("name") or self.root.name),
                "encodingFormat": "application/yaml",
            },
        )
        if description := spec.get("description"):
            workflow["description"] = str(description)
        workflow["conformsTo"] = {
            "@id": "https://bioschemas.org/profiles/ComputationalWorkflow/1.0-RELEASE"
        }
        workflow["license"] = self._license_ref()
        if self.remote:
            workflow["url"] = self.remote
        if sha:
            # The spec's version is the commit that last changed it — the
            # only version an astra.yaml actually has.
            workflow["version"] = sha[:7]
            workflow["dateCreated"] = date
            workflow["creator"] = {"@id": self._person(author, email)}
        self.parameters = {d for _, t in self.graph.tasks.items() for d in t.decisions}
        for decision in sorted(self.parameters):
            parameter = ContextEntity(
                self.crate,
                f"#param-{decision}",
                properties={
                    "@type": "FormalParameter",
                    "name": decision,
                    # ASTRA decisions arrive rendered, as strings.
                    "additionalType": "Text",
                },
            )
            self.crate.add(parameter)
            workflow.append_to("input", parameter)
        return workflow

    def _steps_and_tools(self, workflow: Any) -> None:
        for position, output_id in enumerate(self._output_ids()):
            tool = ContextEntity(
                self.crate,
                self._tool_id(output_id),
                properties={
                    "@type": "SoftwareApplication",
                    "name": output_id,
                    "description": f"the recipe of output `{output_id}`",
                },
            )
            if self.remote:
                tool["url"] = self.remote
            if version := self._spec_sha():
                tool["softwareVersion"] = version
            self.crate.add(tool)
            step = ContextEntity(
                self.crate,
                f"#step-{output_id}",
                properties={
                    "@type": "HowToStep",
                    "position": position,
                    "workExample": {"@id": tool.id},
                },
            )
            self.crate.add(step)
            workflow.append_to("step", step)
            # The Provenance profile's one MUST on the workflow itself:
            # the tools it orchestrates are its parts.
            workflow.append_to("hasPart", tool)

    def _output_ids(self) -> list[str]:
        return sorted({output_id for _, output_id in self.graph.tasks})

    def _spec_sha(self) -> str:
        sha = self.writer(self.root / "astra.yaml")[0]
        return sha[:7]

    def _tool_id(self, output_id: str) -> str:
        """An id for one output's recipe — under the project's own
        repository URL when it has one (an http URI, which is what the
        profiles prefer for an application), a ``urn:uuid`` otherwise."""
        if self.remote:
            return f"{self.remote}#tool/{output_id}"
        return self._uri(f"tool/{output_id}")

    # ----- the data entities -----

    def _environment_files(self) -> None:
        """The lock and its companions — what every run consumed."""
        for name in ("pyproject.toml", "uv.lock", ".python-version", *self._universe_files()):
            if (self.root / name).is_file():
                self._file(name)
        if (self.root / "README.md").is_file():
            readme = self._file("README.md")
            readme["about"] = {"@id": "./"}

    def _universe_files(self) -> list[str]:
        return sorted(
            path.relative_to(self.root).as_posix()
            for path in (self.root / "universes").glob("*.yaml")
        )

    def _file(self, name: str, **extra: Any) -> Any:
        properties = dict(extra)
        if fmt := _format_of(name):
            properties["encodingFormat"] = fmt
        return self.crate.add_file(self.root / name, name, properties=properties)

    def _dataset(self, key: Key, manifest: assets.Manifest) -> str:
        universe_id, output_id = key
        dataset_id = f"results/{universe_id}/{output_id}/"
        entity = self.crate.add_dataset(self.graph.tasks[key].output_dir, dataset_id)
        entity["name"] = f"{output_id} (universe {universe_id})"
        entity["description"] = f"output `{output_id}` materialized under `{universe_id}`"
        entity["version"] = manifest.data_version
        manifest_file = self._file(f"{dataset_id}{assets.MANIFEST_FILENAME}")
        manifest_file["about"] = {"@id": dataset_id}
        entity["subjectOf"] = {"@id": manifest_file.id}
        return dataset_id

    # ----- the runs -----

    def _action(
        self, key: Key, manifest: assets.Manifest, datasets: dict[Key, str]
    ) -> ContextEntity:
        universe_id, output_id = key
        task = self.graph.tasks[key]
        sha, _, author, email, _ = self.writer(task.output_dir)
        properties: dict[str, Any] = {
            "@type": "CreateAction",
            "name": f"run of `{output_id}` in universe `{universe_id}`",
            # The *recorded* recipe, not the graph's: the action states
            # what ran, and the spec may have moved since — the manifest
            # is the canonical record of the execution.
            "description": manifest.recipe,
            "instrument": {"@id": self._tool_id(output_id)},
            "result": [{"@id": datasets[key]}],
            "actionStatus": "http://schema.org/CompletedActionStatus",
            "object": self._objects(key, manifest, datasets),
        }
        if manifest.started_at:
            properties["startTime"] = manifest.started_at
        if manifest.finished_at:
            properties["endTime"] = manifest.finished_at
        if sha:
            properties["agent"] = {"@id": self._person(author, email)}
        if manifest.image is not None:
            properties["containerImage"] = {"@id": self._image(manifest.image)}
        action = ContextEntity(
            self.crate, self._uri(f"action/{universe_id}/{output_id}"), properties
        )
        self.crate.add(action)
        self.actions.append(action)
        return action

    def _objects(
        self, key: Key, manifest: assets.Manifest, datasets: dict[Key, str]
    ) -> list[dict[str, str]]:
        task = self.graph.tasks[key]
        refs = [
            {"@id": name}
            for name in ("uv.lock", ".python-version", "pyproject.toml")
            if (self.root / name).is_file()
        ]
        for name in sorted(task.inputs):
            upstream = task.produced_by.get(name)
            if upstream is not None:
                if upstream in datasets:
                    refs.append({"@id": datasets[upstream]})
                continue
            refs.append({"@id": self._external(name, task.inputs[name], manifest)})
        # The *recorded* decisions: the values the recipe actually ran
        # under, whatever the spec says today. A decision the workflow no
        # longer declares gets no exampleOfWork — there is no parameter
        # for it to exemplify.
        for decision in sorted(manifest.decisions):
            properties: dict[str, Any] = {
                "@type": "PropertyValue",
                "name": decision,
                "value": manifest.decisions[decision],
            }
            if decision in self.parameters:
                properties["exampleOfWork"] = {"@id": f"#param-{decision}"}
            value = ContextEntity(
                self.crate, f"#value-{key[0]}-{key[1]}-{decision}", properties
            )
            self.crate.add(value)
            refs.append({"@id": value.id})
        return refs

    def _external(self, name: str, path: Path, manifest: assets.Manifest) -> str:
        """A declared input the spec points at, in or out of the tree.

        In-or-out is :func:`plan.declared_path`'s answer — relative
        inside the tree, absolute outside it — never a second spelling
        of that rule here: two copies of one path rule is how the first
        one shipped a bug.
        """
        declared = plan.declared_path(self.root, path)
        in_tree = not Path(declared).is_absolute()
        entity_id = declared if in_tree else Path(declared).as_uri()
        recorded = manifest.input_versions.get(name, "")
        digest = recorded.removeprefix("sha256:") if recorded.startswith("sha256:") else ""
        if (existing := self.crate.dereference(entity_id)) is not None:
            # Two manifests can testify to different bytes for one input
            # — a half-rebuilt project. Publishing either digest would
            # contradict a manifest in the same crate, so publish
            # neither; the manifests themselves keep the full story.
            if digest and existing.get("sha256") not in (None, digest):
                existing.pop("sha256")
            return entity_id
        properties: dict[str, Any] = {"@type": "File", "name": declared}
        if fmt := _format_of(declared):
            properties["encodingFormat"] = fmt
        if digest:
            properties["sha256"] = digest
        if in_tree:
            self.crate.add_file(path, declared, properties=properties)
        else:
            # Outside the repository: recorded by content, not stored
            # in it — the layer's stated weaker promise, so a context
            # entity rather than a data entity the crate cannot hold.
            self.crate.add(ContextEntity(self.crate, entity_id, properties))
        return entity_id

    def _control(self, key: Key, action: ContextEntity) -> ContextEntity:
        universe_id, output_id = key
        control = ContextEntity(
            self.crate,
            f"#control-{universe_id}-{output_id}",
            properties={
                "@type": "ControlAction",
                "name": f"orchestration of `{output_id}` in universe `{universe_id}`",
                "instrument": {"@id": f"#step-{output_id}"},
                "object": {"@id": action.id},
            },
        )
        self.crate.add(control)
        return control

    def _runs(self, workflow: Any, datasets: dict[Key, str]) -> None:
        """One ``OrganizeAction`` per run — outputs sharing a ``git_sha``
        were made by one ``lc materialize``, the driver's one HEAD read."""
        runs: dict[str, list[tuple[Key, assets.Manifest]]] = {}
        for key, manifest in self.made:
            runs.setdefault(manifest.git_sha, []).append((key, manifest))
        for sha in sorted(runs):
            group = runs[sha]
            # The engine *that run* recorded, never the one installed
            # here: the render must be a function of repository state,
            # or two collaborators on different lc versions re-commit
            # the crate at each other forever — and an lc upgrade is
            # recorded as moving nothing. One run has one engine by
            # construction; every manifest of the group agrees.
            engine = self._engine(group[0][1].lc_version)
            times = sorted(t for _, m in group for t in (m.started_at, m.finished_at) if t)
            run_action = ContextEntity(
                self.crate,
                self._uri(f"run/{sha}"),
                properties={
                    "@type": "CreateAction",
                    "name": f"lc materialize at {sha[:7] or 'unknown commit'}",
                    "description": (
                        f"one materialization run, starting from commit {sha or '(unrecorded)'}"
                    ),
                    "instrument": {"@id": workflow.id},
                    "result": [{"@id": datasets[key]} for key, _ in group],
                    "actionStatus": "http://schema.org/CompletedActionStatus",
                },
            )
            if times:
                run_action["startTime"] = times[0]
                run_action["endTime"] = times[-1]
            writer_sha, _, author, email, _ = self.writer(self.graph.tasks[group[0][0]].output_dir)
            if writer_sha:
                run_action["agent"] = {"@id": self._person(author, email)}
            self.crate.add(run_action)
            self.actions.append(run_action)
            organize = ContextEntity(
                self.crate,
                f"#organize-{sha[:7] or 'unknown'}",
                properties={
                    "@type": "OrganizeAction",
                    "name": f"scheduling of the run at {sha[:7] or 'unknown commit'}",
                    "instrument": {"@id": engine},
                    "object": [
                        {"@id": f"#control-{key[0]}-{key[1]}"} for key, _ in group
                    ],
                    "result": {"@id": run_action.id},
                },
            )
            self.crate.add(organize)

    def _engine(self, version: str) -> str:
        """The engine one run's manifests attest, deduplicated by version."""
        if version not in self.engines:
            engine_id = (
                f"https://pypi.org/project/lightcone-cli/{version}/"
                if version
                else "#lightcone-cli"
            )
            properties: dict[str, Any] = {
                "@type": "SoftwareApplication",
                "name": "lightcone-cli",
            }
            if version:
                properties["softwareVersion"] = version
            if self.engine_url:
                properties["url"] = self.engine_url
            self.crate.add(ContextEntity(self.crate, engine_id, properties))
            self.engines[version] = engine_id
        return self.engines[version]

    def _image(self, image: dict[str, Any]) -> str:
        """The committed archive: identity and payload as one entity."""
        archive = str(image.get("archive") or "")
        if archive not in self.images:
            properties: dict[str, Any] = {
                "@type": ["File", "ContainerImage"],
                "name": str(image.get("tag") or archive),
                "tag": str(image.get("tag") or ""),
                "encodingFormat": "application/x-tar",
                # The archive is `docker-archive` format wherever it runs —
                # podman, docker and podman-hpc all consume it as one.
                "additionalType": {"@id": "https://w3id.org/ro/terms/workflow-run#DockerImage"},
            }
            if str(image.get("id") or "").startswith("sha256:"):
                properties["sha256"] = str(image["id"]).removeprefix("sha256:")
            self.crate.add_file(self.root / archive, archive, properties=properties)
            self.images[archive] = archive
        return self.images[archive]

    # ----- shared entities and the root -----

    def _person(self, author: str, email: str) -> str:
        person_key = email or author
        if person_key not in self.persons:
            person_id = f"mailto:{email}" if email else f"#person-{len(self.persons)}"
            self.crate.add(
                ContextEntity(
                    self.crate,
                    person_id,
                    properties={"@type": "Person", "name": author, "email": email},
                )
            )
            self.persons[person_key] = person_id
        return self.persons[person_key]

    def _root(self, spec: dict[str, Any]) -> None:
        root = self.crate.root_dataset
        root["name"] = str(spec.get("name") or self.root.name)
        root["description"] = str(
            spec.get("description")
            or f"ASTRA analysis `{spec.get('name') or self.root.name}`, "
            "materialized by lightcone-cli."
        )
        root["license"] = self._license_ref()
        # The newest recorded instant, never the clock: the document must
        # be a pure function of repository state, or every render is a
        # fresh diff and convergence commits forever.
        stamps = sorted(m.finished_at for _, m in self.made if m.finished_at)
        spec_date = self.writer(self.root / "astra.yaml")[4]
        root["datePublished"] = stamps[-1] if stamps else (spec_date or "1970-01-01")
        root["conformsTo"] = [{"@id": profile_id} for profile_id, _, _ in _PROFILES]
        for profile_id, name, version in _PROFILES:
            self.crate.add(
                ContextEntity(
                    self.crate,
                    profile_id,
                    properties={
                        "@type": "CreativeWork",
                        "name": f"{name} {version}",
                        "version": version,
                    },
                )
            )
        if self.persons:
            root["author"] = [
                {"@id": person_id} for person_id in sorted(self.persons.values())
            ]
        if self.actions:
            root["mentions"] = [{"@id": action.id} for action in self.actions]

    def _license_ref(self) -> Any:
        """The root's license value, always a linkable entity.

        A path in the tree becomes a File data entity; a URL becomes a
        CreativeWork at that URL; anything else — an SPDX id, an SPDX
        expression, free text — becomes a *local* CreativeWork carrying
        the declared string. Deliberately never a minted spdx.org URL:
        the declaration is not validated against the SPDX list, and a
        fabricated dead URL in a document built for archives is worse
        than a local entity.
        """
        if (self.root / self.license).is_file():
            self._file(self.license)
            return {"@id": self.license}
        license_id = (
            self.license
            if self.license.startswith(("http://", "https://"))
            else "#license"
        )
        self.crate.add(
            ContextEntity(
                self.crate,
                license_id,
                properties={"@type": "CreativeWork", "name": self.license},
            )
        )
        return {"@id": license_id}

    def _uri(self, kind: str) -> str:
        """Mint an absolute, deterministic id under the dataset's UUID.

        ``urn:uuid`` from the dsid namespace, so the same entity gets the
        same id in every clone and every render — and an absolute URI is
        what the profiles ask of an application id.
        """
        return f"urn:uuid:{uuid.uuid5(self.namespace, kind)}"


#: A closed suffix → media type map, deliberately not ``mimetypes``:
#: that module reads the host's own tables (``/etc/mime.types``), which
#: would make the rendered document differ between machines — and the
#: document must be a pure function of repository state. Unknown stays
#: unknown; empty means the entity simply carries no ``encodingFormat``.
_FORMATS = {
    ".toml": "application/toml",
    ".lock": "application/toml",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".json": "application/json",
    ".md": "text/markdown",
    ".python-version": "text/plain",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".fits": "image/fits",
    ".h5": "application/x-hdf5",
    ".hdf5": "application/x-hdf5",
    ".parquet": "application/vnd.apache.parquet",
    ".png": "image/png",
    ".pdf": "application/pdf",
}


def _format_of(name: str) -> str:
    """A media type for *name* from the closed map — empty when unknown."""
    return _FORMATS.get(Path(name).suffix or Path(name).name, "")
