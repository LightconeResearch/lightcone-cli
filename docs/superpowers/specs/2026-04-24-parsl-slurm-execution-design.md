# Parsl-backed SLURM execution

**Date:** 2026-04-24
**Branch:** `dagster-parsl`
**Status:** Design — awaiting implementation

## Problem

Today's SLURM backend (`lightcone.engine.runner._run_slurm`) submits one
`sbatch` per recipe. Each submission re-pays the queue wait — at sites
where even the `debug` queue is ~20 min, an analysis with N recipes
takes N × queue-wait, regardless of how trivial the recipes are. The
runner also carries ~600 lines of bespoke sbatch generation, sacct/squeue
polling, and per-recipe QoS fit/switch clamping that all exist solely
because we run one job per recipe.

## Goal

Replace the per-recipe-sbatch SLURM backend with a single-allocation
**pilot** model implemented on Parsl. One `lc run` invocation acquires
one (or, optionally, two) SLURM allocation(s) and dispatches every
recipe in the analysis tree into them, paying the queue wait once.

Dagster keeps everything it does today: asset lineage, dependency
resolution, materialization status, the webserver. Parsl replaces only
the *execution substrate* under `backend: slurm`.

## Non-goals

- Autoscaling — `init_blocks = min_blocks = max_blocks = 1` per pilot.
  No dynamic provisioning of additional allocations within a single
  `lc run`.
- Pre-existing-allocation attach (`salloc` + `lc run`). Future work.
- Live stdout streaming during recipe execution. v1 reads stdout/stderr
  from Parsl's `task_logs/` after the task completes (matches the
  current SLURM backend's UX, which also blocks silently while polling
  `sacct`).
- Multi-pilot autoscale or cross-pilot task migration.
- Any change to `backend: local`, `backend: docker`, `backend: venv`,
  or `backend: docker` paths.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Execution shape | Block-based pilot, fixed size, one `lc run` = one (or two) allocation(s) | Pays queue wait once; matches the way HPC users think about compute budgets. |
| Concurrency inside an allocation | Per-task resources via `WorkQueueExecutor` | True bin-packing of heterogeneous recipes (1-CPU plot + 32-CPU sim sharing a node). HTEX's `max_workers_per_node` assumes uniform tasks, which is wrong for our workload. |
| Dagster integration | Asset → `bash_app(...).result()` (loose coupling) | Preserves Dagster's lineage / UI / status story. The runner becomes one method that builds a Parsl `bash_app` and awaits it. Minimal change to `assets.py`. |
| Pilot count per `lc run` | One `cpu` pilot, optionally one `gpu` pilot, optionally one `mpi` pilot — each its own executor | Real HPC sites separate CPU and GPU into different QoSes/partitions; one SlurmProvider per pilot is the cleanest mapping. |
| Rollout | Replace `_run_slurm` entirely; delete legacy code | Greenfield SLURM rewrite. No compat shim. |
| WorkQueue dependency | Hard-required when `backend: slurm` selected | Partial bin-packing via HTEX fallback would be a confusing footgun. |
| Container path | Unchanged — bash command still includes `podman-hpc run …` | `bash_app` runs an arbitrary shell command; no new mechanism needed. |

## Architecture

```
lc run
  └── build_parsl_config(target_config, cli_overrides) -> parsl.Config
  └── with parsl.load(config):
        └── build_definitions(...) -> dagster.Definitions
        └── definitions.execute_in_process(...)
              └── (per asset) ASTRAContainerRunner.execute(...)
                    └── _run_slurm(...)        # body rewritten on top of bash_app
                          └── bash_app(executors=[label],
                                       parsl_resource_specification=spec)(cmd).result()
                                └── WorkQueue dispatches into the pilot allocation
        # context exit -> parsl.dfk().cleanup() -> SLURM allocation(s) released
```

**Lifecycle.** The DFK is process-global (Parsl's design) and owned by
the `lc run` CLI command, not by the runner. `with parsl.load(config):`
loads the DFK on entry and calls `parsl.dfk().cleanup()` on exit
(including Ctrl-C and exception paths), which releases the SLURM
allocation immediately. No orphaned jobs.

**Routing.** A single function in `parsl_backend.py` picks an executor
label per recipe based on the recipe's resources and which pilots are
configured in the target:

```python
def pick_executor(resources: dict, pilots: dict) -> str:
    if resources.get("nodes", 1) > 1 and "mpi" in pilots:
        return "mpi"
    if resources.get("gpus") and "gpu" in pilots:
        return "gpu"
    if "cpu" in pilots:
        return "cpu"
    raise ConfigurationError(
        f"No suitable pilot configured for resources={resources}; "
        f"available pilots: {list(pilots)}"
    )
```

Falling through to a missing pilot raises immediately at task dispatch
time with a clear message — better than silently routing to the wrong
pool.

## Code structure

**New file** — `src/lightcone/engine/parsl_backend.py` (~150 lines):

```python
def build_parsl_config(
    target_config: dict[str, Any],
    cli_overrides: dict[str, Any] | None = None,
) -> parsl.Config:
    """Build a parsl.Config from target YAML.

    Constructs one WorkQueueExecutor per pilot in target_config["pilots"].
    Each executor wraps a SlurmProvider with init/min/max_blocks all = 1.
    Translates target options (qos, account, constraint, partition) and
    CLI overrides directly into SlurmProvider kwargs.
    """

def pick_executor(resources: dict[str, Any], pilots: dict[str, Any]) -> str:
    """Route a recipe to a pilot based on its resources."""

def recipe_resources_to_parsl(resources: dict[str, Any]) -> dict[str, Any]:
    """Map ASTRA recipe.resources to WorkQueue per-task spec.

    Keys: cores, memory (MB), gpus, wall_time (seconds).
    """

def validate_pilots_against_qos(
    pilots: dict[str, Any],
    target_name: str,
) -> None:
    """Pre-flight check at lc run start.

    For each pilot, look up the QoS in the cluster cache and verify that
    nodes/walltime fit. Raises with a clear message if not. Replaces the
    per-recipe fit/switch clamping that previously ran inside _run_slurm.
    """
```

**Modified** — `src/lightcone/engine/runner.py`:

Deletions (~650 lines):

- `_run_slurm` (current body)
- `_validate_and_adjust_qos`
- `_parse_time_minutes`
- `generate_sbatch_script`
- `_podman_hpc_run_command` *(only the sbatch-script wrapping; the
  in-bash `podman-hpc run …` string construction is moved into
  `_run_slurm`'s new body — it's the same command, just no longer
  embedded in a sbatch heredoc)*
- `_shell_quote`
- `_parse_sbatch_job_id`
- `_poll_slurm_job`
- `_check_sacct`
- `_check_squeue_fallback`
- `translate_resources_to_slurm_directives`
- `_normalise_time_limit`

Addition — `_run_slurm` rewritten (~30 lines):

```python
def _run_slurm(
    self,
    command: str,
    container: str | None,
    input_ids: list[str],
    output_id: str,
    universe_id: str,
    resources: dict[str, Any],
    external_inputs: dict[str, str] | None = None,
    cwd: str | None = None,
) -> ExecutionResult:
    from parsl import bash_app
    from parsl.app.errors import BashExitFailure
    from lightcone.engine.parsl_backend import (
        pick_executor, recipe_resources_to_parsl,
    )

    pilots = self.target_config.get("pilots", {})
    label = pick_executor(resources, pilots)
    spec = recipe_resources_to_parsl(resources)

    # Build the same shell command we'd previously have embedded in
    # an sbatch script (podman-hpc run, external-input symlinks, etc.).
    full_cmd = build_recipe_shell_command(
        command, container, container_runtime=..., resources=resources,
        external_inputs=external_inputs, cwd=cwd or str(self.project_root),
    )

    @bash_app(executors=[label], parsl_resource_specification=spec)
    def _run(stdout=parsl.AUTO_LOGNAME, stderr=parsl.AUTO_LOGNAME):
        return full_cmd

    fut = _run()
    try:
        fut.result()
        exit_code = 0
    except BashExitFailure as e:
        exit_code = e.exitcode

    stdout_tail = _read_tail(fut.stdout, _TAIL_CHARS)
    stderr_tail = _read_tail(fut.stderr, _TAIL_CHARS)

    return ExecutionResult(
        exit_code=exit_code,
        output_path=self.project_root / "results" / universe_id,
        metadata={
            "backend": "slurm",
            "executor": label,
            "stdout": stdout_tail,
            "stderr": stderr_tail,
        },
    )
```

The `build_recipe_shell_command` helper is essentially today's
`_podman_hpc_run_command` plus the external-input symlink loop from
`generate_sbatch_script`, lifted out of the sbatch context. It produces
the same shell command string that was previously written into an sbatch
script body.

**Modified** — `src/lightcone/cli/commands.py`:

The `lc run` command wraps `execute_in_process` in the Parsl context:

```python
if backend == "slurm":
    from lightcone.engine.parsl_backend import (
        build_parsl_config, validate_pilots_against_qos,
    )
    validate_pilots_against_qos(
        target_config["pilots"], target_name=target_name,
    )
    parsl_config = build_parsl_config(target_config, cli_overrides)
    with parsl.load(parsl_config):
        defs = build_definitions(...)
        defs.get_implicit_global_job_def().execute_in_process(...)
else:
    defs = build_definitions(...)
    defs.get_implicit_global_job_def().execute_in_process(...)
```

**Unchanged** — `src/lightcone/engine/slurm_info.py` (still drives QoS
validation, just at pilot scope now), `src/lightcone/engine/targets.py`,
`src/lightcone/engine/assets.py` (the runner-call inside `_asset` is
unchanged — it still does `runner.execute(...)`).

## Target YAML — schema change

One new top-level field, `pilots`, replacing the current per-recipe
`scheduler:` resource block. Existing `qos`, `constraint`, `account`,
`partition`, `time_limit` choices/defaults stay where they are inside
each pilot.

```yaml
backend: slurm
site: perlmutter
container_runtime: podman-hpc
pilots:
  cpu:
    nodes: 4
    walltime: 2h
    qos: debug
    account: m1234
    worker_init: |
      module load python
      source $HOME/.lightcone/envs/perlmutter/bin/activate
  gpu:
    nodes: 2
    walltime: 1h
    qos: debug
    constraint: gpu
    account: m1234_g
    worker_init: |
      module load python cudatoolkit
      source $HOME/.lightcone/envs/perlmutter-gpu/bin/activate
```

The intent-based options machinery (`qos.choices`, `qos.default`, CLI
overrides via `--qos`) still works — it now resolves once per pilot at
`lc run` start instead of once per recipe.

A migration note in the changelog covers users with existing target
files. Since this is a deliberate greenfield rewrite of the SLURM path,
old target files for `backend: slurm` won't load until updated to the
new shape — `lc target validate` should give a clear "missing `pilots`
key" error.

**CLI flags added to `lc run`** for ad-hoc pilot sizing without editing
the target YAML:

- `--pilot <name>:nodes=N` / `--pilot <name>:walltime=T` — override a
  pilot's size for one invocation. Resolution order: CLI > target file.
- Existing per-axis flags (`--qos`, `--constraint`, `--account`,
  `--time-limit`) keep working but now apply to **all** pilots in the
  target unless qualified (`--qos cpu:debug`).

## Data flow

Happy path (one recipe):

1. Dagster invokes the asset's `_asset(context)` function.
2. `runner.execute(...)` dispatches to `_run_slurm(...)`.
3. `_run_slurm` builds the bash command (the same string we'd have put
   in an sbatch script today, minus the `#SBATCH` header).
4. `bash_app(...).result()` blocks until WorkQueue dispatches the task
   into the pilot allocation and the worker exits.
5. Stdout/stderr files written by Parsl under
   `runinfo/<run_id>/task_logs/` are tail-read (last 2000 chars,
   matching today's `_TAIL_CHARS`).
6. Returns `ExecutionResult(exit_code=0, output_path=…,
   metadata={backend: "slurm", executor: "cpu", stdout: …, stderr: …})`.

Subsequent recipes pay no queue wait — they dispatch into the already-
running pilot allocation immediately.

## Error model

| Failure | Source | How surfaced |
|---|---|---|
| Pilot config invalid (e.g. nodes > QoS limit) | `validate_pilots_against_qos` at `lc run` start | Exit before any asset runs, with a clear "pilot config rejected by QoS X (max nodes Y)" message |
| Pilot allocation rejected by SLURM | `SlurmProvider` → `sbatch` non-zero | Parsl raises at first task submission; caught in the `with parsl.load(…):` context, prints sbatch stderr, exits |
| Recipe non-zero exit | `BashExitFailure` from `bash_app` | Caught in `_run_slurm`; surfaced as `ExecutionResult(exit_code=e.exitcode, …)` — same shape as today, asset still raises `RuntimeError` upstream |
| Pilot allocation expired mid-run | Parsl marks pending tasks as failed | `bash_app(...).result()` raises; surfaced as `ExecutionResult(exit_code=1, metadata={"slurm_state": "TIMEOUT_PILOT"})`. No retry in v1. |
| Ctrl-C / Dagster failure | Context exit | `with parsl.load(…):` cleanup releases the SLURM allocation immediately |

## Testing strategy

| Layer | What it tests | How |
|---|---|---|
| **Unit** (no Parsl runtime) | Pure functions in `parsl_backend.py` | `build_parsl_config` returns a `parsl.Config` with the right `SlurmProvider(nodes_per_block=4, qos=…, account=…)`; `pick_executor` routing rules; `recipe_resources_to_parsl` mapping |
| **Integration** (Parsl + LocalProvider) | End-to-end `_run_slurm` path without SLURM | One `WorkQueueExecutor` with `LocalProvider(init_blocks=1)`. Run real `bash_app` tasks (`echo foo`, `exit 1`, `cat $missing`). Verify `ExecutionResult` shape, exit codes, stdout tail, `BashExitFailure` handling, container-command wrapping |
| **Smoke** (real SLURM) | Manual, not in CI | `tests/manual/test_perlmutter.py` script run before each release. Asserts: pilot acquires one allocation, three trivial recipes run sequentially within it, allocation released on exit |

**Deletions from the test suite:**

- All tests that mock `sbatch`/`sacct`/`squeue` subprocess calls
- `test_validate_and_adjust_qos` cases that drove the per-recipe
  fit/switch logic (the pilot-level pre-check is one test, not the
  dozens we have now)
- `test_generate_sbatch_script` and the directive-translation tests

**Kept and re-pointed:**

- `test_integration.py` flows using `backend: local` are unchanged
- `test_cli_run.py` flows that drive `lc run` get a `mocker.patch
  ("parsl.load")` so they don't actually boot a DFK during CLI testing

**New fixture** — `parsl_local_pilot` in `tests/conftest.py`: yields
after `parsl.load(local_config)`, cleans up after.

**Pre-merge smoke checklist** (manual, in PR description):

- [ ] `lc run --target perlmutter` on a 3-recipe analysis: one
  allocation acquired, all three run, allocation released
- [ ] Ctrl-C mid-run → allocation released within 30 s
  (`squeue -u $USER` shows it gone)
- [ ] Recipe failure → other recipes continue, only the failed asset
  is marked failed in Dagster

## Dependencies

Add to `pyproject.toml`:

- `parsl >= 2024.1.0` — Parsl uses date-style versions; any 2024+
  release supports WorkQueue and the `parsl_resource_specification`
  interface used here. Pin the floor in `pyproject.toml` and the
  upper bound only if Parsl ships a breaking 2025 release.
- `ndcctools >= 7.10` — provides the `work_queue` Python module that
  WorkQueueExecutor imports (conda-only on most platforms; the install
  guidance for `lc setup` will need a note)

The `ndcctools` install footprint is non-trivial (~hundreds of MB).
`lc setup` should detect when SLURM targets are being configured and
guide the user through installing it (e.g., via `conda install -c
conda-forge ndcctools`).

## Open questions for implementation

1. **Worker environment** — Parsl workers run on compute nodes and need
   the project's Python env available. Today the SLURM script does
   `source .venv/bin/activate` implicitly via `worker_init`. The pilot's
   `worker_init` field will carry this. Document the pattern in
   `docs/hpc/`.
2. **Run directory** — Parsl's `runinfo/` directory should land under
   `results/.parsl/` to mirror today's `results/.slurm/` convention.
   Set via `Config(run_dir=...)`.
3. **Telemetry** — the existing Langfuse hook fires per-recipe. Verify
   it still fires from inside the Parsl-wrapped path; nothing structural
   should change since we still call `runner.execute(...)` per asset.

## Net impact

- `runner.py`: ~1107 → ~540 lines (delete ~650, add ~30 in `_run_slurm`,
  add ~50 in extracted `build_recipe_shell_command`)
- `parsl_backend.py` (new): ~150 lines
- `commands.py`: +~10 lines for the `with parsl.load(...):` wrapper
- Test suite: net deletion (mock-heavy SLURM tests gone; new
  integration tests are smaller)
- **Net: roughly −400 LOC in the engine, dramatically better SLURM UX.**
