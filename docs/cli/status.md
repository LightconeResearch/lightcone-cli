# lc status

Show materialisation status of all outputs.

## Synopsis

```
lc status [OPTIONS]
```

## Description

Displays the materialisation state of every output across all universes. The display format depends on whether the project has sub-analyses:

- **Flat project**: a table with outputs as rows and universes as columns.
- **Project with sub-analyses**: a Rich tree grouped by sub-analysis.

At the bottom, a summary line shows recipe coverage and container status.

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--universe`, `-u` | all | Show status for a specific universe only |

## Status values

| Value | Meaning |
|-------|---------|
| `ok` | Materialised — Dagster event log confirms a successful run |
| `pending` | Has a recipe block but has never been materialised |
| `no recipe` | Declared as an output but has no `recipe` block |
| `alias` | Root-level output pointing to a sub-analysis output via `from:` |

## Examples

```bash
lc status
lc status --universe experiment1
```

## Sample output (flat project)

```
            my-analysis -- Output Status
┌──────────────────┬──────────┬─────────────┐
│ Output           │ baseline │ experiment1 │
├──────────────────┼──────────┼─────────────┤
│ accuracy         │ ok       │ pending     │
│ visualisation    │ pending  │ pending     │
│ conclusion       │ no recipe│ no recipe   │
└──────────────────┴──────────┴─────────────┘

  Recipes: 2/3 outputs integrated
  Materialized: 1/4 runs
  Container: build Containerfile  lc-myanalysis-a1b2c3 (built)
```

## Implementation notes

Status is read from the Dagster SQLite event log at `results/.dagster/`. If the file is absent or corrupted, all outputs report `pending` rather than erroring.

The `get_output_status()` function batch-queries `DagsterInstance.get_latest_materialization_events()` to avoid N+1 queries.
