"""The spec, read as a graph of tasks.

``astra.yaml`` × ``universes/*.yaml`` gives one task per
``(universe, output)`` pair that has a recipe. A task carries everything
executing it needs and nothing about *how* it will be executed: the
rendered command, where its bytes go, what it reads, which decisions it
was made under, and its ``definition_version``.

What the spec *means* is ASTRA's to say. ``astra.resolve`` settles each
universe's decisions, resolves every output's inputs to what supplies
them, drops the outputs whose ``when:`` does not hold, and renders the
recipe grammar — so scoping, ``from:`` references and sub-analysis
nesting are read here rather than re-derived.

Where an output lands follows the spec's own shape: every analysis node
has an *analysis root* — the directory holding its ``astra.yaml`` — and its
materialize to ``<analysis root>/results/<universe>/<inline scope…>/<id>.<format>``.
An external sub-analysis (``path:``) is a self-similar analysis with its
own analysis root and, where it names one, its own universe; an inline one
shares its parent's and disambiguates with a scope directory. Graph keys stay
the qualified id, so only the path nests, never the addressing.

Nothing here schedules anything. Ordering is Dask's job at execution time
and a topological walk's job in ``--check``; this module only says which
task depends on which.
"""

from __future__ import annotations

from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from pathlib import Path

from lightcone.engine import assets, identity
from lightcone.engine.project import SPEC_FILENAME, ProjectError

#: A task's identity within a run: which universe, which output.
Key = tuple[str, str]


@dataclass(frozen=True)
class Task:
    """One output, in one universe: everything needed to make it."""

    universe_id: str
    output_id: str
    #: The single file this output is.
    output_path: Path
    #: The recipe with its placeholders substituted — a shell command.
    recipe: str
    #: Declared input name → the path it resolves to. Upstream outputs are
    #: their files; everything else is whatever ``source`` named.
    inputs: dict[str, Path]
    #: The subset of ``inputs`` another task produces, and which one.
    produced_by: dict[str, Key]
    decisions: dict[str, str]
    definition_version: str

    @property
    def manifest_path(self) -> Path:
        """This output's manifest sidecar."""
        return assets.manifest_path(self.output_path)

    @property
    def key(self) -> Key:
        """This task's identity within a run."""
        return (self.universe_id, self.output_id)

    @property
    def depends_on(self) -> tuple[Key, ...]:
        """The tasks that must be made first, deduplicated, in order."""
        return tuple(dict.fromkeys(self.produced_by.values()))


@dataclass(frozen=True)
class Graph:
    """Every task a run could make, and how they relate."""

    tasks: dict[Key, Task]

    def order(self) -> list[Key]:
        """Return the tasks in dependency order.

        Only ``--check`` needs this, to classify a task after everything
        upstream of it. Execution never calls it: Dask derives the same
        order from the futures it is handed.

        Returns:
            Every task key, dependencies first.

        Raises:
            ProjectError: If the outputs depend on each other in a cycle.
        """
        sorter = TopologicalSorter({k: set(t.depends_on) for k, t in self.tasks.items()})
        try:
            return list(sorter.static_order())
        except CycleError as e:
            raise ProjectError(f"the outputs depend on each other in a cycle: {e.args[1]}") from e

    def closure(self, targets: list[Key]) -> Graph:
        """Narrow the graph to *targets* and what they depend on.

        Asking for an output asks for what it is made of; anything less
        runs a recipe against inputs that were never brought up to date.

        Args:
            targets: The task keys asked for.

        Returns:
            A graph holding *targets* plus their transitive dependencies.
        """
        wanted: set[Key] = set()
        pending = list(targets)
        while pending:
            key = pending.pop()
            if key in wanted:
                continue
            wanted.add(key)
            pending.extend(self.tasks[key].depends_on)
        return Graph(tasks={k: t for k, t in self.tasks.items() if k in wanted})

    def resolve(self, targets: list[str]) -> list[Key]:
        """Turn what a user typed into task keys.

        Args:
            targets: Each an output id — matching every universe that has
                it — or ``<universe>/<output_id>`` for exactly one.

        Returns:
            The matching task keys, in the order given.

        Raises:
            ProjectError: If a target matches nothing. Quietly making
                nothing is the least useful thing a build tool can do.
        """
        keys: list[Key] = []
        for target in targets:
            universe, _, output = target.rpartition("/")
            matched = [
                key for key in self.tasks if key[1] == output and universe in ("", key[0])
            ]
            if not matched:
                known = ", ".join(sorted(f"{u}/{o}" for u, o in self.tasks)) or "none"
                raise ProjectError(f"no output matches `{target}`. Available: {known}")
            keys.extend(matched)
        return keys


# =============================================================================
# Building the graph
# =============================================================================


def build(root: Path) -> Graph:
    """Read a project's spec and universes into a graph of tasks.

    Args:
        root: The project root.

    Returns:
        One task per ``(universe, output)`` pair that has a recipe and is
        active in that universe.

    Raises:
        ProjectError: If the spec is missing, declares no universe, gives
            two universes the same id, or names an input nothing provides.
    """
    from astra.helpers import load_yaml, resolve_analysis_tree

    spec_path = root / SPEC_FILENAME
    if not spec_path.is_file():
        raise ProjectError(
            f"{root}: no {SPEC_FILENAME} — there is no analysis to materialize."
        )
    universes = sorted((root / "universes").glob("*.yaml"))
    if not universes:
        raise ProjectError(
            f"{root}/universes/ declares no universe — a run needs at least one "
            "set of decisions to make outputs under."
        )
    _validate(spec_path, universes)
    spec = dict(resolve_analysis_tree(load_yaml(spec_path), root))

    tasks: dict[Key, Task] = {}
    declared_in: dict[str, Path] = {}
    for path in universes:
        universe = load_yaml(path)
        universe_id = str(universe.get("id") or path.stem)
        # A universe id names a directory under results/, so two files
        # claiming one would write to the same place — and since the second
        # simply replaces the first here, the outputs of one of them would
        # go missing with nothing said.
        if (first := declared_in.get(universe_id)) is not None:
            raise ProjectError(
                f"{first.name} and {path.name} both declare the universe "
                f"`{universe_id}`, so both would materialize into "
                f"results/{universe_id}/. Give each universe its own id."
            )
        declared_in[universe_id] = path
        for task in _tasks(root, universe_id, spec, universe):
            tasks[task.key] = task

    return Graph(tasks=tasks)


def declared_path(root: Path, path: Path) -> str:
    """Name *path* the way the analysis declared it.

    Project-relative inside the tree, absolute outside it — the two forms a
    recipe, a manifest and a ``[DATALAD RUNCMD]`` record all want, and the
    reason a declared input with an absolute ``source:`` has somewhere to
    be written down rather than being a crash.

    Deliberately **not** resolved. Every declared input under ``data/`` is
    an annex symlink, so resolving would name
    ``.git/annex/objects/SHA256E-…`` — the storage rather than the input,
    and a path no one can fetch.

    Args:
        root: The project root.
        path: What to name.

    Returns:
        A POSIX path, relative to *root* where it is under it.
    """
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _validate(spec_path: Path, universes: list[Path]) -> None:
    """Refuse a spec ASTRA rejects, before anything is resolved.

    Resolution answers what a valid spec *means*; it does not re-check
    that it is one. So an invalid spec reaches it as a missing decision or
    an unresolvable input — blaming the run for a fault in the file, and
    at a point far from the line that caused it. Asking ASTRA first costs
    one pass over a spec file and moves the error to where it can be
    fixed.

    Args:
        spec_path: The project's ``astra.yaml``.
        universes: Every universe file that will be resolved against it.

    Raises:
        ProjectError: Listing every structural and semantic error found,
            in ASTRA's own words.
    """
    from astra.validation import (
        validate_analysis_file,
        validate_analysis_schema,
        validate_universe_file,
    )

    problems = [
        *validate_analysis_schema(spec_path),
        *(str(e) for e in validate_analysis_file(spec_path)),
    ]
    for path in universes:
        problems += [f"{path.name}: {e}" for e in validate_universe_file(path, spec_path)]
    if problems:
        listed = "\n".join(f"  {problem}" for problem in problems)
        raise ProjectError(
            f"{spec_path} does not validate, so there is nothing to materialize "
            f"from it:\n{listed}"
        )


def _tasks(
    root: Path,
    universe_id: str,
    spec: dict[str, object],
    universe: dict[str, object],
) -> list[Task]:
    """Every task one universe contributes.

    ``resolve_outputs`` has already dropped what this universe does not
    produce, so the only filter left is whether an output carries a
    command: a re-export names bytes another output makes, and making it
    twice under two ids is not a thing to do.
    """
    from astra.resolve import render_command, resolve_outputs

    resolved = resolve_outputs(spec, universe, root)
    # The whole resolved output, not just its id: an upstream input has to
    # resolve to the *file* another task writes, which needs that output's
    # format. A set of ids cannot say it.
    executable = {out.id: out for out in resolved if out.command}

    if absent := sorted(out.id for out in executable.values() if not out.format):
        raise ProjectError(
            f"{len(absent)} output(s) declare no `format:`: {', '.join(absent)}. "
            "lc names each output's file from it, so there is nowhere to write them — "
            "add the artifact's file extension, e.g. `format: png`."
        )

    def file_of(out: object) -> Path:
        return assets.output_path(
            root,
            universe_id,
            str(out.id),  # type: ignore[attr-defined]
            str(out.format),  # type: ignore[attr-defined]
        )

    tasks = []
    for out in resolved:
        if not out.command:
            continue
        output_path = file_of(out)
        values: dict[str, str] = {}
        paths: dict[str, Path] = {}
        produced_by: dict[str, Key] = {}
        for declared in out.inputs:
            if declared.produced_by in executable:
                produced_by[declared.id] = (universe_id, declared.produced_by)
                paths[declared.id] = file_of(executable[declared.produced_by])
            elif declared.source:
                # An absolute `source:` wins over the join — pathlib's own
                # rule, and the one anyone writing one expects.
                paths[declared.id] = root / declared.source
            else:
                raise ProjectError(
                    f"output `{out.id}` declares the input `{declared.id}`, but no "
                    "output produces it and no declared input gives it a source."
                )
            values[declared.id] = declared_path(root, paths[declared.id])

        try:
            recipe = render_command(
                out.command,
                inputs=values,
                decisions=out.decisions,
                output=declared_path(root, output_path),
            )
        except ValueError as e:
            raise ProjectError(f"output `{out.id}`: {e}") from e

        tasks.append(
            Task(
                universe_id=universe_id,
                output_id=out.id,
                output_path=output_path,
                recipe=recipe,
                inputs=paths,
                produced_by=produced_by,
                decisions=out.decisions,
                definition_version=identity.definition_version(
                    recipe=recipe, decisions=out.decisions, fmt=str(out.format)
                ),
            )
        )
    return tasks
