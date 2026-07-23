# Developer corner

`lightcone-cli` is a thin shim over Snakemake that owns provenance. This guide
covers everything below the user surface: how the execution and integrity layers
work, what each engine module does, how the agent plugin is structured, and
how to get a working dev loop.

If you're looking for the user-facing docs, the
[user guide](user/index.md) is the other half of this site.

## What this covers

- [Architecture](architecture.md) — the three subsystems (Snakefile generation,
  manifest layer, cluster management) and the invariants that hold them together.
- [CLI Reference](cli/index.md) — every `lc` command: flags, options, and the
  exact Snakemake invocation each one triggers.
- [Python API](api/index.md) — the `lightcone.engine.*` modules: public
  signatures, common entry points, and module responsibilities.
- [Skills](skills/index.md) — the agent skills, which ship from the
  `agent-skills` marketplace, including the `from-paper` reproduction bundle.
- [HPC & SLURM](hpc/index.md) — how the Dask cluster manager adapts to local,
  SLURM, and external schedulers.
- [Contributing](contributing/setup.md) — clone, install, run the test suite,
  lint, and build the docs locally.

## Get started in three commands

!!! tip "Dev loop"

    ```bash
    git clone https://github.com/LightconeResearch/lightcone-cli.git
    cd lightcone-cli
    just install        # uv sync --all-groups
    just test           # pytest
    ```

    Run `just` with no arguments to see all available recipes.

## What lightcone-cli *owns*

The codebase is intentionally small. Snakemake handles DAG construction,
parallelism, cluster submission, staleness detection, locking, and log capture —
we do not replicate any of that. The parts that are ours:

- **Snakefile generator** — translates `astra.yaml` into `.lightcone/Snakefile`.
- **Manifest layer** — writes and verifies `.lightcone-manifest.json` per output.
- **Cluster manager** — picks local / SLURM / external Dask shape at runtime.

The agent skills, hooks, and the `lc-extractor` subagent are *not* ours to
bundle. They ship from the
[agent-skills](https://github.com/LightconeResearch/agent-skills) marketplace as
the `lightcone` plugin. `lc init` registers that marketplace in the project's
`.claude/settings.json`.
