"""The publication view: the repository described as a Workflow Run RO-Crate.

The project *is* the crate. ``ro-crate-metadata.json`` sits at the root
and describes what the repository already holds — the spec, the lock,
each materialized result and the run that made it — so a deposit is
``git archive`` on the repository, not an export step that copies bytes.
ASTRA's records stay the canonical account; the crate is the same facts
in schema.org vocabulary, readable by archives and viewers that will
never run ``lc``.

The document is a pure function of repository state: :func:`render`
takes the graph, the license, the remote, and a way to ask git who last wrote each
output, and returns bytes that are identical when nothing changed. That
is what lets ``lc materialize`` converge the file the way it converges
``uv.lock`` — compare, and commit only a difference — with no verb for a
human to remember.

Profiles targeted: Process, Workflow and Provenance Run Crate 0.5, plus
Workflow RO-Crate 1.0. The Provenance layer is structural, not claimed:
one ``lc materialize`` invocation is one ``OrganizeAction`` (results of a
run share their records' ``commit``, because the driver reads HEAD
once and hands it down), each result's execution is a ``CreateAction``
steered by a ``ControlAction``, and the spec's outputs are the
workflow's ``HowToStep`` s.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rocrate.model import ContextEntity
from rocrate.model.computerlanguage import ComputerLanguage
from rocrate.rocrate import ROCrate

from lightcone.engine import assets
from lightcone.engine.dataset import LastWrite
from lightcone.engine.plan import Graph, Key
from lightcone.engine.project import SPEC_FILENAME

if TYPE_CHECKING:
    from astra.records import Record

#: The vocabulary the run-level facts come from. Without it in the
#: ``@context``, terms like ``containerImage`` and ``sha256`` are
#: undefined and silently dropped on JSON-LD expansion — typed but inert.
_WORKFLOW_RUN_CONTEXT = "https://w3id.org/ro/terms/workflow-run"

#: The RO-Crate version written, pinned rather than left to rocrate's
#: default: the Workflow RO-Crate and Run Crate profiles are written
#: against 1.1's vocabulary, and the 1.3 context maps
#: ``ComputationalWorkflow`` to a term their shapes do not recognise, so
#: following the default turns the workflow into an untyped file.
_RO_CRATE_VERSION = "1.1"

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


def render(
    root: Path,
    graph: Graph,
    *,
    license: str,
    dsid: str,
    remote: str,
    writer: Callable[..., LastWrite],
    keys: Mapping[str, str],
) -> str:
    """Build the crate document for the project as it stands.

    Pure given its arguments: reads the tree, runs nothing, and returns
    identical bytes for identical repository state — timestamps come from
    records and commits, never from the clock, and entities are built
    in sorted order. The writer is asked about each path at most once.

    Args:
        root: The project root.
        graph: The full task graph — every planned result.
        license: The declared license, from :func:`project.license_of`.
        dsid: The dataset UUID, the namespace absolute entity ids are
            minted under so they are stable across clones.
        remote: The repository's ``origin`` URL, or empty when it has
            none — where the workflow and its recipes can be found.
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
    build = _Builder(root, graph, license, dsid, remote, writer, keys)
    return build.document()


def _split(key: Key) -> tuple[str, str]:
    """A task's result name — after its analysis's folder, for a result
    that belongs to another analysis — and its universe, empty for a
    result built once."""
    name, _, universe = key.partition("#")
    return name, universe


def _slug(key: Key) -> str:
    """A task's key as a fragment identifier, which cannot hold its ``#``."""
    name, universe = _split(key)
    return f"{universe}-{name}" if universe else name


def _offset(stamp: str) -> str:
    """A record's UTC instant with a numeric offset: the Run Crate shapes
    read ``startTime`` and ``endTime`` with ``+hh:mm``, never ``Z``."""
    return stamp.removesuffix("Z") + "+00:00"


def _titled(key: Key) -> str:
    name, universe = _split(key)
    return f"`{name}` in universe `{universe}`" if universe else f"`{name}`"


class _Builder:
    """One render: accumulates entities into a ``ROCrate``, in one order."""

    def __init__(
        self,
        root: Path,
        graph: Graph,
        license: str,
        dsid: str,
        remote: str,
        writer: Callable[..., LastWrite],
        keys: Mapping[str, str],
    ) -> None:
        from astra.contract import Contract
        from astra.diagnostics import Catalogue
        from astra.load import ParseError, load_yaml
        from astra.records import Record, read_record
        from astra.sources.reader import WorktreeReader

        self.root = root
        self.graph = graph
        self.license = license
        self.writer = writer
        self.keys = dict(keys)
        self.crate = ROCrate(version=_RO_CRATE_VERSION)
        self.crate.metadata.extra_contexts.append(_WORKFLOW_RUN_CONTEXT)
        #: Every materialized task, sorted, with its record at the first
        #: of its places — the one its recipe wrote; the others hold copies.
        reader, contract = WorktreeReader(root, None), Contract.load(Catalogue.load())
        self.made: list[tuple[Key, Record]] = []
        for key in sorted(graph.tasks):
            record = read_record(reader, graph.tasks[key].places[0].record, contract)
            if isinstance(record, Record):
                self.made.append((key, record))
        self.made_keys = {key for key, _ in self.made}
        self.persons: dict[str, str] = {}  # email/name → @id
        self.agents: dict[Key, str] = {}  # each action's Person @id, or ""
        self.engines: set[str] = set()  # engine @ids already added
        self.actions: list[str] = []  # action @ids, for the root's mentions
        #: Loop invariants, asked once: the uuid5 namespace every minted
        #: id lives under, the spec's header and last commit, and which
        #: decisions it declares.
        self.namespace = uuid.uuid5(uuid.NAMESPACE_URL, dsid)
        self.remote = remote
        try:
            self.spec: dict[str, Any] = load_yaml((root / SPEC_FILENAME).read_bytes()).value
        except ParseError:
            self.spec = {}
        self.title = str(self.spec.get("label") or root.name)
        self.spec_write = writer(root / SPEC_FILENAME)
        self.parameters = {d for t in graph.tasks.values() for d in t.decisions}

    def document(self) -> str:
        """Assemble the graph and serialize it, deterministically."""
        workflow = self._workflow()
        self._steps_and_tools(workflow)
        self._environment_files()
        for key, record in self.made:
            self._dataset(key, record)
        for key, record in self.made:
            self._control(key, self._action(key, record))
        self._runs(workflow)
        self._root()
        document = self.crate.metadata.generate()
        # Compacted JSON-LD: a property with one value is that value, not
        # a one-element array. Applied here rather than at each builder
        # because the arrays come from three places — rocrate's
        # `append_to`, and two literal lists — and which of them hold one
        # element depends on the project (one output, one author). The
        # same rule rocrate itself applies to `@context`.
        for entity in document["@graph"]:
            for name, value in entity.items():
                if isinstance(value, list) and len(value) == 1:
                    entity[name] = value[0]
        text: str = json.dumps(document, indent=1, sort_keys=True, ensure_ascii=False)
        return text + "\n"

    # ----- the workflow and its structure -----

    def _workflow(self) -> Any:
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
        output_ids = sorted({_split(key)[0] for key in self.graph.tasks})
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
        """The lock and its companions — what every run consumed — and
        the spec of each analysis below the project's whose recipes run."""
        specs = sorted(
            {
                posixpath.join(task.workdir, SPEC_FILENAME)
                for task in self.graph.tasks.values()
                if task.workdir
            }
        )
        for name in (*_ENVIRONMENT, *specs):
            if (self.root / name).is_file():
                self._file(name)
        if (self.root / "README.md").is_file():
            readme = self._file("README.md")
            readme["about"] = {"@id": "./"}

    def _file(self, name: str) -> Any:
        # Idempotent by id, so the second asker (the license file is
        # asked for by the workflow and the root) does not hash again.
        if (existing := self.crate.dereference(name)) is not None:
            return existing
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

        The byte path re-checks the pointer shape rather than trusting
        the key map's absence: ``annex_keys`` answers empty for a whole
        repository whenever git-annex cannot answer at all, and a
        pointer file reads perfectly well — so without the guard, one
        failed ``git annex find`` would publish a well-formed digest of
        the pointer text for every annexed file, silently.
        """
        if key := self.keys.get(name):
            if digest := _SHA256_KEY.match(key):
                return {"contentSize": digest.group(1), "sha256": digest.group(2)}
            if size := _KEY_SIZE.match(key):
                return {"contentSize": size.group(1)}
            return {}
        path = self.root / name
        try:
            if path.is_symlink() or assets.is_pointer(path):
                return {}
            data = path.read_bytes()
        except OSError:
            return {}
        return {"contentSize": str(len(data)), "sha256": hashlib.sha256(data).hexdigest()}

    def _dataset_id(self, key: Key) -> str:
        """One result's crate id — the place ASTRA gives its bytes, never
        a second spelling of the results layout. No trailing slash: a
        result is one file, so the entity is a ``File``."""
        return self.graph.tasks[key].places[0].bytes

    def _dataset(self, key: Key, record: Record) -> None:
        place = self.graph.tasks[key].places[0]
        # A `File`, not a `Dataset` with parts: a result is one file, so
        # there is one annex key to look up by name and one checksum to
        # publish — the same digest its record gives.
        entity = self._file(self._dataset_id(key))
        entity["name"] = record.address
        entity["description"] = f"output {_titled(key)}, materialized"
        entity["version"] = record.content_hash
        described = self._file(place.record)
        described["about"] = {"@id": entity.id}
        entity["subjectOf"] = {"@id": described.id}

    # ----- the runs -----

    def _action(self, key: Key, record: Record) -> str:
        place = self.graph.tasks[key].places[0]
        write = self.writer(place.bytes, place.record)
        self.agents[key] = self._person(write.author, write.email) if write else ""
        properties: dict[str, Any] = {
            "@type": "CreateAction",
            "name": f"run of {_titled(key)}",
            # The command as the spec now stands, like the decisions below:
            # a record holds neither, and one made from another command is
            # stale, so this is what a current result ran.
            "description": self.graph.tasks[key].command,
            "instrument": {"@id": self._tool_id(_split(key)[0])},
            "result": [{"@id": self._dataset_id(key)}],
            "actionStatus": "http://schema.org/CompletedActionStatus",
            "object": self._objects(key),
        }
        if record.started:
            properties["startTime"] = _offset(record.started)
        if record.finished:
            properties["endTime"] = _offset(record.finished)
        if self.agents[key]:
            properties["agent"] = {"@id": self.agents[key]}
        action = ContextEntity(self.crate, self._uri(f"action/{key}"), properties)
        self.crate.add(action)
        self.actions.append(str(action.id))
        return str(action.id)

    def _objects(self, key: Key) -> list[dict[str, str]]:
        task = self.graph.tasks[key]
        refs = [{"@id": name} for name in _ENVIRONMENT if (self.root / name).is_file()]
        for name in sorted(task.inputs):
            upstream = task.produced_by.get(name)
            if upstream is not None:
                if upstream in self.made_keys:
                    refs.append({"@id": self._dataset_id(upstream)})
                continue
            refs.append({"@id": self._input(task.inputs[name])})
        # The options the task's decisions select as the spec now stands:
        # a record holds none, and one that names another selection is
        # stale, so these are what a current result ran under.
        for decision in sorted(task.decisions):
            properties: dict[str, Any] = {
                "@type": "PropertyValue",
                "name": decision,
                "value": task.decisions[decision],
                "exampleOfWork": {"@id": f"#param-{decision}"},
            }
            value = ContextEntity(self.crate, f"#value-{_slug(key)}-{decision}", properties)
            self.crate.add(value)
            refs.append({"@id": value.id})
        return refs

    def _input(self, path: str) -> str:
        """A declared input file, by its path in the repository.

        Its checksum comes from its annex key, like every other file: a
        ``File`` entity's ``sha256`` describes the *deposit* — the bytes a
        ``git archive`` carries — never what any particular run consumed,
        so records disagreeing about a shared input (a half-rebuilt
        project) do not suppress it; which bytes a run consumed is its
        own record's ``inputs``.
        """
        if self.crate.dereference(path) is None:
            properties: dict[str, Any] = {"@type": "File", "name": path}
            if fmt := _format_of(path):
                properties["encodingFormat"] = fmt
            properties.update(self._integrity(path))
            self.crate.add_file(self.root / path, path, properties=properties)
        return path

    def _control(self, key: Key, action_id: str) -> None:
        self.crate.add(
            ContextEntity(
                self.crate,
                f"#control-{_slug(key)}",
                properties={
                    "@type": "ControlAction",
                    "name": f"orchestration of {_titled(key)}",
                    "instrument": {"@id": f"#step-{_split(key)[0]}"},
                    "object": {"@id": action_id},
                },
            )
        )

    def _runs(self, workflow: Any) -> None:
        """One ``OrganizeAction`` per run — results whose records name one
        commit were made by one ``lc materialize``, the driver's one HEAD
        read."""
        runs: dict[str, list[tuple[Key, Record]]] = {}
        for key, record in self.made:
            runs.setdefault(record.commit, []).append((key, record))
        for sha in sorted(runs):
            group = runs[sha]
            # The engine *that run* recorded, never the one installed
            # here: the render must be a function of repository state,
            # or two collaborators on different lc versions re-commit
            # the crate at each other forever — and an lc upgrade is
            # recorded as moving nothing. One run has one engine by
            # construction; every record of the group agrees.
            engine = self._engine(group[0][1].runner.version)
            times = sorted(t for _, r in group for t in (r.started, r.finished) if t)
            run_action = ContextEntity(
                self.crate,
                self._uri(f"run/{sha}"),
                properties={
                    "@type": "CreateAction",
                    "name": f"lc materialize at {sha[:7]}",
                    "description": f"one materialization run, starting from commit {sha}",
                    "instrument": {"@id": workflow.id},
                    "result": [{"@id": self._dataset_id(key)} for key, _ in group],
                    "actionStatus": "http://schema.org/CompletedActionStatus",
                },
            )
            if times:
                run_action["startTime"] = _offset(times[0])
                run_action["endTime"] = _offset(times[-1])
            if agent := self.agents.get(group[0][0], ""):
                run_action["agent"] = {"@id": agent}
            self.crate.add(run_action)
            self.actions.append(str(run_action.id))
            organize = ContextEntity(
                self.crate,
                f"#organize-{sha[:7]}",
                properties={
                    "@type": "OrganizeAction",
                    "name": f"scheduling of the run at {sha[:7]}",
                    "instrument": {"@id": engine},
                    "object": [{"@id": f"#control-{_slug(key)}"} for key, _ in group],
                    "result": {"@id": run_action.id},
                },
            )
            self.crate.add(organize)

    def _engine(self, version: str) -> str:
        """The engine one run's records name, added once per version.

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
        stamps = sorted(r.finished for _, r in self.made if r.finished)
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
