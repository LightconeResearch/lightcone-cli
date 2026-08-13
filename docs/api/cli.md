# lightcone.cli.commands

The Click surface. Defined in `src/lightcone/cli/commands.py`. Public
commands include `init`, `run`, `status`, `cancel`, `verify`, `build`, and
`export`.

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

## Helpers

### `_config_path() → Path`

Returns `~/.lightcone/config.yaml`. The main group creates it on first use
with container and SLURM defaults.

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
| `queued` | `[blue]◷ queued[/blue]` |
| `running` | `[cyan]▶ running[/cyan]` |
| `failed` | `[red]✗ failed[/red]` |

## Boilerplate text

`_BOILERPLATE_ASTRA`, `_GITIGNORE`, and `_PROJECT_CLAUDE_MD` are
multi-line strings written at `lc init` time. Edit them to change what
new projects look like.

## Plugin install

`_install_claude_plugin(project_dir, plugin_source, permissions)` copies
the bundled plugin into `project_dir/.claude/` (`skills`, `agents`,
`scripts`, `guides`, `templates`) and writes `.claude/settings.json`
from the chosen permission tier. Existing subdirectories are removed
before copying.
