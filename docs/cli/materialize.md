# lc materialize

```text
lc materialize [OUTPUTS…] [-u UNIVERSE] [-j N] [-f] [-v]
               [--rerun-triggers LIST] [--require-sandbox[=declared-fs]]
               [--no-sandbox]
```

Materialize the outputs declared in `astra.yaml` — all of them by
default, or the named ones (bare `output_id`, or
`analysis_id.output_id` to disambiguate), across all universes or the
one named with `-u`.

## What happens

1. The launcher converges the environment: direct mode syncs `.venv`
   from the lock; containerized mode resolves (building if necessary,
   with an announcement) the digest-pinned environment image and
   re-enters `lc` inside it.
2. If the environment changed since outputs were materialized, the
   blast radius is printed up front: `environment changed: N
   materialized output(s) are now stale`.
3. A Snakefile is generated from `astra.yaml` (never edit it — it is
   regenerated every run) and Snakemake dispatches each rule as a Dask
   task on a run-scoped local cluster.
4. Each rule runs the worker sequence: an environment gate (has the
   lock changed since the run started?), an environment check
   (`uv sync --check` / image identity assert), the recipe inside the
   sandbox with the offline overlay, a second gate, and only then the
   manifest write. A failing recipe writes no manifest.

## Sandbox flags

| Flag | |
|---|---|
| `--require-sandbox` | refuse to run any recipe without an enforcement mechanism |
| `--require-sandbox=declared-fs` | additionally require declared-file scoping |
| `--no-sandbox` | run without enforcement — recorded honestly as `{mechanism: none, fs: open}` |

## Scheduling flags

| Flag | |
|---|---|
| `-j / --jobs N` | parallel bound (default: CPU count) |
| `-f / --force` | re-run the named outputs (or everything, when none named) |
| `--rerun-triggers` | Snakemake rerun triggers (default `code,input,mtime,params`) |
| `-v / --verbose` | forward the full executor output |

## Provenance

Every produced output directory gains `.lightcone-manifest.json` —
identity (`code_version`, `env_version`, `data_version`), the chain
(`input_versions`), git state, runtime attestation, the image identity
when a container ran, and the `hermeticity` record of the enforcement
that actually applied. Concurrent `lc materialize` invocations on one
project are excluded by a run lock.
