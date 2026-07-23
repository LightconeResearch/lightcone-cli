# lightcone.cli.commands

The Click surface. Defined in `src/lightcone/cli/commands.py`. Six
public commands: `init`, `run`, `status`, `verify`, `build`, `setup`.

The user-facing reference is in [CLI Overview](../cli/index.md). This
page is a tour of the module internals.

## Entry point

```python
@click.group()
@click.version_option(package_name="lightcone-cli")
@click.pass_context
def main(ctx: click.Context) -> None:
    ctx.ensure_object(dict)
    if ctx.invoked_subcommand in ("setup", "init", "eval"):
        return
    if not _config_path().exists():
        # print friendly error, sys.exit(1)
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

Returns `~/.lightcone/config.yaml`. Used by the `main` group's
auto-init check and by `setup`.

### `_project_root(start: Path | None = None) → Path`

Walks up from `start` (or `cwd`) looking for `astra.yaml`. Raises
`click.ClickException` if none found. Used by `run`, `status`, `verify`,
`build`.

### `_target_for(project: Path, output_id: str, universe: str) → str`

Translate an `output_id` (or qualified `<analysis_id>.<output_id>`) into
the Snakemake target path that materializes it — specifically the
manifest file `results/<universe>/<output_id>/.lightcone-manifest.json`.
Raises `click.ClickException` if the id is unknown or ambiguous.

### `_run_filtered(cmd, *, env)`

Spawn `snakemake`, line-filter its stdout/stderr to suppress executor
banner chatter, and return the exit code. The recipe's own output
streams through untouched, as do unfamiliar diagnostic lines.

### `_status_label(s: str) → str`

Map a status literal to the Rich-formatted display label:

| Status | Display |
|--------|---------|
| `ok` | `[green]✓ ok[/green]` |
| `stale` | `[yellow]✸ stale[/yellow]` |
| `missing` | `[red]✗ miss[/red]` |
| `alias` | `[dim]→ alias[/dim]` |

## Boilerplate text

`_BOILERPLATE_ASTRA`, `_GITIGNORE`, and `_PROJECT_CLAUDE_MD` are
multi-line strings written at `lc init` time. Edit them to change what
new projects look like.

## Claude settings

`_merge_claude_settings(project_dir)` non-destructively updates
`project_dir/.claude/settings.json`. It adds `enabledPlugins`, which activates
the `lightcone` plugin in this project, while preserving any other content
already in the file. It writes no marketplace source — `lc init` registers the
`agent-skills` marketplace globally with `claude plugin marketplace add`. It
holds no permission policy — permissions belong to the harness. No skills,
hooks, or agents are copied — the plugin carries them.
