# lc eval

Evaluate the lightcone-cli build loop against seed tasks.

This is a **maintainer tool**, not part of the analysis workflow. It
drives a coding agent through a scripted task inside an ephemeral
sandbox, then scores the resulting project. Nothing here touches your
own `astra.yaml`.

## Availability

`lc eval` only registers when its dependencies are installed. They live
in a PEP 735 `[dependency-groups]` entry, **not** a packaging extra —
`pip install lightcone-cli[eval]` does not work. From a source checkout:

```bash
git clone https://github.com/LightconeResearch/lightcone-cli
cd lightcone-cli
uv sync --group eval
uv run lc eval --help
```

Without the group, `lc eval` fails with `Error: No such command 'eval'`.

The harness also needs credentials in the environment (a `.env` file at
the project root is loaded automatically):

- `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN` — passed into the
  sandbox so the agent can run.
- Daytona credentials — sandboxes are Daytona-hosted.

## Synopsis

```text
lc eval run     [OPTIONS] CONFIG_PATH
lc eval report  [OPTIONS] RESULTS_PATH
lc eval compare RESULTS1 RESULTS2
```

## `lc eval run`

Run an eval suite from a config file.

| Option | Default | Effect |
|--------|---------|--------|
| `--concurrency`, `-c N` | from the config's `max_concurrency` | Max parallel sandboxes. |
| `--num-trials`, `-n N` | from the config's `num_trials` | Override the trial count. |
| `--dry-run` | off | Print the trial schedule and exit without running anything. |
| `--evals-dir PATH` | `evals/` in the cwd | Where task definitions live. |

A config is a small YAML file naming the tasks to run:

```yaml
id: build
tasks:
  - snae
num_trials: 3
max_concurrency: 2
output_dir: "eval-results"
```

Each task is a directory under `evals/tasks/<id>/` holding a `task.yaml`
(the prompt and its graders), a target `astra.yaml`, and any fixture
`data/`. Graders come in three shapes: a shell command (exit 0 = pass),
a `status` grader scoring the fraction of declared outputs that
materialized, and a custom script whose last stdout line is the score.
The composite score is their weighted mean.

Results are written to `<output_dir>/<run-id>-<timestamp>/results.json`.

```bash
lc eval run evals/build.yaml
lc eval run evals/build.yaml --dry-run
lc eval run evals/build.yaml --num-trials 1 --concurrency 2
```

## `lc eval report`

Re-display a previous run's results as a table.

| Option | Effect |
|--------|--------|
| `--json` | Emit the raw JSON summary instead of the table. |

```bash
lc eval report eval-results/build-20260315/results.json
```

## `lc eval compare`

Print two runs side by side — the fastest way to see whether a skill or
prompt change moved the needle.

```bash
lc eval compare eval-results/run1.json eval-results/run2.json
```

The implementation lives in `src/lightcone/eval/` — `cli.py` for the
command surface, with `harness.py`, `sandbox.py`, `graders.py`, and
`report.py` behind it.
