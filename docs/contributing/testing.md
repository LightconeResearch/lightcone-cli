# Testing

## Test layout

```
tests/
├── conftest.py               # shared fixtures (_fake_config, tmp project helpers)
├── test_cli.py               # CLI command tests
├── test_cli_run.py           # prism run integration tests
├── test_integration.py       # end-to-end tests
└── dagster/
    ├── test_assets.py        # build_asset_definitions tests
    ├── test_runner.py        # ASTRAContainerRunner tests
    ├── test_status.py        # status query tests
    └── test_targets.py       # target config tests
```

## Key fixtures

### `_fake_config` (autouse)

Prevents the auto-setup wizard from firing during CLI tests by monkeypatching `get_config_path()`:

```python
@pytest.fixture(autouse=True)
def _fake_config(tmp_path, monkeypatch):
    config = tmp_path / "fake_config.yaml"
    config.write_text("default_target: local\n")
    monkeypatch.setattr("prism.dagster.targets.get_config_path", lambda: config)
    monkeypatch.setattr("prism.cli.get_config_path", lambda: config)
```

### `tmp_project` (helper)

Creates a minimal ASTRA project in `tmp_path`:

```python
def make_project(tmp_path, spec):
    (tmp_path / "astra.yaml").write_text(yaml.dump(spec))
    (tmp_path / "universes").mkdir()
    (tmp_path / "universes" / "baseline.yaml").write_text("id: baseline\ndecisions: {}\n")
    return tmp_path
```

## CLI tests

Use Click's `CliRunner`:

```python
from click.testing import CliRunner
from prism.cli import main

def test_init_creates_structure(tmp_path):
    runner = CliRunner()
    result = runner.invoke(main, ["init", str(tmp_path / "myproject")])
    assert result.exit_code == 0
    assert (tmp_path / "myproject" / "astra.yaml").exists()
```

## Asset tests

```python
from prism.dagster.assets import build_asset_definitions

def test_asset_keys(simple_spec):
    assets = build_asset_definitions(simple_spec, universe_id="baseline")
    keys = {a.key for a in assets if hasattr(a, 'key')}
    assert dg.AssetKey(["baseline", "accuracy"]) in keys
```

## Runner tests

```python
from prism.dagster.runner import ASTRAContainerRunner

def test_local_execution(tmp_path):
    (tmp_path / "universes").mkdir()
    runner = ASTRAContainerRunner(str(tmp_path), backend="local")
    result = runner.execute(
        command="echo done",
        output_id="out",
        universe_id="baseline",
    )
    assert result.exit_code == 0
    assert result.metadata["backend"] == "local"
```

## Eval tests

Skill performance evals live in `evals/`. Install the optional dependency and run:

```bash
pip install -e ".[eval]"
prism eval run           # run all evals
prism eval run --skill prism-build  # run a specific skill
```

Or via just:

```bash
just evals             # uv sync --extra eval && prism eval run
just evals-skill prism-build
```

Evals measure whether skills produce the expected outputs (e.g. a valid `astra.yaml`) given test fixtures.
