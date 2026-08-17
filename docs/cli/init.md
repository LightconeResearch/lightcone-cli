# lc init

```text
lc init [DIRECTORY] [--no-git] [--no-sync] [--check] [--json] [--scratch EXPR]
```

Idempotently converge DIRECTORY (default `.`) into an ASTRA project:
creates whatever is missing, repairs the pieces lightcone manages, and
never overwrites files you own. A directory that already holds an
`astra.yaml` is adopted, not rejected.

## What it creates

- `astra.yaml` + `universes/baseline.yaml` + `src/` — the ASTRA
  boilerplate (analysis structure only; any legacy `container:` line is
  stripped — the environment does not live in the spec).
- `pyproject.toml` — a *virtual* uv project (no `[build-system]`) with
  `lightcone-cli` as an ordinary locked dependency: the engine lives
  inside the experiment's lock.
- `.python-version` — an exact interpreter patch pin.
- `uv.lock` (via `uv lock`) and `.venv` (via
  `uv sync --locked --exact --compile-bytecode`; skip with `--no-sync`).
- `AGENTS.md` — the boundary rules for AI agents, appended once to an
  existing file.
- `.gitignore`, `.lightcone/` project state, `results/` + README, and a
  template MyST report (`myst.yml` + `index.md`).

## Refusals

An authored root `Containerfile` is refused with instructions: images
are generated from the lock — delete or rename the file, then re-run.
The file operation is the consent; there is no override flag.

## Options

| Flag | |
|---|---|
| `--check` | report drift without writing anything; exit 1 if unconverged |
| `--json` | machine-readable convergence report |
| `--no-sync` | lock but don't materialize `.venv` |
| `--no-git` | skip `git init` |
| `--scratch EXPR` | pin the scratch root in `.lightcone/lightcone.yaml` (shell expressions kept verbatim) |
