# Testing

## Test layout

```text
tests/
├── conftest.py             # shared fixtures
├── test_cli.py             # Click CliRunner integration tests
├── test_container.py       # detection, image tag, build_image, wrap_recipe, RuntimeChoice
├── test_dask_cluster.py    # cluster_for_run branches & resource keys
├── test_dask_plugin.py     # snakemake_executor_plugin_dask
├── test_eval_tasks.py      # eval task seed specs validate against astra
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

The agentic eval is a plain GitHub Actions workflow —
`.github/workflows/eval.yml` — with no Python harness behind it. On
each PR it scaffolds a project with `lc init`, overlays the seed files
from `evals/tasks/snae/` (`astra.yaml`, `data/`), runs Claude Code
headlessly with `evals/prompt.md` (the astra skill is installed from
the `LightconeResearch/agent-skills` plugin marketplace), and then
checks the outcome with `astra validate` and `lc status --json` — the
job fails unless every declared output is materialized. The built
project (including the agent transcript) is uploaded as a workflow
artifact.

To reproduce locally, run the same commands the workflow does with
`claude`, `lc`, and `astra` on PATH.
