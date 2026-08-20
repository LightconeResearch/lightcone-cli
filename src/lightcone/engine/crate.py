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

import hashlib
import json
import re
import tomllib
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from rocrate.model import ContextEntity
from rocrate.model.computerlanguage import ComputerLanguage
from rocrate.rocrate import ROCrate

from lightcone.engine import assets, plan
from lightcone.engine.dataset import LastWrite
from lightcone.engine.plan import Graph, Key
from lightcone.engine.project import SPEC_FILENAME

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

#: The files that pin what a run installed — one spelling, so the crate's
#: data entities and every action's ``object`` list cannot drift apart.
_ENVIRONMENT = ("pyproject.toml", "uv.lock", ".python-version")

#: A SHA-256-backed annex key: ``SHA256E-s<size>--<64 hex><ext>`` (the E
#: backend keeps the extension). The hex *is* the raw sha256 of the
#: content, which is what makes a checksum publishable with none of the
#: bytes fetched. Any other backend yields a size and no digest — never
#: a wrong one.
_SHA256_KEY = re.compile(r"^SHA256E?-s(\d+)--([0-9a-f]{64})(?:\..*)?$")

#: Any backend key's size field, for ``contentSize`` alone.
_KEY_SIZE = re.compile(r"^\w+-s(\d+)")


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
    keys: Mapping[str, str],
) -> str:
    """Build the crate document for the project as it stands.

    Pure given its arguments: reads the tree, runs nothing, and returns
    identical bytes for identical repository state — timestamps come from
    manifests and commits, never from the clock, and entities are built
    in sorted order. The writer is asked about each path at most once.

    Args:
        root: The project root.
        graph: The full task graph — every universe, every output.
        license: The declared license, from :func:`license_of`.
        dsid: The dataset UUID, the namespace absolute entity ids are
            minted under so they are stable across clones.
        writer: Answers "which commit last touched this path" —
            :func:`dataset.last_writer` bound to the root, injected so
            the builder stays free of git.
        keys: Each annexed file's key, repository-relative —
            :func:`dataset.annex_keys`'s answer, injected for the same
            reason as *writer*. SHA-256-backed keys become per-file
            checksums an archive can verify with ``sha256sum``.

    Returns:
        The ``ro-crate-metadata.json`` text, trailing newline included.
    """
    build = _Builder(root, graph, license, dsid, writer, keys)
    return build.document()


def _control_id(key: Key) -> str:
    return f"#control-{key[0]}-{key[1]}"


class _Builder:
    """One render: accumulates entities into a ``ROCrate``, in one order."""

    def __init__(
        self,
        root: Path,
        graph: Graph,
        license: str,
        dsid: str,
        writer: Callable[[Path], LastWrite],
        keys: Mapping[str, str],
    ) -> None:
        from astra.helpers import load_yaml

        self.root = root
        self.graph = graph
        self.license = license
        self.writer = writer
        self.keys = dict(keys)
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
        self.made_keys = {key for key, _ in self.made}
        self.persons: dict[str, str] = {}  # email/name → @id
        self.agents: dict[Key, str] = {}  # each action's Person @id, or ""
        self.images: set[str] = set()  # archive paths already added
        self.engines: set[str] = set()  # engine @ids already added
        self.actions: list[str] = []  # action @ids, for the root's mentions
        #: Loop invariants, asked once: the uuid5 namespace every minted
        #: id lives under, the project's one recorded remote, the spec's
        #: header and last commit, and which decisions it declares.
        self.namespace = uuid.uuid5(uuid.NAMESPACE_URL, dsid)
        remotes = {m.git_remote for _, m in self.made if m.git_remote}
        self.remote = remotes.pop() if len(remotes) == 1 else ""
        loaded = load_yaml(root / SPEC_FILENAME)
        self.spec: dict[str, Any] = dict(loaded) if isinstance(loaded, dict) else {}
        self.title = str(self.spec.get("name") or root.name)
        self.spec_write = writer(root / SPEC_FILENAME)
        self.parameters = {d for t in graph.tasks.values() for d in t.decisions}

    def document(self) -> str:
        """Assemble the graph and serialize it, deterministically."""
        workflow = self._workflow()
        self._steps_and_tools(workflow)
        self._environment_files()
        for key, manifest in self.made:
            self._dataset(key, manifest)
        for key, manifest in self.made:
            self._control(key, self._action(key, manifest))
        self._runs(workflow)
        self._root()
        text: str = json.dumps(
            self.crate.metadata.generate(), indent=1, sort_keys=True, ensure_ascii=False
        )
        return text + "\n"

    # ----- the workflow and its structure -----

    def _workflow(self) -> Any:
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
        workflow = self.crate.add_workflow(
            self.root / SPEC_FILENAME,
            SPEC_FILENAME,
            main=True,
            lang=lang,
            properties={
                # `HowTo` is what licenses the `step` list below — the
                # Provenance profile's requirement, not decoration.
                "@type": ["File", "SoftwareSourceCode", "ComputationalWorkflow", "HowTo"],
                "name": self.title,
                "encodingFormat": "application/yaml",
            },
        )
        if description := self.spec.get("description"):
            workflow["description"] = str(description)
        workflow["conformsTo"] = {
            "@id": "https://bioschemas.org/profiles/ComputationalWorkflow/1.0-RELEASE"
        }
        workflow["license"] = self._license_ref()
        if self.remote:
            workflow["url"] = self.remote
        if self.spec_write:
            # The spec's version is the commit that last changed it — the
            # only version an astra.yaml actually has.
            workflow["version"] = self.spec_write.sha[:7]
            workflow["dateCreated"] = self.spec_write.date
            workflow["creator"] = {
                "@id": self._person(self.spec_write.author, self.spec_write.email)
            }
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
        output_ids = sorted({output_id for _, output_id in self.graph.tasks})
        for position, output_id in enumerate(output_ids):
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
            if self.spec_write:
                tool["softwareVersion"] = self.spec_write.sha[:7]
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
        universes = sorted(
            path.relative_to(self.root).as_posix()
            for path in (self.root / "universes").glob("*.yaml")
        )
        for name in (*_ENVIRONMENT, *universes):
            if (self.root / name).is_file():
                self._file(name)
        if (self.root / "README.md").is_file():
            readme = self._file("README.md")
            readme["about"] = {"@id": "./"}

    def _file(self, name: str) -> Any:
        properties: dict[str, Any] = {}
        if fmt := _format_of(name):
            properties["encodingFormat"] = fmt
        properties.update(self._integrity(name))
        return self.crate.add_file(self.root / name, name, properties=properties)

    def _integrity(self, name: str) -> dict[str, str]:
        """``sha256`` and ``contentSize`` for one repository file.

        An annexed file answers from its key — the working tree may hold
        only a pointer, and hashing that would publish a digest of the
        wrong bytes — and a git-carried file from the bytes themselves.
        Both are repository state, so the render stays pure. A file
        neither annexed nor readable carries no claim at all.
        """
        if key := self.keys.get(name):
            if digest := _SHA256_KEY.match(key):
                return {"contentSize": digest.group(1), "sha256": digest.group(2)}
            if size := _KEY_SIZE.match(key):
                return {"contentSize": size.group(1)}
            return {}
        try:
            data = (self.root / name).read_bytes()
        except OSError:
            return {}
        return {"contentSize": str(len(data)), "sha256": hashlib.sha256(data).hexdigest()}

    def _dataset_id(self, key: Key) -> str:
        """One output directory's crate id — :func:`plan.declared_path`'s
        answer, never a second spelling of the results layout."""
        return plan.declared_path(self.root, self.graph.tasks[key].output_dir) + "/"

    def _dataset(self, key: Key, manifest: assets.Manifest) -> None:
        universe_id, output_id = key
        dataset_id = self._dataset_id(key)
        entity = self.crate.add_dataset(self.graph.tasks[key].output_dir, dataset_id)
        entity["name"] = f"{output_id} (universe {universe_id})"
        entity["description"] = f"output `{output_id}` materialized under `{universe_id}`"
        entity["version"] = manifest.data_version
        manifest_file = self._file(f"{dataset_id}{assets.MANIFEST_FILENAME}")
        manifest_file["about"] = {"@id": dataset_id}
        entity["subjectOf"] = {"@id": manifest_file.id}
        # Every file the directory holds, each with the checksum its
        # annex key already carries — the claim `sha256sum` can check
        # after a `git archive` deposit, where `version` above is lc's
        # own framed directory digest and deliberately is not that.
        parts = [manifest_file]
        parts += [self._file(name) for name in sorted(self.keys) if name.startswith(dataset_id)]
        entity["hasPart"] = [{"@id": part.id} for part in parts]

    # ----- the runs -----

    def _action(self, key: Key, manifest: assets.Manifest) -> str:
        universe_id, output_id = key
        write = self.writer(self.graph.tasks[key].output_dir)
        self.agents[key] = self._person(write.author, write.email) if write else ""
        properties: dict[str, Any] = {
            "@type": "CreateAction",
            "name": f"run of `{output_id}` in universe `{universe_id}`",
            # The *recorded* recipe, not the graph's: the action states
            # what ran, and the spec may have moved since — the manifest
            # is the canonical record of the execution.
            "description": manifest.recipe,
            "instrument": {"@id": self._tool_id(output_id)},
            "result": [{"@id": self._dataset_id(key)}],
            "actionStatus": "http://schema.org/CompletedActionStatus",
            "object": self._objects(key, manifest),
        }
        if manifest.started_at:
            properties["startTime"] = manifest.started_at
        if manifest.finished_at:
            properties["endTime"] = manifest.finished_at
        if self.agents[key]:
            properties["agent"] = {"@id": self.agents[key]}
        if manifest.image is not None:
            properties["containerImage"] = {"@id": self._image(manifest.image)}
        action = ContextEntity(
            self.crate, self._uri(f"action/{universe_id}/{output_id}"), properties
        )
        self.crate.add(action)
        self.actions.append(str(action.id))
        return str(action.id)

    def _objects(self, key: Key, manifest: assets.Manifest) -> list[dict[str, str]]:
        task = self.graph.tasks[key]
        refs = [{"@id": name} for name in _ENVIRONMENT if (self.root / name).is_file()]
        for name in sorted(task.inputs):
            upstream = task.produced_by.get(name)
            if upstream is not None:
                if upstream in self.made_keys:
                    refs.append({"@id": self._dataset_id(upstream)})
                continue
            refs.append({"@id": self._external(name, task.inputs[name])})
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

    def _external(self, name: str, path: Path) -> str:
        """A declared input the spec points at, in or out of the tree.

        In-or-out is :func:`plan.declared_path`'s answer — relative
        inside the tree, absolute outside it — never a second spelling
        of that rule here: two copies of one path rule is how the first
        one shipped a bug.

        An in-tree input's checksum comes from its annex key, like every
        other file. An out-of-tree input carries none: its recorded
        ``input_versions`` digest is lc's *framed* hash, not a raw
        sha256, so publishing it under the workflow-run ``sha256`` term
        would be a checksum nothing can verify — the manifests keep the
        full story, which is the layer's stated weaker promise.
        """
        declared = plan.declared_path(self.root, path)
        in_tree = not Path(declared).is_absolute()
        entity_id = declared if in_tree else Path(declared).as_uri()
        if self.crate.dereference(entity_id) is not None:
            return entity_id
        properties: dict[str, Any] = {"@type": "File", "name": declared}
        if fmt := _format_of(declared):
            properties["encodingFormat"] = fmt
        if in_tree:
            properties.update(self._integrity(declared))
            self.crate.add_file(path, declared, properties=properties)
        else:
            # Outside the repository: recorded by content, not stored
            # in it — the layer's stated weaker promise, so a context
            # entity rather than a data entity the crate cannot hold.
            self.crate.add(ContextEntity(self.crate, entity_id, properties))
        return entity_id

    def _control(self, key: Key, action_id: str) -> None:
        universe_id, output_id = key
        self.crate.add(
            ContextEntity(
                self.crate,
                _control_id(key),
                properties={
                    "@type": "ControlAction",
                    "name": f"orchestration of `{output_id}` in universe `{universe_id}`",
                    "instrument": {"@id": f"#step-{output_id}"},
                    "object": {"@id": action_id},
                },
            )
        )

    def _runs(self, workflow: Any) -> None:
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
                    "result": [{"@id": self._dataset_id(key)} for key, _ in group],
                    "actionStatus": "http://schema.org/CompletedActionStatus",
                },
            )
            if times:
                run_action["startTime"] = times[0]
                run_action["endTime"] = times[-1]
            if agent := self.agents.get(group[0][0], ""):
                run_action["agent"] = {"@id": agent}
            self.crate.add(run_action)
            self.actions.append(str(run_action.id))
            organize = ContextEntity(
                self.crate,
                f"#organize-{sha[:7] or 'unknown'}",
                properties={
                    "@type": "OrganizeAction",
                    "name": f"scheduling of the run at {sha[:7] or 'unknown commit'}",
                    "instrument": {"@id": engine},
                    "object": [{"@id": _control_id(key)} for key, _ in group],
                    "result": {"@id": run_action.id},
                },
            )
            self.crate.add(organize)

    def _engine(self, version: str) -> str:
        """The engine one run's manifests attest, added once per version.

        Its release page is both the id and the ``url`` — the one address
        derivable from repository state alone; anything read off the
        installed engine would differ between collaborators' hosts.
        """
        engine_id = (
            f"https://pypi.org/project/lightcone-cli/{version}/"
            if version
            else "#lightcone-cli"
        )
        if engine_id not in self.engines:
            properties: dict[str, Any] = {
                "@type": "SoftwareApplication",
                "name": "lightcone-cli",
            }
            if version:
                properties["softwareVersion"] = version
                properties["url"] = engine_id
            self.crate.add(ContextEntity(self.crate, engine_id, properties))
            self.engines.add(engine_id)
        return engine_id

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
            self.images.add(archive)
        return archive

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

    def _root(self) -> None:
        root = self.crate.root_dataset
        root["name"] = self.title
        root["description"] = str(
            self.spec.get("description")
            or f"ASTRA analysis `{self.title}`, materialized by lightcone-cli."
        )
        root["license"] = self._license_ref()
        # The newest recorded instant, never the clock: the document must
        # be a pure function of repository state, or every render is a
        # fresh diff and convergence commits forever.
        stamps = sorted(m.finished_at for _, m in self.made if m.finished_at)
        root["datePublished"] = stamps[-1] if stamps else (self.spec_write.date or "1970-01-01")
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
            root["mentions"] = [{"@id": action_id} for action_id in self.actions]

    def _license_ref(self) -> Any:
        """The root's license value, always a linkable entity.

        A path in the tree becomes a File data entity; a URL becomes a
        CreativeWork at that URL; anything else — an SPDX id, an SPDX
        expression, free text — becomes a *local* CreativeWork carrying
        the declared string. Deliberately never a minted spdx.org URL:
        the declaration is not validated against the SPDX list, and a
        fabricated dead URL in a document built for archives is worse
        than a local entity. Idempotent: `add` replaces by id, so the
        workflow and the root may both ask.
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
