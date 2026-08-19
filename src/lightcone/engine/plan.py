"""The spec, read as a graph of tasks.

``astra.yaml`` × ``universes/*.yaml`` gives one task per
``(universe, output)`` pair that has a recipe. A task carries everything
executing it needs and nothing about *how* it will be executed: the
rendered command, where its bytes go, what it reads, which decisions it
was made under, and its ``code_version``.

Nothing here schedules anything. Ordering is Dask's job at execution time
and a topological walk's job in ``--check``; this module only says which
task depends on which.

Sub-analyses are flattened rather than nested. An output declared inside
``analyses.<id>`` is addressed as ``<id>.<output_id>`` and its bytes land
in ``results/<universe>/<id>.<output_id>/`` beside everything else — one
addressing scheme and one place to look, whatever shape the spec has.
References resolve the way ASTRA scopes them: an id declared beside the
output wins, and the root is the fallback.
"""

from __future__ import annotations

import string
from collections.abc import Callable
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Any

from lightcone.engine import assets, identity
from lightcone.engine.project import SPEC_FILENAME, ProjectError

#: A task's identity within a run: which universe, which output.
Key = tuple[str, str]

_FORMATTER = string.Formatter()


@dataclass(frozen=True)
class Task:
    """One output, in one universe: everything needed to make it."""

    universe_id: str
    output_id: str
    output_dir: Path
    #: The recipe with its placeholders substituted — a shell command.
    recipe: str
    #: Declared input name → the path it resolves to. Upstream outputs are
    #: their directories; everything else is whatever ``source`` named.
    inputs: dict[str, Path]
    #: The subset of ``inputs`` another task produces, and which one.
    produced_by: dict[str, Key]
    decisions: dict[str, str]
    code_version: str

    @property
    def key(self) -> Key:
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
        """The tasks in dependency order.

        Only ``--check`` needs this: it has to classify a task after
        everything upstream of it, so an upstream it already decided will
        be rebuilt can be passed down as "this is going to change".
        Execution never calls it — Dask derives the same order from the
        futures it is handed.
        """
        sorter = TopologicalSorter({k: set(t.depends_on) for k, t in self.tasks.items()})
        try:
            return list(sorter.static_order())
        except CycleError as e:
            raise ProjectError(f"the outputs depend on each other in a cycle: {e.args[1]}") from e

    def closure(self, targets: list[Key]) -> Graph:
        """*targets* and everything they transitively depend on.

        Asking for an output asks for what it is made of — anything less
        runs a recipe against inputs that were never brought up to date.
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

        A target is an output id — every universe that has it — or
        ``<universe>/<output_id>`` for exactly one. An unknown target is
        an error rather than an empty run: quietly making nothing is the
        least useful thing a build tool can do.
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


def build(root: Path, *, env_version: str) -> Graph:
    """Read *root*'s spec and universes into a graph of tasks.

    *env_version* is passed in rather than computed here: every task in a
    run has to be identified against the same environment, and a graph
    that recomputed it per task could straddle an edit.
    """
    spec = _spec(root)
    outputs = _outputs(spec)
    sources = _sources(spec)
    universes = _universes(root, spec)
    if not universes:
        raise ProjectError(
            f"{root}/universes/ declares no universe — a run needs at least one "
            "set of decisions to make outputs under."
        )

    tasks: dict[Key, Task] = {}
    for universe_id, decisions in universes.items():
        for output_id, declared in outputs.items():
            if _command(declared):
                key = (universe_id, output_id)
                tasks[key] = _task(root, key, declared, outputs, sources, decisions, env_version)
    return Graph(tasks=tasks)


@dataclass(frozen=True)
class _Declared:
    """One output as the spec declares it, and the scope it was found in."""

    definition: dict[str, Any]
    #: The sub-analysis it came from, or ``None`` for a root output.
    analysis_id: str | None


def _task(
    root: Path,
    key: Key,
    declared: _Declared,
    outputs: dict[str, _Declared],
    sources: dict[str, str],
    decisions: dict[str, str],
    env_version: str,
) -> Task:
    universe_id, output_id = key
    output_dir = assets.output_dir(root, universe_id, output_id)
    scope = declared.analysis_id

    values: dict[str, str] = {}
    paths: dict[str, Path] = {}
    produced_by: dict[str, Key] = {}
    for name in declared.definition.get("inputs") or []:
        if upstream := _lookup(name, scope, outputs, _command):
            produced_by[name] = (universe_id, upstream)
            paths[name] = assets.output_dir(root, universe_id, upstream)
            values[name] = paths[name].relative_to(root).as_posix()
        elif source := _lookup(name, scope, sources, bool):
            values[name] = sources[source]
            candidate = Path(sources[source])
            paths[name] = candidate if candidate.is_absolute() else root / candidate
        else:
            raise ProjectError(
                f"output `{output_id}` declares the input `{name}`, but no output "
                "produces it and no declared input gives it a source."
            )

    # No `usable` here, because a decision's *value* is not a test of
    # whether it was made. An empty string is a choice someone wrote down,
    # and treating it as absent reports "the output does not declare this
    # decision", which is false and leaves nothing to act on.
    mine = {
        name: decisions[found]
        for name in declared.definition.get("decisions") or []
        if (found := _lookup(name, scope, decisions))
    }
    recipe = render(
        _command(declared),
        inputs=values,
        decisions=mine,
        output=output_dir.relative_to(root).as_posix(),
    )
    return Task(
        universe_id=universe_id,
        output_id=output_id,
        output_dir=output_dir,
        recipe=recipe,
        inputs=paths,
        produced_by=produced_by,
        decisions=mine,
        code_version=identity.code_version(recipe=recipe, decisions=mine, env=env_version),
    )


def _lookup(
    name: str,
    scope: str | None,
    among: dict[str, Any],
    usable: Callable[[Any], object] | None = None,
) -> str | None:
    """The key *name* resolves to in *among*, following ASTRA's scoping.

    An id declared inside a sub-analysis is qualified and wins; the root's
    bare id is the fallback. *usable* is read for truth and rejects a
    match that exists but
    cannot serve — a re-exported output with no recipe produces no bytes,
    and an input with no source names nothing. Omitting it means presence
    is the whole test, which is the right answer where the value carries
    no such distinction.
    """
    candidates = (f"{scope}.{name}", name) if scope else (name,)
    return next(
        (c for c in candidates if c in among and (usable is None or usable(among[c]))), None
    )


def _command(declared: _Declared) -> str:
    """A recipe's command template, or empty when the output has none."""
    return str((declared.definition.get("recipe") or {}).get("command") or "")


# =============================================================================
# Reading the spec and the universes
# =============================================================================


def _spec(root: Path) -> dict[str, Any]:
    """*root*'s ``astra.yaml``, with ``path:`` sub-analyses expanded."""
    from astra.helpers import load_yaml, resolve_analysis_tree

    path = root / SPEC_FILENAME
    if not path.is_file():
        raise ProjectError(f"{root}: no {SPEC_FILENAME} — there is no analysis to materialize.")
    data: dict[str, Any] = load_yaml(path)
    return dict(resolve_analysis_tree(data, root))


def _analyses(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The sub-analyses, refusing a depth this layer cannot address.

    One level flattens onto ``<id>.<output_id>``. A second would need a
    naming scheme and a decision-merge rule that nothing here has, and
    quietly ignoring those outputs would be worse than saying so.
    """
    found = {}
    for analysis_id, node in (spec.get("analyses") or {}).items():
        if node.get("analyses"):
            raise ProjectError(
                f"sub-analysis `{analysis_id}` declares sub-analyses of its own. "
                "One level of nesting is supported; flatten the deeper ones."
            )
        found[str(analysis_id)] = node
    return found


def _outputs(spec: dict[str, Any]) -> dict[str, _Declared]:
    """Every output in the tree, flattened onto one namespace."""
    found = {
        str(o["id"]): _Declared(o, analysis_id=None)
        for o in spec.get("outputs") or []
        if o.get("id")
    }
    for analysis_id, node in _analyses(spec).items():
        for output in node.get("outputs") or []:
            if output.get("id"):
                found[f"{analysis_id}.{output['id']}"] = _Declared(output, analysis_id)
    return found


def _sources(spec: dict[str, Any]) -> dict[str, str]:
    """Every declared input's source, flattened onto the same namespace.

    A sub-analysis input written ``from: ../<id>`` is an alias: it carries
    no source of its own and inherits the one it points at. Only upward
    references are resolved, which is the only direction ASTRA's ``from:``
    goes for inputs.
    """
    root_sources = {
        str(i["id"]): str(i.get("source") or "") for i in spec.get("inputs") or [] if i.get("id")
    }
    found = dict(root_sources)
    for analysis_id, node in _analyses(spec).items():
        for declared in node.get("inputs") or []:
            if not declared.get("id"):
                continue
            source = declared.get("source") or ""
            if alias := declared.get("from"):
                source = root_sources.get(str(alias).lstrip("./"), "")
            found[f"{analysis_id}.{declared['id']}"] = str(source)
    return found


def _universes(root: Path, spec: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Each universe's decisions, by universe id, flattened like the rest.

    A sub-analysis keeps its own ``universes/`` directory, and the root
    universe says which of them this one selects; absent that, the same
    id. Its decisions land under ``<analysis_id>.<decision_id>``, so the
    flat namespace holds for decisions exactly as it does for outputs.

    Discovered by glob, so a project with no ``universes/`` directory is
    empty rather than an error — git carries no empty directories, and a
    fresh clone is a normal state to be in.
    """
    from astra.helpers import load_yaml

    found: dict[str, dict[str, str]] = {}
    for path in sorted((root / "universes").glob("*.yaml")):
        data = load_yaml(path)
        universe_id = str(data.get("id") or path.stem)
        selected = data.get("analyses") or {}
        decisions = _flatten(data.get("decisions"))
        for analysis_id, node in _analyses(spec).items():
            sub = root / str(node.get("path") or analysis_id) / "universes"
            chosen = str((selected.get(analysis_id) or {}).get("universe") or universe_id)
            if (sub_path := sub / f"{chosen}.yaml").is_file():
                for name, value in _flatten(load_yaml(sub_path).get("decisions")).items():
                    decisions[f"{analysis_id}.{name}"] = value
        found[universe_id] = decisions
    return found


def _flatten(decisions: Any) -> dict[str, str]:
    """A universe's decisions as strings, with unset ones left out.

    A YAML null is *not* a choice, and `str(None)` would render the literal
    ``None`` into a shell command and into ``code_version``. Dropping it
    means a recipe that references the decision fails by name instead.
    """
    return {str(k): str(v) for k, v in (decisions or {}).items() if v is not None}


# =============================================================================
# Rendering a recipe
# =============================================================================


def render(template: str, *, inputs: dict[str, str], decisions: dict[str, str], output: str) -> str:
    """Substitute ASTRA's recipe placeholders.

    ``{output}`` is the directory the recipe writes into, ``{inputs}`` is
    every input's value in declaration order, and ``{inputs.<id>}`` /
    ``{decisions.<id>}`` are one of each. ``{{`` and ``}}`` collapse to
    literal braces.

    Strict on everything else, deliberately: an unknown placeholder, an
    undeclared reference, or a format spec is a spec bug, and a recipe
    that ran with a silently empty substitution would produce bytes the
    manifest then swears are correct.
    """
    pieces: list[str] = []
    for literal, field, spec, conversion in _FORMATTER.parse(template):
        pieces.append(literal)
        if field is None:
            continue
        if spec or conversion:
            raise ProjectError(f"recipe placeholder `{{{field}}}` takes no format spec.")
        if field == "output":
            pieces.append(output)
        elif field == "inputs":
            pieces.append(" ".join(inputs.values()))
        else:
            pieces.append(_named(field, inputs=inputs, decisions=decisions))
    return "".join(pieces)


def _named(field: str, *, inputs: dict[str, str], decisions: dict[str, str]) -> str:
    namespace, dot, name = field.partition(".")
    available = {"inputs": inputs, "decisions": decisions}
    if not dot or namespace not in available:
        raise ProjectError(
            f"unknown recipe placeholder `{{{field}}}` — use {{output}}, {{inputs}}, "
            "{inputs.<id>}, or {decisions.<id>}."
        )
    if name not in available[namespace]:
        raise ProjectError(
            f"recipe placeholder `{{{field}}}` names a {namespace[:-1]} "
            "the output does not declare."
        )
    return available[namespace][name]
