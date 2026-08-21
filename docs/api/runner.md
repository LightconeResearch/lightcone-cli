# lightcone.engine.runner

The per-rule execution helper invoked from the generated Snakefile. Each
rule's `run:` block boils down to one call to `run_rule()`.

Source: `src/lightcone/engine/runner.py`.

!!! note "Not the old pluggable runner"

    An earlier module of this name was a backend abstraction (`docker`,
    `venv`, `local`, `slurm`). That is gone: containers are resolved at
    Snakefile-generation time by [engine/container](container.md), and
    *where* a rule runs is decided by
    [engine/dask_cluster](dask_cluster.md). Today's `runner` is the thin
    body of a generated rule.

## `SENTINEL`

```python
SENTINEL = "__LCSTREAM__::"
```

Every line the runner emits carries this prefix. It is the entire
mechanism behind `lc run`'s clean narrative output: the dask executor's
`_run_shell` keeps sentinel-prefixed lines from the worker subprocess and
drops everything else — snakemake bootstrap, dask logs, stray prints —
so nothing has to be filtered against a moving target of upstream log
strings. `_run_snakemake` in the CLI strips the prefix on the way to the
terminal.

Chosen to be vanishingly unlikely in real recipe output: printable
ASCII, column-0 anchored, distinctive, and short enough that capture cost
stays negligible.

## `run_rule(*, rule_key, universe, output_dir, inputs, cfg) → None`

In order:

1. Emit a `▶ <rule_key> [<universe>]` header.
2. Run `cfg["shell_command"]` — already template-substituted and
   container-wrapped by [engine/snakefile](snakefile.md) — with stdout
   and stderr captured.
3. Re-emit the recipe's captured output, indented, stdout then stderr.
   (Snakemake's own capture interleaves the same way.)
4. On **non-zero exit**: emit a `✗` trailer carrying the exit code and
   raise `subprocess.CalledProcessError`, so Snakemake records the job as
   failed and halts the DAG. The manifest is **not** written — `lc
   verify` must never see a manifest pointing at incomplete data.
5. On success: [`write_manifest`](manifest.md), then
   [`validate_output`](validation.md), emitting any warnings as `⚠`
   lines.
6. Emit a `✓ <rule_key> [<universe>]   <duration>` trailer.

`inputs` is keyed by the *raw* declared input id (`sub.real`), not the
Snakemake-safe identifier (`sub__real`) — the raw ids are what
`write_manifest` records in `input_versions` and what `lc verify` walks.

## `_emit(line="")` (private)

Writes one sentinel-prefixed line to stdout and **flushes**. The flush
matters: the runner executes inside a child snakemake subprocess whose
stdout is captured by the worker's `_run_shell`, and without it Python
may block-buffer the recipe output until after the rule's trailer.

## Public surface

```python
__all__ = ["SENTINEL", "run_rule"]
```
