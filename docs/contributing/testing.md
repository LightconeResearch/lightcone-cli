# Testing

## Test layout

```
tests/
├── conftest.py             # shared fixtures
├── test_cli.py             # Click CliRunner integration tests
├── test_container.py       # detection, image tag, build_image, wrap_recipe, RuntimeChoice
├── test_dask_cluster.py    # cluster_for_run branches & resource keys
├── test_dask_plugin.py     # snakemake_executor_plugin_dask
├── test_eval_*.py          # eval harness (graders, harness, models, report)
├── test_manifest.py        # write_manifest, sha256_dir, code_version
├── test_snakefile.py       # generator + final `snakemake -n` parse test
├── test_status.py          # OutputStatus across ok/stale/missing/alias
├── test_tree.py            # collect_tree_outputs, find_upstream_output, …
├── test_validation.py      # validate_output across metric/table/figure types
└── test_verify.py          # verify_outputs across all three failure kinds
```

Tests mirror `src/` 1:1 — when you add a module, add a test file at the
matching path.

## Common patterns

### CLI tests (Click `CliRunner`)

```python
from click.testing import CliRunner
from lightcone.cli.commands import main

def test_init_creates_structure(tmp_path):
    runner = CliRunner()
    result = runner.invoke(main, ["init", str(tmp_path / "myproject"), "--no-git", "--no-venv"])
    assert result.exit_code == 0
    assert (tmp_path / "myproject" / "astra.yaml").exists()
```

### End-to-end against a tmp project

`test_status.py`, `test_verify.py`, and `test_snakefile.py` build a
minimal ASTRA project under `tmp_path` (one `astra.yaml`, one
`universes/baseline.yaml`, optional sub-analyses), then run the
function under test. Helpers:

- `astra.helpers.load_yaml` / `resolve_analysis_tree` mirror what
  production code does.
- `lightcone.engine.snakefile.generate(project, universes=[...], runtime="none")`
  for tests that need an actual Snakefile.

### Snakefile parsing

`tests/test_snakefile.py` ends with a parse test that runs
`snakemake -n -s <generated-Snakefile>` to confirm the generator
produces a Snakefile the upstream tool actually accepts. Add a similar
assertion when changing rule shape.

### Slow tests

```bash
uv run pytest -m slow            # opt in to the slow tests
```

The `slow` marker is reserved for tests that start a real Dask cluster.
Do not use it for things that are merely a bit chatty — prefer trimming
test scope.

## Eval harness (separate)

Skill performance evals live in `evals/` with their fixtures in
`evals/tasks/`. The harness lives at `lightcone.eval.harness`. **The
`lc eval` CLI subgroup is currently not registered on `main`** — the
top-level `lc` invocation will fail with "No such command: eval". To
run evals today, invoke the harness in Python directly:

```python
from pathlib import Path
from lightcone.eval.harness import load_run_config, run_eval

config = load_run_config(Path("evals/example-run.yaml"))
result = run_eval(config, Path("evals"))
```

When the eval CLI is rewired (it should be a one-line `add_command` in
`lightcone.cli.commands`), the documented incantation will be:

```bash
lc eval run evals/example-run.yaml
lc eval report eval-results/<run>/results.json
lc eval compare eval-results/<run1>.json eval-results/<run2>.json
```
