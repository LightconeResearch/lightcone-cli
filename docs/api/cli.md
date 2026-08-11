# lightcone.cli.commands

The Click surface. Defined in `src/lightcone/cli/commands.py`. Six
public commands: `init`, `run`, `status`, `verify`, `build`, `export`
(plus the `eval` group, registered when the `eval` extra is
installed).

The user-facing reference is in [CLI Overview](../cli/index.md). This
page is a tour of the module internals.

## Entry point

```python
@click.group()
@click.version_option(package_name="lightcone-cli")
@click.pass_context
def main(ctx: click.Context) -> None:
    ctx.ensure_object(dict)
    _ensure_global_config()   # auto-create ~/.lightcone/config.yaml with defaults
```

`main` is exposed as `lightcone.cli.main` (re-exported from
`lightcone.cli.__init__`) and is the entry point declared in
`pyproject.toml::project.scripts`:

```toml
[project.scripts]
lc = "lightcone.cli:main"
```

## Helpers

### `_config_path() → Path`

Returns `~/.lightcone/config.yaml`. Used by `_ensure_global_config()`,
which the `main` group calls to create the file with defaults
(`container: {runtime: auto}`) on first invocation.

### `_project_root(start: Path | None = None) → Path`

Walks up from `start` (or `cwd`) looking for `astra.yaml`. Raises
`click.ClickException` if none found. Used by `run`, `status`, `verify`,
`build`.

### `_target_for(project: Path, output_id: str, universe: str) → str`

Translate an `output_id` (or qualified `<analysis_id>.<output_id>`) into
the Snakemake target path that materializes it — specifically the
manifest file `results/<universe>/<output_id>/.lightcone-manifest.json`.
Raises `click.ClickException` if the id is unknown or ambiguous.

### `_run_snakemake(cmd, *, env, scratch_root, verbose)`

Spawn `snakemake` and forward the run's narrative output: lines the
executor plugin prefixes with the sentinel
(`lightcone.engine.runner.SENTINEL`) stream to the terminal with the
prefix stripped; everything else (DAG chatter, job stats) is dropped
unless `verbose`. stderr is tailed into a bounded ring buffer and, on
failure, dumped to `snakemake-stderr-<pid>.log` under the scratch
root. Returns the exit code.

### `_status_label(s: str) → str`

Map a status literal to the Rich-formatted display label:

| Status | Display |
|--------|---------|
| `ok` | `[green]✓ ok[/green]` |
| `stale` | `[yellow]✸ stale[/yellow]` |
| `missing` | `[red]✗ miss[/red]` |
| `alias` | `[dim]→ alias[/dim]` |

## Boilerplate text

`_CONTAINERFILE_TEMPLATE`, `_REQUIREMENTS`, `_GITIGNORE_BASE`,
`_GITIGNORE_APPEND`, `_MYST_YML`, and `_INDEX_MD_BODY` are multi-line
strings written at `lc init` time (the spec boilerplate itself comes
from astra's boilerplate helper). Edit them to change what new
projects look like.

`init` is a convergence loop, not a one-shot scaffolder: each managed
item is created if missing, offered to a `repair(text) -> str | None`
hook otherwise (the `_migrate_legacy_containerfile`,
`_strip_lightcone_requirement`, and `_repair_gitignore` functions —
conservative migrations of content lightcone itself wrote), and left
alone when the hook returns `None`. `--check` computes the same report
without writing (exit 1 when not converged); `--json` prints it as
`{converged, created, repaired, unchanged, warnings}`. Warnings carry
problems init can see but must not fix (e.g. a directory `COPY` in a
hand-edited Containerfile, detected via
`lightcone.engine.container.directory_copy_sources`).
