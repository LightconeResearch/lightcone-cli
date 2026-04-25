# Testing

## Test layout

```
tests/
├── conftest.py               # shared fixtures (_isolate_lightcone_home, tmp project helpers)
├── test_cli.py               # CLI command tests
├── test_cli_run.py           # lc run integration tests
├── test_integration.py       # end-to-end tests
├── test_assets.py            # build_asset_definitions tests
├── test_runner.py            # ASTRAContainerRunner tests
├── test_clusters.py          # cluster CRUD, sbatch rendering, QoS preflight tests
└── test_sites.py             # site registry tests
```

## Key fixtures

### `_isolate_lightcone_home` (autouse)

Redirects `~/.lightcone/` reads and writes to a per-test temp directory so tests never touch the developer's real config. Cluster CRUD, cluster cache, and worker-env paths all derive from `Path.home()`:

```python
@pytest.fixture(autouse=True)
def _isolate_lightcone_home(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
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
from lightcone.cli.commands import main

def test_init_creates_structure(tmp_path):
    runner = CliRunner()
    result = runner.invoke(main, ["init", str(tmp_path / "myproject")])
    assert result.exit_code == 0
    assert (tmp_path / "myproject" / "astra.yaml").exists()
```

## Asset tests

```python
from lightcone.engine.assets import build_asset_definitions

def test_asset_keys(simple_spec):
    assets = build_asset_definitions(simple_spec, universe_id="baseline")
    keys = {a.key for a in assets if hasattr(a, 'key')}
    assert dg.AssetKey(["baseline", "accuracy"]) in keys
```

## Runner tests

```python
from lightcone.engine.runner import ASTRAContainerRunner

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
lc eval run           # run all evals
lc eval run --skill lc-build  # run a specific skill
```

Or via just:

```bash
just evals             # uv sync --extra eval && lc eval run
just evals-skill lc-build
```

Evals measure whether skills produce the expected outputs (e.g. a valid `astra.yaml`) given test fixtures.
