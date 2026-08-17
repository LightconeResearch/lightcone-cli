"""Command-line interface for lightcone-cli — the ASTRA execution layer.

The redesigned CLI is a thin shim over Snakemake. Provenance integrity
(per-output content-addressed manifests) is implemented in
:mod:`lightcone.engine.manifest`; ``lc materialize`` generates a
Snakefile from ``astra.yaml`` and shells out to ``snakemake``.

Commands:
- ``lc init``        — idempotently converge a project scaffold;
  ``--check``/``--json`` for agents.
- ``lc materialize`` — generate Snakefile and run snakemake.
- ``lc run``         — probe: run an arbitrary command in the recipe
  environment (never materializes outputs).
- ``lc status``      — manifest-driven status walk (no Snakemake needed).
- ``lc verify``      — recompute hashes and validate the provenance chain.
- ``lc build``       — build container images.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path

import click
import yaml
from rich.console import Console

from lightcone.engine.container import ContainerBuildError

console = Console()
logger = logging.getLogger(__name__)


class _EngineErrorGroup(click.Group):
    """Render engine errors as clean CLI errors instead of tracebacks.

    The engine raises :class:`ContainerBuildError` from many entry
    points — tag hashing, builds, status walks. Translating once at the
    group boundary keeps every command, present and future, from leaking
    a raw traceback; click prints ``ClickException`` messages cleanly
    and exits 1.
    """

    def invoke(self, ctx: click.Context) -> object:
        try:
            return super().invoke(ctx)
        except ContainerBuildError as e:
            raise click.ClickException(str(e)) from e


@click.group(cls=_EngineErrorGroup)
@click.version_option(package_name="lightcone-cli")
@click.pass_context
def main(ctx: click.Context) -> None:
    """lightcone-cli — execution layer for ASTRA projects."""
    ctx.ensure_object(dict)


# =============================================================================
# Project discovery
# =============================================================================


def _project_root(start: Path | None = None) -> Path:
    """Walk up from cwd until we find ``astra.yaml``. Errors if absent."""
    p = (start or Path.cwd()).resolve()
    for parent in [p, *p.parents]:
        if (parent / "astra.yaml").is_file():
            return parent
    raise click.ClickException(
        "No astra.yaml found in current directory or any parent. "
        "Run `lc init` to create one."
    )


# =============================================================================
# lc init
# =============================================================================
_LIGHTCONE = """
_______________________
| . _ |_ _|_ _ _  _  _
|_|(_|| | | (_(_)| |(/_
_____|_________________
"""


@main.command()
@click.argument("directory", type=click.Path(file_okay=False, path_type=Path), default=".")
@click.option("--no-git", is_flag=True, help="Skip git init")
@click.option("--no-venv", is_flag=True, help="Skip Python venv creation")
@click.option(
    "--check",
    "check_only",
    is_flag=True,
    help=(
        "Report what would be created or repaired without writing anything; "
        "exit 1 if the project is not converged."
    ),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the convergence report as JSON on stdout.",
)
@click.option(
    "--scratch",
    "scratch_override",
    default=None,
    type=str,
    help=(
        "Scratch root for snakemake state, dask spill, and run locks. "
        "Overrides the site default. Shell expressions like $SCRATCH are "
        "expanded at run time (kept verbatim in the project config)."
    ),
)
def init(
    directory: Path,
    no_git: bool,
    no_venv: bool,
    check_only: bool,
    as_json: bool,
    scratch_override: str | None,
) -> None:
    """Converge DIRECTORY into an ASTRA project (idempotent).

    Safe to re-run at any time: creates whatever is missing, repairs the
    pieces lightcone manages, and never overwrites files you own.
    Problems it can see but must not fix (e.g. an unsupported directory
    COPY in your Containerfile) are reported as warnings. A directory
    that already holds an ``astra.yaml`` is adopted, not rejected.

    The spec scaffold (``astra.yaml``, ``universes/baseline.yaml``)
    follows the ``astra init`` boilerplate; on top of it sit the
    lightcone pieces: ``Containerfile`` + ``requirements.txt``,
    ``.gitignore`` entries, ``.lightcone/`` project state, a template
    MyST report (``myst.yml`` + ``index.md``), and an optional venv.
    """
    directory = directory.resolve()
    write = not check_only

    report: dict[str, list[str]] = {
        "created": [],
        "repaired": [],
        "unchanged": [],
        "warnings": [],
    }

    def _converge(name: str, present: bool, apply: Callable[[], object]) -> None:
        if present:
            report["unchanged"].append(name)
        else:
            report["created"].append(name)
            if write:
                apply()

    def _converge_file(
        name: str,
        path: Path,
        template: str,
        repair: Callable[[str], str | None] | None = None,
    ) -> None:
        """Create *path* from *template* if missing; else offer it to *repair*.

        ``repair`` receives the current text and returns the fixed text,
        or ``None`` when the file is already fine. Repairs must be
        conservative by construction — user-authored content is never
        touched.
        """
        if not path.exists():
            report["created"].append(name)
            if write:
                path.write_text(template)
        elif repair is not None and (fixed := repair(path.read_text())) is not None:
            report["repaired"].append(name)
            if write:
                path.write_text(fixed)
        else:
            report["unchanged"].append(name)

    if write:
        if not as_json:
            console.print(f"[cyan]{_LIGHTCONE}[/cyan]")
        directory.mkdir(parents=True, exist_ok=True)

    # Spec scaffold: astra.yaml + universes/baseline.yaml. astra's init
    # *command* refuses non-empty directories and overwrites .gitignore
    # — both wrong for convergence — so use the bare scaffold API and
    # manage .gitignore ourselves below.
    def _scaffold_spec() -> None:
        try:
            # Public API (LightconeResearch/astra-tools#99). The ignore
            # is for astra-tools releases that predate it; mypy will
            # flag it as unused once the dependency pin catches up.
            from astra.cli import create_boilerplate  # type: ignore[attr-defined]
        except ImportError:  # astra-tools ≤ 0.2.x without the public API
            create_boilerplate = None
        if create_boilerplate is not None:
            create_boilerplate(directory)
        else:
            from astra.cli import _create_boilerplate_astra_yaml

            (directory / "universes").mkdir(exist_ok=True)
            _create_boilerplate_astra_yaml(directory)
        # The boilerplate recipes reference scripts under src/ (e.g.
        # ``python src/main.py``); astra's own init creates the
        # directory, so the scaffold must too.
        (directory / "src").mkdir(exist_ok=True)
        # Point the spec at our project-local Containerfile. The astra
        # boilerplate ships a registry image so the scaffold is runnable
        # as-is, but we want lightcone projects to build their own image
        # so dependencies can evolve under content-addressed rebuilds.
        # Rewrite the top-level ``container:`` line whatever image the
        # boilerplate names, so astra bumping its default doesn't
        # silently disable the rewrite.
        astra_yaml_path = directory / "astra.yaml"
        rewritten = re.sub(
            r"(?m)^container:.*$",
            "container: Containerfile",
            astra_yaml_path.read_text(),
            count=1,
        )
        if "container: Containerfile" not in rewritten:
            report["warnings"].append(
                "astra.yaml: no top-level `container:` line found to point "
                "at the Containerfile; set it manually."
            )
        astra_yaml_path.write_text(rewritten)

    _converge("astra.yaml", (directory / "astra.yaml").exists(), _scaffold_spec)

    # One scaffold everywhere — the Containerfile is agnostic to the
    # execution environment. requirements.txt holds only the analysis
    # dependencies; the execution stack (lightcone-cli, which carries
    # snakemake, dask, distributed, dask-gateway) is a separate
    # Containerfile layer so the same image can wrap recipes locally or
    # run as a Dask Gateway worker pod on a hub, while the project venv
    # stays free of it — `lc` lives outside the venv. Anything
    # pod-specific (uid, mounts) is deployment configuration, not image
    # content.
    cf_path = directory / "Containerfile"
    _converge_file(
        "Containerfile",
        cf_path,
        _CONTAINERFILE_TEMPLATE.format(lc_requirement=_lightcone_requirement()),
    )
    # Advisory: a Containerfile with directory COPY sources belongs to
    # the user, so init won't edit it — but lc build / lc run will
    # reject it, so say so now rather than at build time.
    if cf_path.is_file():
        from lightcone.engine.container import directory_copy_sources

        if bad := directory_copy_sources(cf_path, directory):
            report["warnings"].append(
                "Containerfile: COPY/ADD of a directory "
                f"({', '.join(repr(s) for s in bad)}) is not supported and "
                "lc build/run will fail. The image is an environment — "
                "recipes run against the live project tree, so remove the "
                "line(s)."
            )
    _converge_file(
        "requirements.txt",
        directory / "requirements.txt",
        _REQUIREMENTS,
    )

    # .gitignore: create with base + lightcone entries if absent; append
    # the block once to a user-owned file (keyed on the "# lightcone-cli"
    # marker).
    _converge_file(
        ".gitignore",
        directory / ".gitignore",
        _GITIGNORE_BASE + _GITIGNORE_APPEND,
        repair=_repair_gitignore,
    )

    # .lightcone/ project state dir + lightcone.yaml. An explicit
    # --scratch converges the stored scratch_root; without it an
    # existing config is left alone. A file we can't parse is left
    # untouched and reported — init must stay safe to re-run.
    cfg_path = directory / ".lightcone" / "lightcone.yaml"
    cfg_name = ".lightcone/lightcone.yaml"
    if not cfg_path.exists():
        report["created"].append(cfg_name)
        if write:
            cfg_path.parent.mkdir(exist_ok=True)
            project_cfg: dict[str, object] = {"target": "local"}
            if scratch_override:
                project_cfg["scratch_root"] = scratch_override
            cfg_path.write_text(yaml.safe_dump(project_cfg))
    else:
        try:
            existing_cfg = yaml.safe_load(cfg_path.read_text())
        except yaml.YAMLError:
            existing_cfg = None
        if not isinstance(existing_cfg, dict):
            report["unchanged"].append(cfg_name)
            report["warnings"].append(
                f"{cfg_name} is not a valid YAML mapping; left untouched"
                + (" (--scratch not applied)" if scratch_override else "")
                + "."
            )
        elif scratch_override and existing_cfg.get("scratch_root") != scratch_override:
            report["repaired"].append(cfg_name)
            if write:
                existing_cfg["scratch_root"] = scratch_override
                cfg_path.write_text(yaml.safe_dump(existing_cfg))
        else:
            report["unchanged"].append(cfg_name)

    # results/ ships with a README explaining the materialization
    # contract — the placeholder directory alone is invisible in git
    # (empty + ignored), so the README is what actually tells a human
    # or agent opening the project where outputs land and that they
    # must come from `lc run`, not be written by hand.
    results_dir = directory / "results"
    if results_dir.exists() and not results_dir.is_dir():
        report["unchanged"].extend(["results/", "results/README.md"])
        report["warnings"].append(
            "results exists but is not a directory; outputs cannot "
            "materialize until it is one."
        )
    else:
        _converge("results/", results_dir.is_dir(), results_dir.mkdir)
        _converge_file("results/README.md", results_dir / "README.md", _RESULTS_README)

    # Template MyST report. MyST support is a recommended add-on on top of
    # the spec, not part of it — which is why the report scaffold lives here
    # and not in `astra init`.
    _converge_file("myst.yml", directory / "myst.yml", _MYST_YML)
    project_name = directory.name or "My Analysis"
    _converge_file("index.md", directory / "index.md", f"# {project_name}\n" + _INDEX_MD_BODY)

    if not no_git:
        _converge(
            ".git",
            (directory / ".git").exists(),
            lambda: subprocess.run(["git", "init", "-q"], cwd=directory, check=False),
        )

    if not no_venv:
        _converge(
            ".venv",
            (directory / ".venv").exists(),
            lambda: _create_venv(directory, quiet=as_json),
        )

    converged = not report["created"] and not report["repaired"]

    if as_json:
        payload: dict[str, object] = {"converged": converged, **report}
        click.echo(json.dumps(payload, indent=2))
    elif check_only:
        if converged:
            console.print(f"[green]✓[/green] {directory} is converged — nothing to do")
        else:
            for item in report["created"]:
                console.print(f"  [yellow]would create[/yellow] {item}")
            for item in report["repaired"]:
                console.print(f"  [yellow]would repair[/yellow] {item}")
        for warning in report["warnings"]:
            console.print(f"  [yellow]![/yellow] {warning}")
    else:
        for item in report["created"]:
            console.print(f"[green]✓[/green] created {item}")
        for item in report["repaired"]:
            console.print(f"[green]✓[/green] repaired {item}")
        for warning in report["warnings"]:
            console.print(f"[yellow]![/yellow] {warning}")
        if converged:
            console.print(f"\n[green]Project already converged at[/green] {directory}")
        else:
            console.print(f"\n[green]Project converged at[/green] {directory}")

        # Next steps only make sense for a freshly scaffolded spec.
        if "astra.yaml" in report["created"]:
            console.print("\nNext steps:")
            console.print(
                f"  • Go to the newly created directory [cyan]cd {directory}[/cyan]"
            )
            console.print(
                "  • Describe your analysis in [cyan]astra.yaml[/cyan], "
                "then materialize it with [cyan]lc materialize[/cyan]"
            )
            console.print(
                "  • Preview the report with [cyan]myst start[/cyan] "
                "(requires the MyST CLI: [cyan]npm i -g mystmd[/cyan])"
            )

    if check_only and not converged:
        sys.exit(1)


def _create_venv(directory: Path, quiet: bool = False) -> None:
    """Create ``.venv`` in ``directory`` with the analysis dependencies.

    Installs ``requirements.txt`` only — deliberately *not*
    lightcone-cli. The venv exists to run the analysis code; ``lc``
    itself lives outside it (e.g. ``uv tool install lightcone-cli``),
    and a second copy inside the venv would shadow it with whatever
    version PyPI resolves.
    """

    def _status(msg: str) -> AbstractContextManager[object]:
        return nullcontext() if quiet else console.status(msg)

    if shutil.which("uv"):
        with _status("[dim]Creating virtual environment…[/dim]"):
            subprocess.run(
                ["uv", "venv", "--python", "3.12", ".venv"],
                cwd=directory,
                check=False,
                capture_output=True,
            )
        with _status("[dim]Installing project requirements…[/dim]"):
            subprocess.run(
                [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    ".venv/bin/python",
                    "-r",
                    "requirements.txt",
                ],
                cwd=directory,
                check=False,
                capture_output=True,
            )
    else:
        with _status("[dim]Creating virtual environment…[/dim]"):
            subprocess.run(
                ["python", "-m", "venv", ".venv"],
                cwd=directory,
                check=False,
                capture_output=True,
            )
        with _status("[dim]Installing project requirements…[/dim]"):
            subprocess.run(
                [
                    ".venv/bin/python",
                    "-m",
                    "pip",
                    "install",
                    "-q",
                    "-r",
                    "requirements.txt",
                ],
                cwd=directory,
                check=False,
                capture_output=True,
            )


_CONTAINERFILE_TEMPLATE = """\
FROM python:3.12-slim

WORKDIR /app

# Execution stack — lets this image run rules on any backend, including
# as a Dask Gateway worker pod. Kept out of requirements.txt so the
# project venv stays free of it (`lc` lives outside the venv), and
# installed first so this heavy layer stays cached across
# requirements.txt edits.
RUN pip install --no-cache-dir {lc_requirement}

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# No COPY of the project source: recipes run against the live project
# tree (bind-mounted locally, shared filesystem on a hub), so the image
# is a pure environment — it only rebuilds when dependencies change,
# never on code edits.
"""


_REQUIREMENTS = """\
numpy
pandas
"""


def _repair_gitignore(text: str) -> str | None:
    """Append the managed block once to a user-owned .gitignore.

    Keyed on the block's ``# lightcone-cli`` marker so re-runs never
    duplicate it. This is `lc init`'s only repair hook: adoption of a
    project that already has its own .gitignore.
    """
    if "# lightcone-cli" not in text:
        return text + _GITIGNORE_APPEND
    # Legacy managed-block upgrades, applied line-by-line so everything
    # else in the file stays user territory:
    # * a bare ``results/`` rule ignores the whole directory, and git
    #   cannot re-include results/README.md beneath an excluded
    #   directory;
    # * trailing-slash ``.snakemake/`` entries never match the symlink
    #   that ``.snakemake`` becomes under a scratch root.
    slash_fixes = {
        ".snakemake/": ".snakemake",
        ".snakemake.legacy/": ".snakemake.legacy",
    }
    out: list[str] = []
    changed = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "results/":
            out.append("results/*")
            if "!results/README.md" not in text:
                out.append("!results/README.md")
            changed = True
        elif stripped in slash_fixes:
            out.append(slash_fixes[stripped])
            changed = True
        else:
            out.append(line)
    if changed:
        return "\n".join(out) + ("\n" if text.endswith("\n") else "")
    return None


def _lightcone_requirement() -> str:
    """The lightcone-cli requirement pinned into the project image.

    The project image must be able to execute rules on any backend —
    including as a Dask Gateway worker pod, where the dask worker and
    the child snakemake run *inside* the image. lightcone-cli carries
    that whole stack (snakemake, dask, distributed, dask-gateway) as
    normal dependencies, so one requirement covers it. The pin mirrors
    the version running ``lc init`` to keep driver and image in
    lockstep; dev builds fall back to unpinned (their version isn't
    published).
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        v = version("lightcone-cli")
    except PackageNotFoundError:
        v = ""
    return f"lightcone-cli=={v}" if v and "dev" not in v else "lightcone-cli"


# Written when the project has no .gitignore of its own; mirrors the base
# entries `astra init` would have written.
_GITIGNORE_BASE = """\
# ASTRA Analysis
__pycache__/
*.py[cod]
.venv/
.ipynb_checkpoints/
.DS_Store
"""


# Managed block appended to any pre-existing .gitignore; the
# "# lightcone-cli" marker keeps the append idempotent. The
# ``.snakemake`` entries have no trailing slash on purpose: when a
# scratch root is active the project's ``.snakemake`` is a *symlink*
# into scratch, and a trailing-slash pattern matches only real
# directories.
_GITIGNORE_APPEND = """
# lightcone-cli
.lightcone/Snakefile
.lightcone/snakefile-config.json
.snakemake
.snakemake.legacy
results/*
!results/README.md

# MyST build output
_build/
"""


_RESULTS_README = """\
# results/

Materialized outputs land here, one directory per universe and output:

    results/<universe>/<output_id>/

Each output directory carries a `.lightcone-manifest.json` sidecar
recording exactly how it was produced: recipe, container image,
decisions, input hashes, and the output's content hash.

- Produce or refresh outputs with `lc materialize` — never write files
  here by hand. Hand-placed or edited files fail `lc verify` (the
  content hash won't match, and a missing manifest forces a re-run).
- `lc status` shows what is materialized, stale, or missing.
- Everything in this directory except this README is git-ignored;
  outputs are reproducible from `astra.yaml`, not versioned.
"""


# The template report references the boilerplate ``astra.yaml`` elements by
# path via the MySTRA plugin, so the ids used below must track the astra
# boilerplate (``example_method``, ``main_result``).
_MYST_YML = """\
# MyST configuration for the analysis report (https://mystmd.org/).
# The MySTRA plugin resolves {astra}`...` references against astra.yaml.
# `latest` always tracks the newest MySTRA release; for a reproducible
# build, pin a tag instead, e.g. .../releases/download/v0.0.1/mystra.mjs
version: 1
project:
  plugins:
    - https://github.com/LightconeResearch/MySTRA/releases/latest/download/mystra.mjs
  toc:
    - file: index.md
site:
  template: book-theme
"""


_INDEX_MD_BODY = """
> **TODO:** this report was scaffolded by `lc init`. It references the
> analysis elements declared in `astra.yaml` *by path* — figures, decisions,
> and numbers stay single-sourced in the analysis, so never hard-type a
> measured value here. Preview with `myst start` (requires the MyST CLI).

## Introduction

TODO: the research question, its context, and why it matters.

## Methods

TODO: describe the approach. Reference the decisions the analysis exposes
rather than restating them — for example, we adopt the
{astra}`decisions.example_method` for this analysis:

:::{astra} decisions.example_method
:::

## Results

TODO: present the outputs. Once `lc run` has materialized results, pull
numbers in live, e.g.:

% The analysis yields {astra:value}`outputs.main_result`.

:::{astra} outputs
:::
"""

# =============================================================================
# lc materialize
# =============================================================================


@main.command()
@click.argument("outputs", nargs=-1)
@click.option("--universe", "-u", default=None, help="Universe to materialize")
@click.option("--jobs", "-j", default=None, type=int, help="Parallel jobs")
@click.option(
    "--rerun-triggers",
    default="code,input,mtime,params",
    help="Comma-separated rerun-triggers (default: code,input,mtime,params)",
)
@click.option("--force", "-f", is_flag=True, help="Force re-materialization")
@click.option("--verbose", "-v", is_flag=True, help="Show full executor output")
def materialize(
    outputs: tuple[str, ...],
    universe: str | None,
    jobs: int | None,
    rerun_triggers: str,
    force: bool,
    verbose: bool,
) -> None:
    """Materialize outputs declared in astra.yaml.

    Dispatches through a run-scoped Dask ``LocalCluster``.
    """
    from lightcone.engine.container import load_runtime
    from lightcone.engine.dask_cluster import cluster_for_run
    from lightcone.engine.scratch import (
        RunLockBusyError,
        acquire_run_lock,
        ensure_snakemake_symlink,
        prepare_run_dirs,
        resolve_scratch_root,
    )
    from lightcone.engine.snakefile import discover_universes, generate

    project = _project_root()
    universes = [universe] if universe else discover_universes(project)

    # Resolve scratch and prepare per-run directories before anything
    # else. Snakemake's ``.snakemake/`` is redirected via symlink so its
    # workflow lock and metadata land under the scratch root; dask spill
    # and the run lock live alongside it.
    rundirs = prepare_run_dirs(project)
    ensure_snakemake_symlink(project, rundirs.snakemake_state)
    if verbose:
        console.print(f"[dim]Scratch root:[/dim] {resolve_scratch_root(project)}")

    choice = load_runtime(project_path=project)
    _ensure_images(project, runtime=choice.runtime)
    snakefile_path, _cfg_path = generate(
        project, universes=universes, runtime=choice.runtime
    )

    targets: list[str] = []
    if outputs:
        for o in outputs:
            for u in universes:
                targets.append(_target_for(project, o, u))
    # If no specific targets, pass nothing → snakemake runs `rule all`.

    n = str(jobs or os.cpu_count() or 1)
    # Snakemake requires ``--cores`` to bound per-rule CPU; the dask
    # plugin requires ``--jobs`` to bound parallel dispatch. We surface
    # one knob and pass it as both.
    cmd = _build_snakemake_cmd(
        snakefile_path=snakefile_path,
        project=project,
        n=n,
        rerun_triggers=rerun_triggers,
        targets=targets,
        force=force,
        has_outputs=bool(outputs),
    )

    # Hold a project-level flock for the duration of the run. Acquiring
    # it also clears any stale snakemake lock left by a previously
    # crashed invocation — safe because we just proved we're alone on
    # the project. Concurrent ``lc run`` on the same project bails
    # cleanly rather than corrupting Snakemake state.
    try:
        run_lock_cm = acquire_run_lock(rundirs)
        run_lock_cm.__enter__()
    except RunLockBusyError as e:
        raise click.ClickException(str(e))

    with cluster_for_run(
        verbose=verbose,
        local_directory=str(rundirs.dask_local),
        max_workers=int(n),
    ) as cluster_env:
        env = {**os.environ, **cluster_env}
        if verbose:
            console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
        sys.exit(
            _run_snakemake(
                cmd, env=env, scratch_root=rundirs.root, verbose=verbose
            )
        )


def _run_snakemake(
    cmd: list[str],
    *,
    env: dict[str, str],
    scratch_root: Path,
    verbose: bool,
) -> int:
    """Run snakemake, forwarding the run's narrative output.

    The executor plugin prints each finished rule's block of
    sentinel-prefixed lines (see :data:`lightcone.engine.runner.SENTINEL`)
    on the snakemake process's stdout. We forward those lines — prefix
    stripped — to the terminal and drop everything else snakemake emits
    (DAG chatter, job stats), so a run reads as a clean narrative.
    Verbose mode forwards the noise too.

    stderr is tailed into a bounded ring buffer so a workflow crash
    leaves a real log behind without it being visible during a
    successful run (verbose passes stderr straight through instead).
    """
    from collections import deque

    from lightcone.engine.runner import SENTINEL

    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=None if verbose else subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    tail: deque[str] = deque(maxlen=400)

    def _pump_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            tail.append(line)

    import threading

    stderr_thread: threading.Thread | None = None
    if not verbose:
        stderr_thread = threading.Thread(target=_pump_stderr, daemon=True)
        stderr_thread.start()

    for line in proc.stdout:
        if line.startswith(SENTINEL):
            sys.stdout.write(line[len(SENTINEL):])
            sys.stdout.flush()
        elif verbose:
            sys.stdout.write(line)
            sys.stdout.flush()

    rc = proc.wait()
    if stderr_thread is not None:
        stderr_thread.join(timeout=5)
    if rc != 0 and not verbose:
        log = scratch_root / f"snakemake-stderr-{os.getpid()}.log"
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text("".join(tail))
            console.print(
                f"\n[red]✗ Workflow failed.[/red] "
                f"Last snakemake stderr saved to [cyan]{log}[/cyan]."
            )
        except OSError:
            # Last-ditch: dump to stderr if scratch is unwritable.
            sys.stderr.write("".join(tail))
    return rc


def _build_snakemake_cmd(
    *,
    snakefile_path: Path,
    project: Path,
    n: str,
    rerun_triggers: str,
    targets: list[str],
    force: bool,
    has_outputs: bool,
) -> list[str]:
    """Build the snakemake argv list for ``lc run``.

    ``--rerun-triggers`` uses ``nargs=+`` in snakemake's argparse, so without
    an explicit ``--`` separator it greedily consumes the first positional
    target path as an extra trigger value, causing an "invalid choice" error.

    ``--shared-fs-usage`` lists everything *except*
    ``software-deployment``. With it included (snakemake's default),
    spawned job commands embed the *driver's* ``sys.executable`` — a
    path that doesn't exist inside a Dask Gateway worker image. Without
    it, workers invoke plain ``python`` from their own environment,
    which is equally correct on the other backends: LocalCluster
    threads and srun-launched SLURM workers inherit the driver's
    activated environment (and SLURM setups already require it — see
    the ``dask``-on-PATH check in the cluster module). One invocation
    shape for every backend; everything else stays shared via the
    common filesystem (persistence, inputs/outputs, sources).
    """
    cmd: list[str] = [
        "snakemake",
        "-s",
        str(snakefile_path),
        "-d",
        str(project),
        "--cores",
        n,
        "--jobs",
        n,
        "--executor",
        "dask",
        "--shared-fs-usage",
        "persistence",
        "input-output",
        "sources",
        "storage-local-copies",
        "source-cache",
        "--rerun-triggers",
        *rerun_triggers.split(","),
    ]
    if force:
        # ``--force`` scopes to explicit targets; ``rule all`` itself
        # has no recipe, so force-all is the only useful sense when no
        # targets were named.
        cmd.append("--force" if has_outputs else "--forceall")
    if targets:
        cmd.append("--")
    cmd.extend(targets)
    return cmd


def _target_for(project: Path, output_id: str, universe: str) -> str:
    """Translate an output id into a Snakemake target path (the manifest).

    Accepts either a bare ``output_id`` (root-level or unique sub-analysis
    output) or a qualified ``analysis_id.output_id`` to disambiguate when
    the same id appears in multiple sub-analyses.
    """
    from astra.helpers import load_yaml, resolve_analysis_tree

    from lightcone.engine.manifest import MANIFEST_FILENAME
    from lightcone.engine.tree import collect_tree_outputs, resolve_output_path

    spec = resolve_analysis_tree(load_yaml(project / "astra.yaml"), project)
    matches = []
    for to in collect_tree_outputs(spec):
        if to.output_def.get("recipe") is None:
            continue
        qualified = (
            f"{to.analysis_id}.{to.output_id}" if to.analysis_id else to.output_id
        )
        if qualified == output_id or to.output_id == output_id:
            matches.append((qualified, to))

    if not matches:
        raise click.ClickException(
            f"Output '{output_id}' not found in astra.yaml or has no recipe."
        )
    if len(matches) > 1:
        opts = ", ".join(q for q, _ in matches)
        raise click.ClickException(
            f"Output '{output_id}' is ambiguous; qualify it as one of: {opts}"
        )

    _, to = matches[0]
    target = (
        resolve_output_path(project, to, universe) / to.output_id / MANIFEST_FILENAME
    )
    return str(target.relative_to(project))


# =============================================================================
# lc run — the probe verb
# =============================================================================


def _declared_output_ids(project: Path) -> set[str]:
    """All declared output ids, bare and ``analysis.output``-qualified."""
    from astra.helpers import load_yaml, resolve_analysis_tree

    from lightcone.engine.tree import collect_tree_outputs

    spec = resolve_analysis_tree(load_yaml(project / "astra.yaml"), project)
    ids: set[str] = set()
    for to in collect_tree_outputs(spec):
        ids.add(to.output_id)
        if to.analysis_id:
            ids.add(f"{to.analysis_id}.{to.output_id}")
    return ids


@main.command(context_settings={"ignore_unknown_options": True})
@click.argument("cmd", nargs=-1, type=click.UNPROCESSED)
def run(cmd: tuple[str, ...]) -> None:
    """Run CMD inside the recipe environment (a probe).

    The command executes with the project's locked environment — the
    same interpreter and packages recipes see — via
    ``uv run --locked --exact``. Probes never materialize outputs; use
    ``lc materialize`` for that. With no CMD, opens a shell in the
    recipe environment.
    """
    project = _project_root()

    # Rename guard (v6 reassigned `lc run` from pipeline execution to
    # probing): a first argument naming a declared output errors before
    # any exec — silently exec'ing `best_fit` as a command would be a
    # far worse failure mode than a pointed redirect.
    if cmd and not cmd[0].startswith("-") and cmd[0] in _declared_output_ids(project):
        raise click.ClickException(
            "outputs are materialized, not run — did you mean: "
            f"`lc materialize {cmd[0]}`?"
        )

    if not (project / "pyproject.toml").is_file():
        raise click.ClickException(
            "lc run needs a uv project (pyproject.toml + uv.lock) to know "
            "the recipe environment; this project predates the uv model. "
            "Run `lc init` to scaffold it."
        )

    if not cmd:
        console.print("[dim]opening a shell inside the recipe environment[/dim]")
        cmd = (os.environ.get("SHELL") or "bash",)

    proc = subprocess.run(
        ["uv", "run", "--locked", "--exact", "--project", str(project), "--", *cmd],
        cwd=project,
    )
    sys.exit(proc.returncode)


# =============================================================================
# lc status
# =============================================================================


@main.command()
@click.option("--universe", "-u", default=None)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit machine-readable JSON instead of a styled table.",
)
def status(universe: str | None, as_json: bool) -> None:
    """Report materialization status for every declared output."""
    from lightcone.engine.snakefile import discover_universes
    from lightcone.engine.status import get_output_status

    project = _project_root()
    universes = [universe] if universe else discover_universes(project)

    if as_json:
        payload = {
            "universes": [
                {
                    "universe_id": u,
                    "outputs": [
                        {
                            "output_id": s.output_id,
                            "analysis_id": s.analysis_id,
                            "status": s.status,
                            "recipe_command": s.recipe_command,
                        }
                        for s in get_output_status(project, universe_id=u)
                    ],
                }
                for u in universes
            ],
        }
        click.echo(json.dumps(payload, indent=2))
        return

    for u in universes:
        console.print(f"\n[bold]Universe[/bold] [cyan]{u}[/cyan]")
        for s in get_output_status(project, universe_id=u):
            label = _status_label(s.status)
            scope = f"[dim]{s.analysis_id}.[/dim]" if s.analysis_id else ""
            console.print(f"  {label}  {scope}{s.output_id}")


_STATUS_STYLES = {
    "ok": "[green]✓ ok[/green]    ",
    "stale": "[yellow]✸ stale[/yellow] ",
    "missing": "[red]✗ miss[/red]  ",
    "alias": "[dim]→ alias[/dim] ",
}


def _status_label(s: str) -> str:
    return _STATUS_STYLES.get(s, s)


# =============================================================================
# lc verify
# =============================================================================


@main.command()
@click.option("--universe", "-u", default=None)
def verify(universe: str | None) -> None:
    """Validate the provenance chain by recomputing hashes."""
    from lightcone.engine.snakefile import discover_universes
    from lightcone.engine.verify import verify_outputs

    project = _project_root()
    universes = [universe] if universe else discover_universes(project)

    failed = 0
    for u in universes:
        console.print(f"\n[bold]Universe[/bold] [cyan]{u}[/cyan]")
        for r in verify_outputs(project, universe_id=u):
            if r.passed:
                console.print(f"  [green]✓ ok[/green]    {r.output_id}")
            else:
                failed += 1
                console.print(
                    f"  [red]✗ {r.failure}[/red]  {r.output_id}  [dim]{r.detail}[/dim]"
                )

    if failed:
        console.print(f"\n[bold red]{failed} integrity failure(s).[/bold red]")
        sys.exit(1)
    console.print("\n[green]All outputs verified.[/green]")


# =============================================================================
# lc build
# =============================================================================


@main.command()
@click.option("--force", is_flag=True, help="Rebuild all images even if cached")
def build(force: bool) -> None:
    """Build container images declared in astra.yaml.

    Containerfile syntax is Dockerfile syntax — built with ``podman``.
    Each Containerfile builds to an OCI image tagged
    ``lc-<project>-<hash>`` in the local image store. Pre-built registry
    images (``python:3.12-slim``, ``ghcr.io/foo/bar:tag``) are pulled.
    """
    from lightcone.engine.container import load_runtime

    project = _project_root()
    resolved_runtime = load_runtime(project_path=project).runtime

    if resolved_runtime == "none":
        console.print(
            "[yellow]podman is not on PATH — install it to build "
            "container images.[/yellow]"
        )
        return

    _ensure_images(project, runtime=resolved_runtime, force=force)
    console.print("[green]Done.[/green]")


def _ensure_images(project: Path, *, runtime: str, force: bool = False) -> list[str]:
    """Build/pull every container image referenced in astra.yaml.

    Returns the distinct resolved images, in declaration order (local
    tags for Containerfile specs, prebuilt specs as-is). No-op (and
    empty) when *runtime* is ``"none"``.

    Idempotent: skips images already present in the local image store.
    Used by ``lc build`` (with ``--force`` exposed) and as a pre-flight
    by ``lc materialize`` so the first invocation after editing a
    Containerfile doesn't fail mid-DAG with a missing image.
    """
    if runtime == "none":
        return []

    from astra.helpers import load_yaml, resolve_analysis_tree

    from lightcone.engine.container import (
        build_image,
        compute_image_tag,
        image_exists_locally,
        is_containerfile,
        pull_image,
    )
    from lightcone.engine.tree import collect_tree_outputs

    spec = resolve_analysis_tree(load_yaml(project / "astra.yaml"), project)
    project_name = (spec.get("name") or project.name).lower().replace(" ", "-")

    images: list[str] = []
    seen: set[str] = set()
    for to in collect_tree_outputs(spec):
        recipe = to.output_def.get("recipe") or {}
        spec_str = (
            recipe.get("container")
            or to.analysis_spec.get("container")
            or spec.get("container")
        )
        if not spec_str or spec_str in seen:
            continue
        seen.add(spec_str)
        if not is_containerfile(spec_str, project):
            images.append(spec_str)
            # Pull so ``lc materialize`` can use ``--pull=never`` without
            # depending on the runtime's registry resolution.
            if image_exists_locally(spec_str, runtime=runtime) and not force:
                continue
            console.print(f"[cyan]Pulling[/cyan] {spec_str} [dim](via {runtime})[/dim]")
            pull_image(spec_str, runtime=runtime)
            continue

        containerfile = project / spec_str
        tag = compute_image_tag(project_name, containerfile, project)
        images.append(tag)
        if image_exists_locally(tag, runtime=runtime) and not force:
            continue
        console.print(
            f"[cyan]Building[/cyan] {spec_str} → {tag} [dim](via {runtime})[/dim]"
        )
        build_image(tag, containerfile, project, runtime=runtime)
    return images


# =============================================================================
# lc export
# =============================================================================


@main.group()
def export() -> None:
    """Export project artifacts in interoperable formats."""


@export.command("wrroc")
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    default=Path("./wrroc"),
    help="Bundle directory (or .zip path with --zip).",
    show_default=True,
)
@click.option(
    "--universe",
    "-u",
    multiple=True,
    help="Restrict to specific universes (default: all).",
)
@click.option(
    "--author",
    default=None,
    help='Author override, e.g. "Name <email@host>". Default: git config.',
)
@click.option(
    "--license",
    "license_url",
    default=None,
    help="License URL or SPDX identifier. Default: CC-BY-4.0.",
)
@click.option(
    "--zip/--no-zip",
    "zip_bundle",
    default=False,
    help="Package the bundle as a .zip after building.",
)
@click.option(
    "--metadata-only",
    is_flag=True,
    help="Skip data files; bundle manifests + astra.yaml + universes only.",
)
def export_wrroc_cmd(
    output: Path,
    universe: tuple[str, ...],
    author: str | None,
    license_url: str | None,
    zip_bundle: bool,
    metadata_only: bool,
) -> None:
    """Export a Workflow Run RO-Crate (WRROC) bundle.

    The bundle is suitable for upload to WorkflowHub, Zenodo (with the
    RO-Crate plugin), or any RO-Crate-aware archive. The lightcone
    manifest format on disk is unchanged — this is a publication view
    generated on demand.

    Examples:

      lc export wrroc                                 # ./wrroc/ directory
      lc export wrroc -o my-run.zip --zip             # zip bundle
      lc export wrroc --metadata-only                 # provenance, no data
      lc export wrroc -u baseline -u alt              # specific universes
    """
    from lightcone.engine.wrroc import export_wrroc

    project = _project_root()

    try:
        result = export_wrroc(
            project_path=project,
            output_path=output,
            universes=list(universe) or None,
            author=author,
            license=license_url,
            zip_bundle=zip_bundle,
            include_data=not metadata_only,
        )
    except FileExistsError as e:
        raise click.ClickException(str(e))

    flavor = "zip bundle" if result.is_zip else "directory"
    console.print(
        f"[green]✓[/green] Wrote WRROC {flavor}: [cyan]{result.bundle_path}[/cyan]"
    )
    if result.runs_included == 0:
        console.print(
            "[yellow]Warning:[/yellow] no materialized outputs were found — "
            "the bundle contains only the workflow definition.\n"
            "  This usually means recipes haven't been run yet "
            "(try [cyan]lc materialize[/cyan]) "
            "or the [cyan].lightcone-manifest.json[/cyan] sidecars are missing.\n"
            "  Workflow-only bundles will not pass strict Provenance Run Crate "
            "validation; that profile requires at least one materialized run."
        )
    else:
        u_list = ", ".join(result.universes_included)
        console.print(
            f"  Captured [bold]{result.runs_included}[/bold] runs across "
            f"universes: [cyan]{u_list}[/cyan]"
        )
