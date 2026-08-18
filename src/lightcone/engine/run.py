"""What ``lc run`` is: a probe of the recipe environment (spec §4).

Byte-for-byte the environment recipes will get — the same lock, the same
converged ``.venv`` — under the same sandbox. That equivalence is the
verb's whole purpose: if a probe works, the recipe will, and if a probe
is denied, the recipe would have been.

A probe has no output, which is what makes it the strictest consumer of
the boundary: it may read the project and the declared inputs, and write
only its own tmp scope. Nothing it does can land in the tree.

The command runs *inside* ``uv run``, and the sandbox wraps the command
rather than uv: uv, its config, and its caches are trusted plumbing
outside the boundary (spec §7).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from lightcone.engine import sandbox
from lightcone.engine.project import (
    SPEC_FILENAME,
    ProjectError,
    child_env,
    declares_system_layer,
    require_uv,
)

#: Opened by a bare ``lc run``. In the exec allowlist by construction.
DEFAULT_SHELL = "bash"


def probe(
    project: Path,
    command: Sequence[str],
    *,
    sandboxed: bool = True,
    require: bool = False,
    on_plan: Callable[[list[str]], None] | None = None,
) -> sandbox.Outcome:
    """Run *command* in the project environment, inside the boundary.

    An empty *command* opens a shell, which is announced rather than
    silent — a shell that looks like your own but cannot write the
    project is worse than no shell if you do not know it is there.

    *on_plan* receives the ``--sandbox-debug`` dump just before the
    command is spawned, so the policy is readable while it still matters
    — including for the shell, where printing it afterwards would be
    printing it to nobody.
    """
    require_uv()
    _refuse_containerized(project)
    spec = read_spec(project)
    _guard_output_name(command, spec)

    interactive = not command
    argv = list(command) or [DEFAULT_SHELL]

    if require and not sandboxed:
        raise ProjectError(
            "--require-sandbox and --no-sandbox contradict each other: one "
            "insists on enforcement, the other turns it off."
        )
    backend: sandbox.Backend = sandbox.detect() if sandboxed else sandbox.disabled()
    _enforce_requirement(backend, require)

    with sandbox.scope(project, read_paths=input_paths(project, spec)) as policy:
        return sandbox.run(
            backend,
            policy,
            argv,
            cwd=project,
            prefix=uv_prefix(project),
            # Same reason as convergence: this uv invocation names its
            # project explicitly, so an environment activated elsewhere
            # is never what we mean — and uv says so, once per run, in
            # the middle of the probe's own output.
            env=child_env(),
            explain=not interactive,
            announce=(
                None
                if on_plan is None
                else lambda wrapped: on_plan(
                    describe(project, policy, backend.attest(policy), wrapped)
                )
            ),
        )


def uv_prefix(project: Path) -> list[str]:
    """``uv run``, pinned to *project* and refusing to drift.

    ``--locked`` makes a stale lock uv's loud error rather than a silent
    relock, and ``--exact`` keeps a previously-installed extra from
    surviving into the environment being probed. Always an explicit
    ``--project``: uv's own walk-up discovery is never trusted (spec §4).
    """
    return ["uv", "run", "--locked", "--exact", "--project", str(project), "--"]


def _refuse_containerized(project: Path) -> None:
    """Containerized mode is derived, not configured — and not built yet.

    A project that declares a system layer must not quietly get a
    direct-mode run: the two execute in different worlds and would attest
    different things.
    """
    if declares_system_layer(project):
        raise ProjectError(
            "this project declares a system layer, which puts it in "
            "containerized mode — not available in this release. Remove "
            "`[tool.lightcone.image]` (and `Containerfile.extra`) to run "
            "in direct mode."
        )


def read_spec(project: Path) -> dict[str, Any]:
    """The project's ``astra.yaml``, with sub-analyses merged in if possible.

    Best-effort by design: a probe exists to debug a project, and a spec
    whose sub-analysis references are stale is exactly when someone runs
    one. A tree that will not resolve degrades to the top-level document
    rather than making the verb unusable.
    """
    from astra.helpers import load_yaml, resolve_analysis_tree

    data: dict[str, Any] = load_yaml(project / SPEC_FILENAME)
    try:
        return dict(resolve_analysis_tree(data, project))
    except Exception:
        return data


def input_paths(project: Path, spec: dict[str, Any]) -> list[Path]:
    """The declared inputs that are filesystem paths, resolved.

    A probe's read allowlist is the union of *all* declared inputs
    (spec §4) — it has no output, so it has no narrower set to use.
    ASTRA's ``source`` is free-form (a URI, a dotted name, a path), so
    the test for "is this a path" is whether it resolves to something
    that exists. Anything else is somebody else's input kind.
    """
    from astra.helpers import get_inputs

    found: list[Path] = []
    for declared in get_inputs(spec):
        source = declared.get("source")
        if not isinstance(source, str) or not source:
            continue
        candidate = Path(source)
        resolved = candidate if candidate.is_absolute() else project / candidate
        if resolved.exists():
            found.append(resolved.resolve())
    return found


def _guard_output_name(command: Sequence[str], spec: dict[str, Any]) -> None:
    """Refuse ``lc run <output_id>`` before anything execs (spec §4).

    ``lc run`` used to mean "materialize these outputs". It now means
    "run this command", so trained fingers typing `lc run best_fit` would
    otherwise have the output name exec'd as a program.

    Spec §4 wants this to redirect to ``lc materialize <id>``. That verb
    does not exist yet, and sending someone to a command that will fail
    is worse than telling them the truth — so the redirect arrives with
    the fabric layer, and until then the message says what it can.
    """
    from astra.helpers import get_output_ids

    if not command or command[0] not in get_output_ids(spec):
        return
    raise ProjectError(
        f"`{command[0]}` is a declared output, and `lc run` runs commands, "
        "not outputs. Materializing outputs arrives with the fabric layer."
    )


def _enforce_requirement(backend: sandbox.Backend, require: bool) -> None:
    """``--require-sandbox``: refuse rather than run unenforced.

    Spec §7 also gives this a ``=declared-fs`` form, which would
    additionally require a scoped filesystem. Every mechanism that exists
    today scopes it, so the two forms would be the same flag — it
    arrives with the first mechanism that does not (the container hatch,
    where a pod bounds only the OS layer).
    """
    if not require or backend.capability.kind != "none":
        return
    detail = f" — {backend.capability.detail}" if backend.capability.detail else ""
    raise ProjectError(f"--require-sandbox: no sandbox mechanism available on this host{detail}")


def describe(
    project: Path,
    policy: sandbox.Policy,
    attestation: sandbox.Attestation,
    argv: Sequence[str],
) -> list[str]:
    """The ``--sandbox-debug`` dump: the policy, verbatim, then the argv.

    Every path the boundary will grant, spelled out. It is long on
    purpose — the question it answers is "why was *that* denied", and a
    summary cannot answer it.
    """
    lines = [f"project:   {project}", f"sandbox:   {attestation.summary()}"]
    for label, paths in (
        ("read", policy.read),
        ("write", policy.write),
        ("execute", policy.execute),
    ):
        lines.append(f"{label + ':':10} {len(paths)} path(s)")
        lines.extend(f"             {path}" for path in paths)
    lines.append("env:")
    lines.extend(f"             {key}={value}" for key, value in sorted(policy.env.items()))
    lines.append("command:")
    lines.append(f"             {' '.join(argv)}")
    return lines
