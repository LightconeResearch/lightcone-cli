# Development Setup

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- [just](https://github.com/casey/just) (`brew install just` or `cargo install just`)
- Git
- Docker or Podman (optional, for container tests)

## Clone and install

```bash
git clone https://github.com/LightconeResearch/Prism.git
cd Prism
just install        # uv sync --all-groups (dev + docs)
```

Dependencies are declared in `pyproject.toml`:

| Group | Contents | Install |
|-------|----------|---------|
| *(main)* | `dagster`, `langfuse`, `click`, … | `uv sync` |
| `dev` | `pytest`, `ruff`, `mypy`, … | `uv sync --group dev` |
| `docs` | `mkdocs-material`, `mkdocstrings` | `uv sync --group docs` |
| `eval` *(optional-dep)* | `anthropic`, `daytona-sdk`, … | `uv sync --extra eval` |

## Running tests

```bash
pytest
```

Key test patterns:

- **CLI tests**: use `CliRunner().invoke(main, ["command", ...])` — check exit code, output, file side effects.
- **Asset tests**: call `build_asset_definitions(spec, runner=mock_runner)` — verify keys, deps, metadata.
- **Runner tests**: create runner with `tmp_path` as project root, call `execute()` — verify exit code and metadata.
- **Integration tests**: `test_integration.py` and `test_cli_run.py` cover end-to-end flows.

The `_fake_config` fixture monkeypatches `get_config_path()` to prevent the auto-setup wizard from firing during tests:

```python
@pytest.fixture(autouse=True)
def _fake_config(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text("default_target: local\n")
    monkeypatch.setattr("prism.dagster.targets.get_config_path", lambda: config)
```

## Linting and type checking

```bash
ruff check src/ tests/
mypy src/
```

Ruff rules: E, F, I, N, W, UP. Line length: 100. Target: Python 3.11.

## Package structure

```
src/prism/          # main package
claude/prism/       # plugin files (bundled via hatch force-include)
tests/              # mirrors src/ structure
evals/              # skill evaluation fixtures
```

## Building the wheel

```bash
just build   # uv build
just version # uv run hatch version
```

The `hatch-vcs` plugin derives the version from git tags. The `claude/prism/` directory is force-included in the wheel via `pyproject.toml`:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/prism"]

[tool.hatch.build.force-include]
"claude/prism" = "prism/claude/prism"
```

## Building the documentation

```bash
just docs-serve     # syncs docs group + live preview at http://127.0.0.1:8000
just docs-strict    # build with --strict (fails on warnings)
just docs-deploy    # push to GitHub Pages
```
