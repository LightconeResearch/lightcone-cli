# ADR-0001 — Replace lightcone-cli's local and SLURM execution backends with `dagster-slurm`

- **Status:** Accepted
- **Proposed:** 2026-04-17
- **Accepted:** 2026-04-22
- **Deciders:** lightcone-cli maintainers
- **Affects:** `src/lightcone/engine/runner.py`, `src/lightcone/engine/assets.py`, `src/lightcone/engine/targets.py`, `src/lightcone/engine/site_registry.py`, `src/lightcone/engine/container.py`, `src/lightcone/cli/commands.py`, tests in `tests/`
- **Related upstream:** [`dagster-slurm`](https://github.com/ascii-supply-networks/dagster-slurm) (v1.13.0), docs at <https://dagster-slurm.geoheil.com/>

## 1. Context

lightcone-cli currently ships a bespoke execution layer (`ASTRAContainerRunner` in `src/lightcone/engine/runner.py`) that implements four backends inside a single ~1000-line class: **docker**, **local**, **venv**, and **slurm**. The SLURM backend generates sbatch scripts (`results/.slurm/`), submits with `sbatch`, polls with `sacct` falling back to `squeue`, and wraps commands in `podman-hpc run` when a container is configured. The local backend is a plain `subprocess.Popen` with streaming output, no environment management beyond a `python` substitution. There is no pluggable-backend abstraction; adding or changing a backend requires editing the monolithic class.

This layer is the weakest part of lightcone-cli. It duplicates work that the wider Dagster ecosystem is solving in the open: real-time log streaming over SSH, account/QoS/reservation handling for multiple sites, Slurm job cancellation semantics, heterogeneous jobs, cluster reuse, and metrics collection via `sacct`. Our own tests cover the happy path of sbatch script generation and sacct parsing, but the polling loop, squeue fallback, timeout handling, and podman-hpc resolution are under-tested and have surfaced several pain points already (see "Known limitations" in the runner analysis attached to this review).

The `dagster-slurm` library — published by ASCII Supply Networks and used in production at multiple Austrian HPC centers — is explicitly designed for the scenario lightcone-cli is trying to support: the same Dagster asset runs on a laptop, on a Docker-emulated Slurm cluster for CI, and on a real cluster, with only the resource wiring changing. It exposes a single `ComputeResource` facade with `mode ∈ {local, slurm, slurm-session, slurm-hetjob}` and a Dagster Pipes–based protocol for log streaming, metadata exchange, and materialization events.

The user request that motivates this ADR is explicit: "design a workflow that can execute seamlessly across SLURM computing centers as well as local machines". That is precisely `dagster-slurm`'s charter.

## 2. Decision

We replace lightcone-cli's `local` and `slurm` execution backends with `dagster-slurm`'s `ComputeResource`. The **Docker** backend (`runner._run_docker`, `container.build_image_docker`) is **kept unchanged** because dagster-slurm has no concept of container-per-asset execution, and lightcone-cli's Docker backend is the primary way our users achieve hermetic local runs today. The **venv** backend is retired — dagster-slurm's pixi-based environment packaging supersedes it for local execution, and we will not maintain two similar abstractions.

In the target architecture, lightcone-cli's asset factory no longer calls `runner.execute(command, ...)`. Instead, each Dagster asset is wired with a `ComputeResource` and invokes `compute.run(context, payload_path, extra_env, extra_slurm_opts)`. The heavy lifting — SSH pooling, sbatch submission, `sacct`/`squeue` polling, log streaming, pipes message ingestion, metrics collection — lives in dagster-slurm.

Concretely:

- `ASTRAContainerRunner` is split. A thin `DockerRunner` (≈150 lines, extracted from current code) remains for the Docker backend. SLURM and local paths are deleted.
- A new `src/lightcone/engine/compute_adapter.py` builds a `ComputeResource` from a lightcone-cli target YAML and exposes the single call that `assets.py` needs.
- lightcone-cli recipes are translated into `dagster-slurm` payloads by a generated wrapper script (see §4.3) so that ASTRA's "a recipe is a shell command" invariant is preserved.
- Target config is migrated from lightcone-cli's custom schema (`scheduler:`, `poll:`, `resource_limits:`) into dagster-slurm's `SlurmResource` / `SlurmQueueConfig` / `SSHConnectionResource` shapes, wrapped by a stable lightcone-cli–level schema.
- Python floor moves from 3.11 to 3.12 (dagster-slurm's hard requirement); Dagster is bumped from 1.9 → 1.13.

## 3. Consequences

### 3.1 What we gain

The upstream library gives us, for free, a set of features we would otherwise have to build or defer: real-time stdout/stderr streaming over SSH with automatic fallback from `ControlMaster` to polling for HPC centers that disable socket multiplexing; `sacct`-based metrics (CPU efficiency, max RSS, node-hours) emitted as Dagster output metadata; Dagster Pipes integration that lets a compute payload emit `AssetMaterialization` and asset-check events back to the run without stdout parsing; safe retry and restart semantics driven by Dagster run tags; a session mode that holds a long-lived Slurm allocation across multiple assets (valuable for any multi-asset astra.yaml that shares a cluster reservation); and a working Docker-compose SLURM emulator we can use for CI.

The code delta is large in the negative direction: approximately 750 lines of lightcone-cli's SLURM, local, and venv runner code are deleted, replaced by roughly 250 lines of adapter, payload generation, and configuration translation. Net reduction is on the order of 500 lines in a module that has been a recurring source of bugs.

### 3.2 What we give up or complicate

We lose built-in `podman-hpc` containerization on SLURM. `dagster-slurm` has no container primitive — environment isolation is achieved by `pixi pack` producing a self-extracting tarball that is uploaded to the cluster and sourced in the sbatch script. For sites that rely on `podman-hpc` image builds today (Perlmutter), we must either (a) wrap the recipe command in an explicit `podman-hpc run` invocation inside the generated payload wrapper (see §4.3); or (b) accept pixi-pack isolation as the new hermetic primitive and retire `container.py`'s podman-hpc build path. This ADR recommends (a) as the migration path and deferring (b) to a follow-up decision record; we do not want to couple a runner swap to an isolation-model change.

We inherit a Python 3.12 floor. lightcone-cli already runs under 3.12 in its `.venv`, so this is more a declaration than a lift. Consumers on 3.11 will be broken once merged — this is a semver-minor breaking change from their perspective.

We inherit `dagster-slurm`'s gaps: no support for SLURM `--constraint` out of the box (lightcone-cli exposes this via `extra_slurm_args`; we must either upstream a PR or emit `#SBATCH --constraint` from our payload wrapper); limited multi-hop SSH (single jump host only); remote paths live under `remote_base/runs/{run_id}/`, not `results/.slurm/` (we fetch logs and sbatch scripts back into `results/.slurm/` post-execution to preserve the current artifact layout).

We accept a coupling to Dagster 1.13 pinned by dagster-slurm. Future upgrades are gated on the upstream bumping its own pin.

### 3.3 Risk summary

The highest-risk item is the container-isolation gap on HPC sites. The second-highest is the ASTRA recipe-model mismatch: dagster-slurm expects a Python payload file, lightcone-cli expects a shell command with injected CLI args. The mitigation for both is the payload wrapper described in §4.3, which is small, deterministic, and unit-testable without Docker.

## 4. Detailed design

### 4.1 Module-by-module plan

`src/lightcone/engine/runner.py` loses everything Slurm-, venv-, and local-related: `_run_slurm`, `_run_slurm_interactive`, `_run_local`, `_run_venv`, `generate_sbatch_script`, `translate_resources_to_slurm_directives`, `_podman_hpc_run_command`, `_normalise_time_limit`, `_parse_sbatch_job_id`, `_poll_slurm_job`, `_check_sacct`, `_check_squeue_fallback`, and the venv dependency-hash caching logic. What remains is a `DockerRunner` class with the current Docker fallback-to-local behavior removed (local is no longer a fallback; Docker failure is now a hard failure with a clear error message). The module shrinks from ~1000 to ~180 lines.

`src/lightcone/engine/compute_adapter.py` is new. It owns three things: (1) loading a lightcone-cli target YAML and constructing a `ComputeResource` with the correct `mode` and subordinate `SlurmResource` / `SSHConnectionResource` / `SlurmQueueConfig` objects; (2) building the payload wrapper script for a given recipe (§4.3); (3) staging dagster-slurm's post-execution artifacts (sbatch script, stdout, stderr, metrics) into `results/.slurm/{output_id}_{universe_id}.*` so the current on-disk layout is preserved. This module is the new seam for tests and for future HPC site additions.

`src/lightcone/engine/assets.py` changes minimally. The `_build_single_asset` body no longer constructs a command string and calls `runner.execute`; instead it writes a payload wrapper via `compute_adapter.prepare_payload(recipe, universe_id, params, external_inputs)` and calls `compute.run(context, payload_path=wrapper_path, extra_slurm_opts=_resources_to_slurm_opts(recipe.resources))`. The asset factory still receives a `runner` parameter for backwards compatibility with Docker-only targets; when the target mode is `local` or `slurm`, it resolves to a `ComputeResource` via the adapter.

`src/lightcone/engine/targets.py` gets a new `TargetKind` enum: `docker | local | slurm | slurm-session`. Loaders for `slurm` and `local` now validate against dagster-slurm's config shape. Existing YAML files need a one-time migration (§4.6).

`src/lightcone/engine/site_registry.py` retains its account-suffix resolution and node-type defaults but no longer emits lightcone-cli–native `scheduler_config` dicts. It emits `SlurmQueueConfig` fragments that the adapter merges into the final `ComputeResource`.

`src/lightcone/engine/container.py` loses `build_image_podman_hpc`, `_podman_hpc_migrate`, `image_exists_podman_hpc`, and `resolve_container_for_slurm`. Docker/Podman build and tag-computation logic stays. The `podman-hpc` container runtime is no longer selectable in target configs (the `container_runtime` field is removed); HPC container execution is handled by the payload wrapper's `podman-hpc run` invocation if the target opts in.

`src/lightcone/cli/commands.py` loses its SLURM-specific passthrough for `--partition` / `--qos` / `--constraint` / `--account`. These now live in the target YAML and are overridable per-run via a new `--slurm-opts key=value,...` flag that lands in `extra_slurm_opts` on the compute resource. `lc setup` and `lc target` UX stays visually similar but writes dagster-slurm–shaped configs.

### 4.2 Interface mapping

The current runner interface:

```python
runner.execute(
    command: str,                      # "python scripts/compute.py"
    container: str | None,
    inputs: list[str],
    output_id: str,
    universe_id: str,
    resources: dict[str, Any],         # {cpus, memory, gpus, time_limit, nodes}
    params: dict[str, Any],            # decision params from universes/{id}.yaml
    external_inputs: dict[str, str] | None,
    cwd_override: str | None,
) -> ExecutionResult                   # {exit_code, output_path, metadata}
```

The new equivalent, resolved through the adapter:

```python
payload_path, ctx = compute_adapter.prepare_payload(
    recipe=recipe, universe_id=universe_id, params=params,
    external_inputs=external_inputs, cwd_override=cwd_override,
    output_id=output_id, project_root=project_root,
)
completed = compute.run(
    context=dagster_context,
    payload_path=payload_path,
    extra_slurm_opts=_resources_to_slurm_opts(recipe.resources),
    extra_env={"ASTRA_UNIVERSE": universe_id, "ASTRA_OUTPUT": output_id},
    extra_files=_resolve_external_inputs(external_inputs),
)
yield from completed.get_results()        # Dagster events
compute_adapter.stage_artifacts(completed, ctx)  # copy logs/script into results/.slurm/
```

`resources` → `extra_slurm_opts` translation is direct: `cpus → cpus_per_task`, `memory → mem`, `gpus → gpus_per_node`, `nodes → nodes`, `time_limit → time_limit` (format normalized to `HH:MM:SS`). The `--constraint` value, if present in target config, is injected into the sbatch script header by the payload wrapper (see §4.3) because dagster-slurm's `_build_sbatch_command` does not accept arbitrary sbatch flags.

Return shape: `ExecutionResult` is retired. `compute.run()` returns a `PipesClientCompletedInvocation`; metrics and exit codes are surfaced as Dagster asset metadata by `get_results()`. The `output_path` field (always `results/{universe_id}/`) is redundant — the IO manager is the source of truth for it.

### 4.3 Payload wrapper — the ASTRA → pipes shim

lightcone-cli recipes are shell commands. dagster-slurm expects a Python payload that opens a pipes session. The gap is bridged by a small generated wrapper written per-materialization to a deterministic path:

```python
# results/.payloads/{universe_id}__{output_id}.py  (auto-generated, do not edit)
from dagster_pipes import open_dagster_pipes
import os, subprocess, shlex, sys, json

CMD = {command_quoted}                # "python scripts/compute.py"
CLI_ARGS = {cli_args_json}            # ["--universe", "baseline", "--method", "option_a"]
CWD = {cwd_json_or_none}
CONTAINER_WRAP = {container_wrap_or_none}  # ["podman-hpc", "run", "--rm", ...]
WORKDIR = {workdir_json}

with open_dagster_pipes() as pipes:
    pipes.log.info(f"astra recipe: {CMD} {' '.join(CLI_ARGS)}")
    full = shlex.split(CMD) + CLI_ARGS
    if CONTAINER_WRAP:
        full = CONTAINER_WRAP + full
    proc = subprocess.run(full, cwd=CWD or WORKDIR, check=False)
    pipes.report_asset_materialization(
        metadata={"exit_code": proc.returncode,
                  "command": " ".join(shlex.quote(a) for a in full)}
    )
    sys.exit(proc.returncode)
```

Three reasons this wrapper design is chosen over "just change ASTRA recipes to be Python scripts":

- ASTRA is a spec maintained separately and is explicitly "pure specification". Changing the recipe model to require a pipes-native Python entry point would leak execution-layer concerns back into the spec. lightcone-cli is the agentic layer; shims belong in lightcone-cli.
- The wrapper is small, deterministic, and easy to regenerate. Its contents are a pure function of `(recipe.command, resources, cli_args, container, external_inputs)` — cacheable, testable, and diff-able across runs.
- If a recipe author does write a pipes-native payload in the future, we keep the door open by honoring a `recipe.payload: path/to/script.py` field that bypasses the wrapper and passes the script directly to `compute.run`.

The wrapper also owns `--constraint` and any other sbatch flag dagster-slurm does not accept, by writing a `#SBATCH --constraint=...` line into the *first* comment block of the script itself — sbatch accepts these directives even when the submission command does not specify them. This is a documented upstream pattern and is already exercised by dagster-slurm users for reservations on sites that expose non-standard constraint strings.

### 4.4 Target config translation

Before:

```yaml
# ~/.lightcone/targets/perlmutter.yaml (current)
site: perlmutter
backend: slurm
connection:
  hostname: perlmutter.nersc.gov
scheduler:
  account: m1234
  qos: debug
  partition: gpu
  constraint: gpu
  container_runtime: podman-hpc
  container_flags: ["--gpu", "--mpi"]
  extra_slurm_args: []
poll:
  interval_seconds: 15
  timeout_seconds: 14400
resource_limits:
  max_walltime_minutes: 360
```

After:

```yaml
# ~/.lightcone/targets/perlmutter.yaml (new)
name: perlmutter
mode: slurm                              # local | slurm | slurm-session | docker
site: perlmutter                         # still used by site_registry for account suffix
ssh:
  host: perlmutter.nersc.gov
  user: ${USER}
  key_path: ~/.ssh/nersc
queue:
  partition: gpu
  account: m1234
  qos: debug
  time_limit: "00:30:00"
  cpus: 4
  mem_per_cpu: 4G
  gpus_per_node: 0
remote_base: /pscratch/sd/a/${USER}/lightcone-runs
# HPC container execution (optional, lightcone-cli–level, wrapped in payload):
container:
  runtime: podman-hpc                    # podman-hpc | apptainer | none
  flags: ["--gpu", "--mpi"]
# Raw sbatch escape hatch — appended to every job's #SBATCH header:
extra_sbatch_directives:
  - "--constraint=gpu"
poll:
  timeout_seconds: 14400                 # passed through to compute.run(poll_timeout=)
```

A CLI one-shot — `lc target migrate perlmutter` — rewrites old configs in place, and `lc setup` writes the new shape by default.

### 4.5 Artifact path alignment

`dagster-slurm` writes sbatch scripts and `slurm-{job_id}.out/err` under `{remote_base}/runs/{run_id}/` on the cluster. To preserve lightcone-cli's current on-disk convention (`results/.slurm/{output_id}_{universe_id}.{sh,out,err}`) and to keep `lc status` and tail-style debugging working, the adapter's `stage_artifacts()` step fetches those files via the SSH pool (`completed.metadata['ssh_pool']` or the resource's pool accessor) and writes them locally. The remote copy is the source of truth while the job runs; the local copy is populated once the job terminates. `results/.payloads/{universe_id}__{output_id}.py` additionally gives us a readable, persistent record of the exact command executed.

### 4.6 Migration of existing user state

- Target YAMLs: `lc target migrate <name>` converts old → new schema; a compatibility shim in `targets.py` accepts both shapes for one release cycle, emitting a deprecation warning on the old shape.
- `~/.lightcone/config.yaml`: `default_target` and `default_permissions` fields unchanged.
- In-flight SLURM jobs at the time of upgrade are abandoned; this is consistent with current restart behavior where lightcone-cli has no reattach path for pre-upgrade jobs.

### 4.7 Dependency and version changes

- `requires-python` bumps from `>=3.11` to `>=3.12,<3.13` (follows upstream pin).
- `dagster` bumps from `>=1.9` to `>=1.13,<1.14` (follows upstream pin).
- `dagster-webserver` and `dagster-docker` bump to the matching 1.13 line.
- New dependency: `dagster-slurm>=1.13,<1.14`.
- `dagster-pipes` is added as a transitive dep and must be installed inside every recipe's runtime environment (container or venv) so the payload wrapper can `import dagster_pipes`.
- We do **not** adopt `pixi` as a top-level dependency. The adapter sets `LocalPipesClient(require_pixi=False)` for `mode=local`, which is supported since dagster-slurm v1.12. For SLURM mode we also disable pixi-pack (`ComputeResource(pre_deployed_env_path=...)` or a shim around the packaging step) because lightcone-cli's existing Containerfile-based image builds already solve environment hermeticity; we do not want two competing environment stories.

## 5. Test strategy

### 5.1 Unit tests (no Docker, no network)

Located in `tests/test_compute_adapter.py` (new).

- `test_target_yaml_to_compute_resource` — table-driven: `local`, `slurm`, and `slurm-session` YAML fixtures each produce a `ComputeResource` with the expected mode, SSH config, queue config, and `poll_timeout`.
- `test_resources_dict_to_slurm_opts` — `{cpus: 4, memory: "8G", gpus: 2, nodes: 1, time_limit: "30m"}` → `{cpus_per_task: 4, mem: "8G", gpus_per_node: 2, nodes: 1, time_limit: "00:30:00"}`. Cover edge cases: missing fields, `time_limit` given as `"1h"`, `"2:00:00"`, and `"120"`.
- `test_payload_wrapper_generation` — for a representative recipe (`command: python scripts/compute.py`, params `{method: a}`, container `python:3.11`, external inputs `{raw: /data/raw.parquet}`), assert the generated Python file (a) runs through `py_compile`, (b) contains the exact `CLI_ARGS` we expect, (c) wraps the command in `podman-hpc run` when container is configured, and (d) writes pipes materialization metadata. Snapshot-test the file contents for stability.
- `test_extra_sbatch_directives_injection` — `extra_sbatch_directives: ["--constraint=gpu"]` appears in the `#SBATCH` block of the generated wrapper.
- `test_stage_artifacts` — mock a `PipesClientCompletedInvocation` with a fake SSH pool; assert `results/.slurm/foo_baseline.{sh,out,err}` are written.
- `test_compute_resource_is_constructed_lazily` — constructing a `ComputeResource` with dummy SSH credentials does not open a connection.

These replace `tests/test_runner.py` entirely and a chunk of `tests/test_runner_local.py` / `tests/test_runner_venv.py` (which are deleted along with the code they cover).

### 5.2 Parity tests vs. the current runner (bridge)

Located in `tests/test_runner_parity.py` (new, temporary — removed after the old runner is deleted).

The old `ASTRAContainerRunner` remains importable behind a feature flag (`LIGHTCONE_LEGACY_RUNNER=1`) for the duration of the transition. A parametrized test runs the same astra.yaml against both backends in `mode=local` and asserts:

- Same exit code for each output.
- Same on-disk output files (hash-compared) under `results/{universe}/{output}/`.
- Same Dagster `AssetMaterialization` event keys and values (modulo the new `cpu_efficiency` / `max_rss` metadata fields, which the old path does not emit).

Three fixture astra.yaml files: a single-step pipeline, a two-step pipeline with `inputs:` dependency, and a multi-universe case with `decisions` injected as CLI args. The parity suite runs on CI with a 30-minute budget and is the gate that unlocks deletion of the legacy runner.

### 5.3 Emulator-based integration tests (Docker compose)

Located in `tests/integration/` (new) with `pytest.mark.needs_slurm_docker` and `pytest.mark.slow`. Follows dagster-slurm's own CI pattern.

`tests/integration/docker-compose.yml` references the upstream image directly:

```yaml
services:
  mysql:       { image: mariadb:12, ... }
  slurmdbd:    { image: ghcr.io/ascii-supply-networks/dagster-slurm/slurm-docker-cluster:25-11-2-1, command: [slurmdbd] }
  slurmctld:   { image: ghcr.io/ascii-supply-networks/dagster-slurm/slurm-docker-cluster:25-11-2-1, command: [slurmctld], ports: ["2223:22"] }
  c1: { image: ghcr.io/ascii-supply-networks/dagster-slurm/slurm-docker-cluster:25-11-2-1, command: [slurmd], hostname: c1 }
  c2: { image: ghcr.io/ascii-supply-networks/dagster-slurm/slurm-docker-cluster:25-11-2-1, command: [slurmd], hostname: c2 }
```

We **do not** vendor the build context — we consume the published image. If upstream breaks compatibility with a new tag, CI pins the last-known-good `IMAGE_TAG`.

Fixtures in `tests/integration/conftest.py`:

- `slurm_emulator` (session-scoped, `autouse=False`) — runs `docker compose up -d --wait` and `docker compose down -v` around the test module. Skipped if `DOCKER_HOST` is unreachable.
- `emulator_target` — yields a lightcone-cli target YAML pointing at `localhost:2223` with user `submitter` / password `submitter`, matching the upstream emulator credentials.

Tests:

- `test_emulator_end_to_end_single_asset` — materialize a one-output astra.yaml that runs `python -c "open('out.txt','w').write('ok')"` and assert `results/baseline/out/out.txt == "ok"`.
- `test_emulator_metadata_propagation` — the payload calls `pipes.report_asset_materialization(metadata={"rows": 42})`; the resulting Dagster event carries `rows=42`.
- `test_emulator_non_zero_exit_fails_asset` — a recipe with `command: exit 7` causes the materialization to fail and the SLURM exit code reaches the Dagster run.
- `test_emulator_resource_directives_respected` — `resources: {cpus: 2, time_limit: "00:02:00"}` produces an sbatch script containing `-c 2` and `-t 00:02:00`. Inspect `results/.slurm/*.sh` after the run.
- `test_emulator_sbatch_constraint_via_extra_directives` — asserts an `extra_sbatch_directives` entry reaches the sbatch header.
- `test_emulator_log_streaming` — the payload emits 500 `pipes.log.info` lines; assert all arrive in the Dagster event log (no dropped lines).
- `test_emulator_cancellation` — materialize a long-running recipe, send the Dagster run terminate signal, assert the Slurm job is cancelled (via `sacct --json` in the emulator).

`pixi run start-staging` is *not* used — that is dagster-slurm's internal example bootstrap and we don't want to couple lightcone-cli to it. We invoke the emulator directly with the published image and our own compose file.

### 5.4 CI wiring

A new GitHub Actions job `integration-slurm`:

```yaml
integration-slurm:
  runs-on: ubuntu-latest
  timeout-minutes: 45
  steps:
    - uses: actions/checkout@v4
    - uses: docker/login-action@v3
      with: { registry: ghcr.io, username: ${{ github.actor }}, password: ${{ secrets.GITHUB_TOKEN }} }
    - run: docker compose -f tests/integration/docker-compose.yml up -d --wait --wait-timeout 120
    - run: pip install -e ".[dev]"
    - run: pytest -m "needs_slurm_docker" tests/integration/
    - if: always()
      run: docker compose -f tests/integration/docker-compose.yml down -v
```

Unit tests remain on the default matrix and complete in under a minute; the integration job is opt-in via label (`ci:slurm`) on PRs and runs on every merge to `main`.

## 6. Rollout plan

- **Week 1 — scaffold (no behavior change).** Add `docs/adr/0001-adopt-dagster-slurm.md` (this document). Add dagster-slurm to `pyproject.toml` as an optional extra `[slurm-next]`. Land the new target YAML schema with a loader that accepts both shapes.
- **Week 2 — compute adapter + local mode.** Ship `compute_adapter.py` and route `mode=local` through dagster-slurm behind a `LIGHTCONE_USE_DAGSTER_SLURM=1` env var. Unit tests (§5.1) green. Parity tests (§5.2) green for local.
- **Week 3 — emulator tests + SLURM mode.** Ship the docker-compose emulator fixture and integration tests. Route `mode=slurm` through dagster-slurm behind the same env var. Parity tests green for SLURM against the emulator.
- **Week 4 — flip default + deprecate old runner.** `LIGHTCONE_USE_DAGSTER_SLURM` defaults to `1`. Deprecation warnings fire on the old code path. Publish a migration note for external users.
- **Week 5 — delete.** Remove `_run_slurm*`, `_run_local`, `_run_venv`, podman-hpc build helpers, parity tests, and the feature flag. `tests/test_runner.py` is deleted.
- **Week 6 — follow-up ADR draft.** Separate decision record on whether to retire `podman-hpc run` wrapping in favor of pixi-pack, and whether to adopt `slurm-session` mode as the default for multi-output runs.

Rollback at any phase is a revert of the phase's PR plus resetting the env var default.

## 7. Open questions and assumptions

- **SSH access from user laptops.** dagster-slurm's SLURM mode submits jobs over SSH from the Dagster process. Current lightcone-cli assumes users are logged into the cluster and running lightcone-cli *on* the edge node. We need to decide whether `lc run --target perlmutter` from a laptop is a supported flow or whether we continue to require the edge-node invocation. Recommendation: support both — the adapter treats `ssh.host == "localhost"` and `SLURM_JOB_ID` being set as a signal to short-circuit to a local pipes client with the recipe run via `srun`.
- **Environment packaging strategy.** This ADR proposes to disable pixi-pack and rely on lightcone-cli's existing Containerfile-built images for hermeticity. If that decision is reversed, the container wrapping logic in the payload wrapper becomes redundant and the follow-up ADR mentioned in §6 becomes mandatory, not optional.
- **Constraint-flag fragility.** Emitting `#SBATCH --constraint=...` from the payload wrapper is the cleanest workaround today, but upstream has indicated willingness to accept a PR adding `extra_sbatch_flags` to `SlurmQueueConfig`. We should open that PR in parallel so the workaround is time-boxed.
- **Retry behavior.** lightcone-cli currently has no automatic retry on transient SLURM failures. dagster-slurm supports reattach on Dagster run retry via run tags. Adopting it is a small config change but warrants a separate note to users.

## 8. Alternatives considered

**A. Do nothing.** Status quo preserves full control but leaves ~1000 lines of under-tested execution code on our maintenance budget. Rejected: the user request for seamless laptop↔HPC workflow is not credible to fulfill without either adopting an upstream library or spending substantial effort rebuilding one.

**B. Use dagster-slurm as an *additional* target, keeping the current runner in place.** Minimal risk, minimal benefit. Leaves two execution stacks to maintain forever and does not address the core quality issue. Rejected in the clarification round.

**C. Replace only the SLURM backend, keep lightcone-cli's local backend.** Smaller blast radius. Rejected in the clarification round and on its merits: keeping two code paths for "run a command" when dagster-slurm already offers a unified one is the wrong trade.

**D. Build our own Dagster-Slurm bridge using Dagster Pipes directly, not via dagster-slurm.** Technically feasible — Dagster Pipes is stable. Rejected because we would end up re-implementing the SSH pool, sacct parsing, log streaming, and cluster-emulator story that dagster-slurm already ships, all while claiming not to be "yet another bespoke runner".

**E. Adopt Prefect or Nextflow.** Out of scope — lightcone-cli is a Dagster-native project and the asset factory / IO manager contract is load-bearing.

## 9. References

- dagster-slurm README: <https://github.com/ascii-supply-networks/dagster-slurm/blob/main/README.md>
- dagster-slurm docs site: <https://dagster-slurm.geoheil.com/>
- Upstream compose emulator: `docker-compose.yml` + `docker-compose.ci.yml` in the repo root
- Upstream CI pattern: `.github/workflows/library.yaml` (`Run integration tests against SLURM cluster` step)
- Upstream `ComputeResource` source: `projects/dagster-slurm/dagster_slurm/resources/compute.py`
- Upstream `_build_sbatch_command`: `projects/dagster-slurm/dagster_slurm/pipes_clients/slurm_pipes_client.py:1630`
- Current lightcone-cli runner: `src/lightcone/engine/runner.py` (the module this ADR retires in large part)
- Dagster Pipes protocol: <https://docs.dagster.io/concepts/dagster-pipes>
