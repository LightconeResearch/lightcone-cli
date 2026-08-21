# lightcone.cli.commands

The Click surface. Defined in `src/lightcone/cli/commands.py`. Five
commands plus one group: `init`, `run`, `status`, `verify`, `build`, and
`export` (with `export wrroc` under it). `lc eval` is registered at
import time from `lightcone.eval.cli` and silently skipped when the
optional `eval` extra isn't installed.

The user-facing reference is in [CLI Overview](../cli/index.md). This
page is a tour of the module internals.

## Entry point

```python
@click.group()
@click.version_option(package_name="lightcone-cli")
@click.pass_context
def main(ctx: click.Context) -> None:
    ctx.ensure_object(dict)
    _ensure_global_config()
```

`_ensure_global_config()` runs on **every** invocation (no
`invoked_subcommand` gate): if `~/.lightcone/config.yaml` is missing it
is created with `container: {runtime: auto}`. Nothing errors out on a
first run.

`main` is exposed as `lightcone.cli.main` (re-exported from
`lightcone.cli.__init__`) and is the entry point declared in
`pyproject.toml::project.scripts`:

```toml
[project.scripts]
lc = "lightcone.cli:main"
```

## `PERMISSION_TIERS`

```python
PERMISSION_TIERS: dict[str, dict[str, list[str]]] = { "yolo": {...}, "recommended": {...}, "minimal": {...} }
```

Used by `lc init --permissions` to populate `.claude/settings.json`.
The constant lives at module top so tests and external tools can read
it directly. To add a new tier, edit this dict and update the
`click.Choice` on `lc init`.

`recommended` splits scratch paths into an `ask` list (prompt before
writing — `//scratch/**`, `//pscratch/**`) and a `deny` list for things
that are never acceptable (`~/.ssh`, `sudo`, `rm -rf`, `git push`).

## Helpers

### `_config_path() → Path`

Returns `~/.lightcone/config.yaml`. Used by `_ensure_global_config()`.

### `_project_root(start: Path | None = None) → Path`

Walks up from `start` (or `cwd`) looking for `astra.yaml`. Raises
`click.ClickException` if none found. Used by `run`, `status`, `verify`,
`build`, and `export wrroc`.

### `_target_for(project: Path, output_id: str, universe: str) → str`

Translate an `output_id` (or qualified `<analysis_id>.<output_id>`) into
the Snakemake target path that materializes it — specifically the
manifest file `results/<universe>/<output_id>/.lightcone-manifest.json`.
Raises `click.ClickException` if the id is unknown or ambiguous.

### `_abort_on_perlmutter_login() → None`

Pre-flight on `lc run`. Refuses to run when `NERSC_HOST=perlmutter` and
no `SLURM_JOB_ID` is set — that conjunction unambiguously marks a login
node. Bypassed by `DASK_SCHEDULER_ADDRESS` (matching the external-
scheduler branch of `cluster_for_run`) or by setting
`LIGHTCONE_ALLOW_LOGIN_NODE`.

### `_build_snakemake_cmd(*, snakefile_path, project, n, rerun_triggers, targets, force, has_outputs) → list[str]`

Builds the `snakemake` argv. Two non-obvious details it encodes:

- `--rerun-triggers` uses `nargs=+` in snakemake's argparse, so a `--`
  separator is emitted before positional targets or the first target
  gets eaten as a trigger value.
- `--shared-fs-usage` lists everything *except* `software-deployment`.
  With snakemake's default, spawned job commands embed the driver's
  `sys.executable` — a path that doesn't exist inside a Dask Gateway
  worker image. Without it, workers invoke plain `python` from their own
  environment, which is equally correct on every other backend.

`--force` is used with explicit targets and `--forceall` without (the
`rule all` target has no recipe of its own).

### `_run_snakemake(cmd, *, env, scratch_root, verbose) → int`

Spawn `snakemake` and forward the run's narrative: the executor plugin
prints each finished rule's block prefixed with
`lightcone.engine.runner.SENTINEL`, and those lines — prefix stripped —
go to the terminal while everything else (DAG chatter, job stats) is
dropped. `--verbose` forwards the noise too.

stderr is tailed into a bounded ring buffer (400 lines) on a daemon
thread. On a non-zero exit it is written to
`<scratch_root>/snakemake-stderr-<pid>.log` and the path is printed —
so a crash leaves a real log behind without polluting a successful run.
Verbose mode passes stderr straight through instead.

### `_ensure_images(project, *, runtime, force=False) → list[str]`

Build or pull every container image referenced in `astra.yaml`, and
return the distinct resolved images in declaration order. Idempotent —
present images are skipped unless `force`. No-op returning `[]` when
`runtime == "none"`.

Three paths per spec:

- **Prebuilt image** (`python:3.12-slim`, `ghcr.io/...`) → `pull_image`,
  so `lc run` can pass `--pull=never`. Skipped entirely on the
  `kubernetes` runtime: worker pods pull from the registry themselves.
- **Containerfile on `kubernetes`** → `_cloudbuild_image()`.
- **Containerfile anywhere else** → `compute_image_tag` +
  `build_image`.

Used by `lc build` (which exposes `--force`) and as a `lc run`
pre-flight, so the first run after editing a Containerfile doesn't fail
mid-DAG on a missing image.

### `_cloudbuild_image(project, spec_str, project_name, force) → str`

Ensure one Containerfile's image through
[`engine.cloudbuild.ensure_image`](cloudbuild.md), rendering its
`on_progress(phase, detail)` callbacks into a live Rich status line.
Raises a `click.ClickException` with hub-admin guidance when
`cloudbuild_available()` is False (the `kubernetes` runtime has no local
OCI CLI and no other build backend to fall back on).

### `_status_label(s: str) → str`

Map a status literal to the Rich-formatted display label:

| Status | Display |
|--------|---------|
| `ok` | `[green]✓ ok[/green]` |
| `stale` | `[yellow]✸ stale[/yellow]` |
| `missing` | `[red]✗ miss[/red]` |
| `alias` | `[dim]→ alias[/dim]` |

## What `lc run` does, in order

1. `_abort_on_perlmutter_login()`.
2. [`scratch.prepare_run_dirs`](scratch.md) + `ensure_snakemake_symlink`
   — resolve scratch and repoint `<project>/.snakemake`.
3. `container.load_runtime()`, then `_ensure_images()`.
4. `snakefile.generate()`.
5. If [`dask_cluster.gateway_branch_active()`](dask_cluster.md): reject
   a spec resolving to more than one distinct image (the worker pod is
   the container for every rule, so only one image can be honoured), and
   pass the single image on as `worker_image`.
6. Warn when `runtime: auto` silently fell back to `none` while the spec
   declares containers — the manifest's `container_image` would
   misrepresent what executed.
7. `scratch.acquire_run_lock()`, then `cluster_for_run(...)`, whose
   yielded env overlay is merged into the child snakemake's environment.

## Scaffold constants

`lc init` writes these module-level strings:

| Constant | Written to |
|----------|------------|
| `_CONTAINERFILE` | `Containerfile` (`python:3.12-slim`, install requirements, `COPY . .`) |
| `_REQUIREMENTS` | `requirements.txt`, plus `_lightcone_requirement()` |
| `_GITIGNORE_APPEND` | appended to the `.gitignore` `astra init` wrote |
| `_MYST_YML`, `_INDEX_MD_BODY` | `myst.yml` + `index.md`, via `_create_report_template()` |
| `_PROJECT_CLAUDE_MD` | the project's root `CLAUDE.md` |

`astra.yaml`, `universes/baseline.yaml`, the base `.gitignore` and `src/`
come from `astra init`, which `lc init` calls first; the only edit made
to the generated spec is swapping `container: python:3.12-slim` for
`container: Containerfile`.

### `_lightcone_requirement() → str`

The requirements lines that pin `lightcone-cli` into the project image.
The image must be able to execute rules on any backend — including *as*
a Dask Gateway worker pod, where the dask worker and the child snakemake
run inside it — and `lightcone-cli` carries that whole stack. The pin
mirrors the version running `lc init`; dev versions fall back to
unpinned.

## Plugin install

`_install_claude_plugin(project_dir, plugin_source, permissions)` copies
the bundled plugin into `project_dir/.claude/` (`skills`, `agents`,
`scripts`, `guides`, `templates`) and writes `.claude/settings.json`
from the chosen permission tier plus the plugin's own `hooks.json`.
Existing subdirectories are removed before copying.
