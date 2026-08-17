"""Command-line interface for lightcone-cli — the ASTRA execution layer.

The CLI is a thin shim over Snakemake. Provenance integrity (per-output
content-addressed manifests) is implemented in
:mod:`lightcone.engine.manifest`; the environment model (uv as the only
substrate, mode derived from the ``[tool.lightcone.image]`` hatch) in
:mod:`lightcone.engine.environment`.

Commands:
- ``lc init``        — idempotently converge a project scaffold;
  ``--check``/``--json`` for agents.
- ``lc materialize`` — generate Snakefile and run snakemake.
- ``lc run``         — probe: run an arbitrary command in the recipe
  environment (never materializes outputs).
- ``lc status``      — manifest-driven status walk (no Snakemake needed).
- ``lc verify``      — recompute hashes and validate the provenance chain.
- ``lc build``       — build the project's environment image
  (containerized mode).
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
from pathlib import Path

import click
import yaml
from rich.console import Console

from lightcone.engine.environment import ProjectEnvironmentError
from lightcone.engine.image.errors import ImageError

console = Console()
logger = logging.getLogger(__name__)


class _EngineErrorGroup(click.Group):
    """Render engine errors as clean CLI errors instead of tracebacks.

    The engine raises :class:`ProjectEnvironmentError` and
    :class:`ImageError` from many entry points — environment loading,
    identity hashing, builds, status walks. Translating once at the
    group boundary keeps every command, present and future, from leaking
    a raw traceback; click prints ``ClickException`` messages cleanly
    and exits 1.
    """

    def invoke(self, ctx: click.Context) -> object:
        try:
            return super().invoke(ctx)
        except (ProjectEnvironmentError, ImageError) as e:
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
    from lightcone.engine.project import find_root

    root = find_root(start)
    if root is None:
        raise click.ClickException(
            "No astra.yaml found in current directory or any parent. "
            "Run `lc init` to create one."
        )
    return root


def _load_env(project: Path):  # type: ignore[no-untyped-def]
    from lightcone.engine.environment import load_environment

    return load_environment(project)


def _refuse_containerized_interim(mode: object) -> None:
    """Temporary gate while the podman image backend lands.

    Direct mode is fully functional; running a containerized project's
    recipes on the host instead would record provenance that
    misrepresents what executed — refuse rather than pretend.
    """
    from lightcone.engine.environment import Mode

    if mode is Mode.CONTAINERIZED:
        raise click.ClickException(
            "containerized mode ([tool.lightcone.image]) is not executable "
            "yet in this development build — the podman image backend "
            "lands in a later migration phase. Remove the declaration to "
            "run in direct mode."
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


def _run_uv(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """One seam for every uv invocation init makes (tests monkeypatch it)."""
    return subprocess.run(
        ["uv", *args], cwd=cwd, capture_output=True, text=True, check=False
    )


@main.command()
@click.argument("directory", type=click.Path(file_okay=False, path_type=Path), default=".")
@click.option("--no-git", is_flag=True, help="Skip git init")
@click.option(
    "--no-sync",
    is_flag=True,
    help="Skip materializing the .venv (uv lock still runs).",
)
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
        "Shell expressions like $SCRATCH are expanded at run time "
        "(kept verbatim in the project config)."
    ),
)
def init(
    directory: Path,
    no_git: bool,
    no_sync: bool,
    check_only: bool,
    as_json: bool,
    scratch_override: str | None,
) -> None:
    """Converge DIRECTORY into an ASTRA project (idempotent).

    Safe to re-run at any time: creates whatever is missing, repairs the
    pieces lightcone manages, and never overwrites files you own. A
    directory that already holds an ``astra.yaml`` is adopted, not
    rejected.

    The spec scaffold (``astra.yaml``, ``universes/baseline.yaml``)
    follows the ``astra init`` boilerplate; on top of it sit the uv
    project (``pyproject.toml`` with lightcone-cli locked in,
    ``.python-version``, ``uv.lock``, ``.venv``), the agent notes
    stanza (AGENTS.md), ``.gitignore`` entries, ``.lightcone/`` project
    state, and a template MyST report (``myst.yml`` + ``index.md``).
    """
    directory = directory.resolve()
    write = not check_only

    # Refusal before any write: an authored Containerfile is the v3-era
    # model. The user's own file operation is the consent to migrate —
    # no flag can substitute for it.
    if (directory / "Containerfile").is_file():
        raise click.ClickException(
            f"{directory}/Containerfile: v6 generates images from the "
            "lock — delete or rename it, then re-run `lc init`. Declare "
            "system dependencies in [tool.lightcone.image] instead."
        )

    if shutil.which("uv") is None:
        raise click.ClickException(
            "uv is required (the environment substrate). Install it: "
            "https://docs.astral.sh/uv/getting-started/installation/"
        )

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
        # ASTRA carries only analysis structure — the environment lives
        # in pyproject.toml + uv.lock. Strip any ``container:`` line the
        # boilerplate ships.
        astra_yaml_path = directory / "astra.yaml"
        stripped = re.sub(
            r"(?m)^container:.*\n?", "", astra_yaml_path.read_text(), count=1
        )
        astra_yaml_path.write_text(stripped)

    _converge("astra.yaml", (directory / "astra.yaml").exists(), _scaffold_spec)

    # The uv project: pyproject.toml (virtual — no [build-system]) with
    # the engine inside the experiment's lock, and the exact interpreter
    # pin. Existing files are the user's: verified, never edited.
    pyproject_path = directory / "pyproject.toml"
    if not pyproject_path.exists():
        report["created"].append("pyproject.toml")
        if write:
            pyproject_path.write_text(
                _PYPROJECT_TEMPLATE.format(
                    name=_project_name(directory),
                    lc_requirement=_lightcone_requirement(),
                )
            )
    else:
        report["unchanged"].append("pyproject.toml")
        if "lightcone-cli" not in pyproject_path.read_text():
            report["warnings"].append(
                "pyproject.toml does not depend on lightcone-cli — the "
                "engine should live inside the experiment's lock: "
                "`uv add lightcone-cli`."
            )

    from lightcone.engine.image.constants import DEFAULT_PYTHON

    _converge_file(".python-version", directory / ".python-version", f"{DEFAULT_PYTHON}\n")

    _converge_file(
        ".gitignore",
        directory / ".gitignore",
        _GITIGNORE_BASE + _GITIGNORE_APPEND,
        repair=_repair_gitignore,
    )

    _converge_file("AGENTS.md", directory / "AGENTS.md", _AGENTS_MD, repair=_repair_agents)

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
            project_cfg: dict[str, object] = {}
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
    # must come from `lc materialize`, not be written by hand.
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

    # Lock, then converge the environment. Failures surface — a silent
    # broken lock would fail every later verb more confusingly.
    def _lock() -> None:
        proc = _run_uv(["lock", "--project", str(directory)], cwd=directory)
        if proc.returncode != 0:
            raise click.ClickException(
                f"`uv lock` failed:\n{proc.stderr.strip()}"
            )

    _converge("uv.lock", (directory / "uv.lock").exists(), _lock)

    if not no_sync:
        def _sync() -> None:
            proc = _run_uv(
                [
                    "sync", "--locked", "--exact", "--compile-bytecode",
                    "--project", str(directory),
                ],
                cwd=directory,
            )
            if proc.returncode != 0:
                raise click.ClickException(
                    f"`uv sync` failed:\n{proc.stderr.strip()}"
                )

        _converge(".venv", (directory / ".venv").exists(), _sync)

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
                "  • Add analysis dependencies with [cyan]uv add[/cyan] "
                "(e.g. [cyan]uv add numpy astropy[/cyan])"
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


def _project_name(directory: Path) -> str:
    """PEP 503-ish project name derived from the directory name."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", directory.name).strip("-._").lower()
    return name or "analysis"


def _lightcone_requirement() -> str:
    """The lightcone-cli requirement pinned into the project scaffold.

    The engine lives *inside the experiment's lock* — pinned to the
    version running ``lc init`` so driver and project stay in lockstep;
    dev builds fall back to unpinned (their version isn't published).
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        v = version("lightcone-cli")
    except PackageNotFoundError:
        v = ""
    return f"lightcone-cli=={v}" if v and "dev" not in v else "lightcone-cli"


_PYPROJECT_TEMPLATE = """\
[project]
name = "{name}"
version = "0.1.0"
requires-python = ">=3.12"
# Analysis dependencies: add with `uv add <package>` — never install
# into another environment by hand. The engine (lightcone-cli) is a
# normal locked dependency: the experiment pins its own execution layer.
dependencies = [
    "{lc_requirement}",
]

[tool.uv]
required-version = ">=0.12"
"""


def _repair_gitignore(text: str) -> str | None:
    """Append the managed block once to a user-owned .gitignore.

    Keyed on the block's ``# lightcone-cli`` marker so re-runs never
    duplicate it.
    """
    if "# lightcone-cli" not in text:
        return text + _GITIGNORE_APPEND
    return None


def _repair_agents(text: str) -> str | None:
    """Append the lightcone stanza once to a user-owned AGENTS.md."""
    if "<!-- lightcone-cli -->" not in text:
        return text + "\n" + _AGENTS_STANZA
    return None


# Written when the project has no .gitignore of its own; mirrors the base
# entries `astra init` would have written.
_GITIGNORE_BASE = """\
# ASTRA Analysis
__pycache__/
*.py[cod]
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
.venv/
.lightcone/Snakefile
.lightcone/snakefile-config.json
.lightcone/image/
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
recording exactly how it was produced: recipe, environment identity,
decisions, input hashes, the output's content hash, and the sandbox
enforcement it ran under.

- Produce or refresh outputs with `lc materialize` — never write files
  here by hand. Hand-placed or edited files fail `lc verify` (the
  content hash won't match, and a missing manifest forces a re-run).
- `lc status` shows what is materialized, stale, or missing.
- Everything in this directory except this README is git-ignored;
  outputs are reproducible from `astra.yaml`, not versioned.
"""


_AGENTS_STANZA = """\
<!-- lightcone-cli -->
## Working in this lightcone project

- The environment is `pyproject.toml` + `uv.lock` (+ `.python-version`).
  A `ModuleNotFoundError` under `lc run`/`lc materialize` means: fix
  `pyproject.toml` with `uv add <package>` — never install into another
  environment by hand.
- `uv add` runs on the host, bare. In a containerized project
  (`[tool.lightcone.image]` declared), add `--no-sync`; never
  `lc run uv add`.
- The four verbs: `lc run <cmd>` probes (arbitrary commands in the
  recipe environment), `lc materialize` executes outputs, `lc status`
  reports, `lc verify` audits.
- Outputs are materialized, not run: `lc materialize <output_id>`,
  never `lc run <output_id>`.
"""

_AGENTS_MD = "# Agent notes\n\n" + _AGENTS_STANZA


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

TODO: present the outputs. Once `lc materialize` has produced results,
pull numbers in live, e.g.:

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
@click.option(
    "--require-sandbox",
    "require_sandbox",
    is_flag=False,
    flag_value="any",
    default=None,
    help=(
        "Refuse to run recipes without a sandbox mechanism; "
        "--require-sandbox=declared-fs additionally requires "
        "declared-file scoping."
    ),
)
@click.option(
    "--no-sandbox",
    is_flag=True,
    help="Run recipes without the sandbox (recorded as unsandboxed).",
)
def materialize(
    outputs: tuple[str, ...],
    universe: str | None,
    jobs: int | None,
    rerun_triggers: str,
    force: bool,
    verbose: bool,
    require_sandbox: str | None,
    no_sandbox: bool,
) -> None:
    """Materialize outputs declared in astra.yaml.

    Dispatches through a run-scoped Dask ``LocalCluster``.
    """
    from lightcone.engine.dask_cluster import cluster_for_run
    from lightcone.engine.environment import scan_lock
    from lightcone.engine.scratch import (
        RunLockBusyError,
        acquire_run_lock,
        ensure_snakemake_symlink,
        prepare_run_dirs,
        resolve_scratch_root,
    )
    from lightcone.engine.snakefile import discover_universes, generate
    from lightcone.engine.status import env_blast_radius

    project = _project_root()
    env = _load_env(project)
    _refuse_containerized_interim(env.mode)
    universes = [universe] if universe else discover_universes(project)

    # Blast radius: surfaced before anything runs, so an environment
    # edit's cost is visible at decision time.
    if (n_stale := env_blast_radius(project, universes=universes, env=env)) > 0:
        console.print(
            f"[yellow]environment changed:[/yellow] {n_stale} materialized "
            "output(s) are now stale"
        )
    if groups := scan_lock(project).non_default_groups:
        console.print(
            f"[dim]note: non-default dependency group(s) "
            f"{', '.join(groups)} are outside lc's guarantees[/dim]"
        )

    # Resolve scratch and prepare per-run directories before anything
    # else. Snakemake's ``.snakemake/`` is redirected via symlink so its
    # workflow lock and metadata land under the scratch root; dask spill
    # and the run lock live alongside it.
    rundirs = prepare_run_dirs(project)
    ensure_snakemake_symlink(project, rundirs.snakemake_state)
    if verbose:
        console.print(f"[dim]Scratch root:[/dim] {resolve_scratch_root(project)}")

    snakefile_path, _cfg_path = generate(project, universes=universes, env=env)

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
    # the project. Concurrent ``lc materialize`` on the same project
    # bails cleanly rather than corrupting Snakemake state.
    try:
        run_lock_cm = acquire_run_lock(rundirs)
        run_lock_cm.__enter__()
    except RunLockBusyError as e:
        raise click.ClickException(str(e))

    from lightcone.engine.runner import NO_SANDBOX_ENV, REQUIRE_SANDBOX_ENV

    with cluster_for_run(
        verbose=verbose,
        local_directory=str(rundirs.dask_local),
        max_workers=int(n),
    ) as cluster_env:
        env_vars = {**os.environ, **cluster_env}
        # Per-run sandbox flags travel to workers via env, not cfg — a
        # run flag must never perturb the content-addressed job identity.
        if no_sandbox:
            env_vars[NO_SANDBOX_ENV] = "1"
        if require_sandbox:
            env_vars[REQUIRE_SANDBOX_ENV] = require_sandbox
        if verbose:
            console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
        sys.exit(
            _run_snakemake(
                cmd, env=env_vars, scratch_root=rundirs.root, verbose=verbose
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
    """Build the snakemake argv list for ``lc materialize``.

    ``--rerun-triggers`` uses ``nargs=+`` in snakemake's argparse, so without
    an explicit ``--`` separator it greedily consumes the first positional
    target path as an extra trigger value, causing an "invalid choice" error.

    ``--shared-fs-usage`` lists everything *except*
    ``software-deployment``: with it included (snakemake's default),
    spawned job commands embed the *driver's* ``sys.executable``;
    without it, workers invoke plain ``python`` from their own
    environment — one invocation shape everywhere.
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
@click.option(
    "--no-sandbox",
    is_flag=True,
    help="Run the probe without the sandbox (recorded as unsandboxed).",
)
@click.option(
    "--sandbox-debug",
    is_flag=True,
    help="Open a shell inside the sandbox to diagnose denials.",
)
@click.argument("cmd", nargs=-1, type=click.UNPROCESSED)
def run(no_sandbox: bool, sandbox_debug: bool, cmd: tuple[str, ...]) -> None:
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

    env = _load_env(project)
    _refuse_containerized_interim(env.mode)

    if not cmd:
        note = " (sandboxed)" if not no_sandbox else ""
        console.print(
            f"[dim]opening a shell inside the recipe environment{note}[/dim]"
        )
        cmd = (os.environ.get("SHELL") or "bash",)

    if no_sandbox:
        proc = subprocess.run(
            [
                "uv", "run", "--locked", "--exact",
                "--project", str(project), "--", *cmd,
            ],
            cwd=project,
        )
        sys.exit(proc.returncode)

    # Sandboxed probe: byte-for-byte the recipe boundary — read scope is
    # the project plus the union of declared external inputs, write
    # scope is the tmp scope only (never in-tree). uv stays trusted
    # plumbing outside the boundary; the shim restricts, then execs CMD.
    import shutil as _shutil

    from lightcone.engine.boundary import ExecScope
    from lightcone.engine.sandbox.policy import build_policy
    from lightcone.engine.sandbox.probe import probe as _capability_probe
    from lightcone.engine.sandbox.wrap import wrap_argv

    if sandbox_debug:
        console.print("[dim]opening a shell inside the sandbox[/dim]")
        cmd = (os.environ.get("SHELL") or "bash",)

    scope = ExecScope(
        project_root=project,
        output_dir=None,
        read_paths=_declared_external_inputs(project),
    )
    capability = _capability_probe()
    policy = build_policy(scope, env_prefix=project / ".venv")
    wrapped = wrap_argv(
        tuple(cmd),
        policy,
        capability,
        interpreter=(
            "uv", "run", "--locked", "--exact",
            "--project", str(project), "--", "python",
        ),
    )
    try:
        proc = subprocess.run(
            list(wrapped.argv),
            cwd=project,
            env={**os.environ, **wrapped.env},
            pass_fds=wrapped.pass_fds,
        )
    finally:
        for fd in wrapped.close_after_spawn:
            try:
                os.close(fd)
            except OSError:
                pass
        _shutil.rmtree(policy.tmp_home, ignore_errors=True)
    sys.exit(proc.returncode)


def _declared_external_inputs(project: Path) -> tuple[Path, ...]:
    """Union of resolved external input paths across the analysis tree —
    a probe has no output, so its read allowlist is every declared
    input (in-tree ones are covered by the project grant)."""
    from astra.helpers import load_yaml, resolve_analysis_tree

    from lightcone.engine.tree import collect_tree_inputs

    spec = resolve_analysis_tree(load_yaml(project / "astra.yaml"), project)
    paths: list[Path] = []
    for inp_def in collect_tree_inputs(spec).values():
        source = inp_def.get("source")
        if not source or not isinstance(source, str):
            continue
        p = Path(source)
        if not p.is_absolute():
            p = project / p
        if p.exists():
            paths.append(p.resolve())
    return tuple(paths)


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
    from lightcone.engine.status import env_blast_radius, get_output_status

    project = _project_root()
    env = _load_env(project)
    universes = [universe] if universe else discover_universes(project)

    if as_json:
        payload = {
            "mode": str(env.mode),
            "env_version": env.env_version,
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
                        for s in get_output_status(project, universe_id=u, env=env)
                    ],
                }
                for u in universes
            ],
        }
        click.echo(json.dumps(payload, indent=2))
        return

    _print_status_header(env)

    for u in universes:
        console.print(f"\n[bold]Universe[/bold] [cyan]{u}[/cyan]")
        for s in get_output_status(project, universe_id=u, env=env):
            label = _status_label(s.status)
            scope = f"[dim]{s.analysis_id}.[/dim]" if s.analysis_id else ""
            console.print(f"  {label}  {scope}{s.output_id}")

    if (n_stale := env_blast_radius(project, universes=universes, env=env)) > 0:
        console.print(
            f"\n[yellow]environment changed:[/yellow] {n_stale} materialized "
            "output(s) are now stale"
        )


def _print_status_header(env) -> None:  # type: ignore[no-untyped-def]
    """The three header lines: mode / image / sandbox.

    Offline and local-only by invariant — reads pyproject + the local
    image record, never the network.
    """
    from lightcone.engine.boundary import get_boundary
    from lightcone.engine.environment import Mode

    if env.mode is Mode.CONTAINERIZED:
        from lightcone.engine.image import image_status

        n = len(env.image.system_packages) if env.image else 0
        mode_line = f"containerized ({n} system package{'s' if n != 1 else ''})"
        try:
            info = image_status(env.root, env)
            if info.built:
                image_line = f"{info.tag} — built [dim]({info.image_id})[/dim]"
            else:
                image_line = f"{info.tag} — needs build (run `lc build`)"
        except ImageError as e:
            image_line = f"[yellow]{e}[/yellow]"
    else:
        mode_line = "direct"
        image_line = "—"
    console.print(f"[dim]mode:[/dim]    {mode_line}")
    console.print(f"[dim]image:[/dim]   {image_line}")
    console.print(f"[dim]sandbox:[/dim] {get_boundary().describe_host()}")


_STATUS_STYLES = {
    "ok": "[green]✓ ok[/green]    ",
    "stale": "[yellow]✸ stale[/yellow] ",
    "missing": "[red]✗ miss[/red]  ",
    "alias": "[dim]→ alias[/dim] ",
    "pre_migration": "[magenta]⧗ pre-v2[/magenta]",
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
            notes = f"  [dim]({', '.join(r.notes)})[/dim]" if r.notes else ""
            if r.passed:
                console.print(f"  [green]✓ ok[/green]    {r.output_id}{notes}")
            else:
                failed += 1
                console.print(
                    f"  [red]✗ {r.failure}[/red]  {r.output_id}  "
                    f"[dim]{r.detail}[/dim]{notes}"
                )

    if failed:
        console.print(f"\n[bold red]{failed} integrity failure(s).[/bold red]")
        sys.exit(1)
    console.print("\n[green]All outputs verified.[/green]")


# =============================================================================
# lc build
# =============================================================================


@main.command()
@click.option("--force", is_flag=True, help="Rebuild the image even if cached")
def build(force: bool) -> None:
    """Build the project's environment image (containerized mode).

    The image is generated from the locked environment — never
    user-authored: pyproject.toml + uv.lock + [tool.lightcone.image]
    render to a Containerfile, built with podman under a
    content-addressed tag. Direct-mode projects have no image.
    """
    from lightcone.engine.environment import Mode
    from lightcone.engine.image import ensure_image

    project = _project_root()
    env = _load_env(project)

    if env.mode is Mode.DIRECT:
        console.print(
            "direct mode — no image to build; declare "
            r"[cyan]\[tool.lightcone.image][/cyan] in pyproject.toml to "
            "containerize."
        )
        return

    record = ensure_image(
        project,
        env,
        force=force,
        on_progress=lambda msg: console.print(f"[cyan]{msg}[/cyan]"),
    )
    console.print(
        f"[green]✓[/green] {record.tag} — built "
        f"[dim](image id {record.image_id[:19]}…, {record.platform})[/dim]"
    )


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
