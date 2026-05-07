# Command Schematics (removed)

The interactive HTML schematics page was retired along with the legacy
Dagster-era CLI surface (`lc dev`, `lc target`, `lc update`). Several of the
commands it documented no longer exist.

For an up-to-date map of the toolchain, see:

- [Architecture](../architecture.md) — execution and integrity flow
- [CLI Overview](index.md) — every command currently shipped
- [Skills Overview](../skills/index.md) — the Claude Code surface

If you want a graphical view of the analysis DAG for a specific project,
generate it from the Snakefile that `lc run` produces:

```bash
lc run --dry-run                                   # produces .lightcone/Snakefile
snakemake -s .lightcone/Snakefile --dag | dot -Tsvg > dag.svg
```
