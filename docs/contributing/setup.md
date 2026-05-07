# Development Setup

You'll need:

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- [just](https://github.com/casey/just) — `brew install just` or `cargo install just`
- Git
- One of: docker, podman, podman-hpc (optional — only needed for
  container tests and for projects that declare `container:`)

## Clone & install

```bash
git clone https://github.com/LightconeResearch/lightcone-cli.git
cd lightcone-cli
just install        # uv sync --all-groups (dev + docs)
```

`just` (alone, with no recipe) lists everything available — the
recipes that follow are the ones you'll touch most.

## Running the test suite

```bash
just test               # uv run pytest
just test-cov           # with coverage report
```

The opt-in `slow` marker covers tests that spin up real subsystems
(local Dask cluster, etc.). They are excluded by default; run with
`uv run pytest -m slow` to include them.

## Linting & types

```bash
just lint               # ruff + mypy
just fix                # ruff --fix
just fmt                # ruff format
```

Ruff rules: `E, F, I, N, W, UP`. Line length: 100. Target: Python 3.11.
Mypy is strict, with `namespace_packages = true` and
`explicit_package_bases = true` (we ship a PEP 420 namespace package).

## Building the docs locally

```bash
just docs-serve         # syncs docs group + live preview at http://127.0.0.1:8000
just docs-strict        # build with --strict
just docs               # one-shot build into site/
```

The docs use [zensical](https://zensical.org). The nav lives in
`zensical.toml`.

## Building the wheel

```bash
just build              # uv build
just version            # current version (from git tags via hatch-vcs)
```

The plugin (`claude/lightcone/`) is force-included into the wheel:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/lightcone", "src/snakemake_executor_plugin_dask"]

[tool.hatch.build.targets.wheel.force-include]
"claude/lightcone" = "lightcone/cli/claude/lightcone"
```

That layout is what `lightcone.cli.plugin.get_plugin_source_dir()`
walks — it tries the bundled location first, then the dev location
relative to the repo root.

## Repo layout

```
src/lightcone/                       # main namespace (PEP 420; no __init__.py at the package root)
src/snakemake_executor_plugin_dask/  # Snakemake → Dask executor plugin
claude/lightcone/                    # Claude Code plugin (force-included into the wheel)
tests/                               # pytest tree, mirrors src/
evals/                               # eval task fixtures (tasks/snae/)
docs/                                # docs site
```

## Pre-commit checklist

Quick sequence before pushing a PR:

```bash
just lint               # ruff + mypy
just test               # full pytest run
just docs-strict        # docs still build cleanly
```

Each line maps to one CI check. CI runs them serially; running locally
catches everything before the PR machinery starts.
