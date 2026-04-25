# `dagster-slurm-pilot` — persistent Dask cluster on SLURM, native dagster-dask

**Date:** 2026-04-25
**Status:** Design — seeking review before implementation
**Supersedes (if accepted):** `docs/superpowers/specs/2026-04-24-parsl-slurm-execution-design.md`

---

## TL;DR

Build a small pilot manager that:

1. The user invokes once to **`sbatch` a single SLURM allocation** that hosts a persistent Dask cluster.
2. Stays alive for the full QoS walltime allowed by the site (24h on Perlmutter regular).
3. Lets every subsequent `lc run` connect to it via Dagster's stock `dask_executor` with `cluster: existing` — **zero queue wait** for the second-through-Nth invocation.
4. Is container-first: recipes execute as `<container-runtime> run …` shell commands inside Dask tasks, not as pickled Python callables.
5. Assumes orchestrator and compute share a filesystem. **No SSH, no data shipping, no laptop-to-cluster handoff.**

The user-side experience is:

```
$ lc pilot start --target perlmutter      # queues ONCE
$ lc run                                   # instant
$ lc run                                   # instant
$ lc run                                   # ...
$ lc pilot stop --target perlmutter
```

The design targets **any SLURM cluster** that meets a few documented preconditions (shared filesystem visible to login + compute, routable TCP between them, supported container runtime). NERSC Perlmutter is the **first deployment** — every choice is verified there, every gotcha is documented, and every release blocks on Perlmutter passing CI. Other SLURM sites are expected to work with site-specific config; we make no v1 commitment beyond Perlmutter.

---

## Problem

The current `dagster-parsl` branch ships a Parsl + WorkQueue pilot that pays the SLURM queue wait once per `lc run`. That's better than per-recipe sbatch, but four structural problems remain:

1. **Three scheduling layers stacked** (Dagster → Parsl → WorkQueue), each adding code, dependencies, and failure modes.
2. **Off-piste from NERSC docs.** NERSC documents Parsl with `HighThroughputExecutor`, not `WorkQueueExecutor`; `ndcctools` is a hidden conda dep most users don't have.
3. **Pilot is tied to a single Dagster run.** Every `lc run` queues *once*, but a user iterating on recipes pays N queue waits for N debug cycles.
4. **The community `dagster-slurm` plugin doesn't fit our shape.** It's built around SSH-from-laptop and per-asset sbatch — exactly the assumptions we don't share. (See "How this differs from `dagster-slurm`" below.)

## Goal

Build a small, focused integration around two ideas:

1. **A persistent, user-managed pilot.** The user starts a Dask cluster on a SLURM allocation once. They queue once. After that, every `lc run --target <name>` connects to the running cluster and dispatches assets with **zero queue wait**.
2. **Native `dagster-dask`.** Dagster already ships `dask_executor` with explicit support for `cluster: existing`. We use it as-is. Each Dagster step (asset materialization) becomes a Dask task that shells out to a container. No custom executor, no Pipes plumbing, no new Dagster glue.

## Scope: generic SLURM, Perlmutter-validated

This is a **generic SLURM** integration. Nothing in the architecture, the wire protocol, the configuration schema, or the CLI surface assumes Perlmutter. The pilot is an `sbatch` script. The rendezvous is a Dask scheduler-file on a shared filesystem. Workers are `dask worker`s launched with `srun`. The orchestrator is `dagster-dask` with `cluster: existing`. None of those pieces care which SLURM site is underneath.

**Perlmutter is the first user, and the bar.** Every decision in this doc is verified against Perlmutter; every release ships only after Perlmutter integration tests pass; site-specific gotchas (Lustre file locking, QoS walltime caps, `podman-hpc` quirks) are first-class concerns documented in their own section.

What this means concretely:

- **Site abstractions are first-class.** A `SiteConfig` describes a cluster's defaults (scratch path, container runtime, typical QoS, `worker_init` boilerplate). `SiteConfig.perlmutter()` ships built-in. `SiteConfig.frontier()`, `SiteConfig.expanse()`, etc. are addable as future PRs without architectural change.
- **No Perlmutter values are hardcoded.** `$PSCRATCH` does not appear in the code path; `scratch_root` does, and the Perlmutter site config sets it to `$PSCRATCH`. Same for `podman-hpc` (configurable container runtime), `m1234` (configurable account), etc.
- **Other SLURM sites should work.** Standard Cray-Slurm, vanilla SLURM, university clusters — if they meet the preconditions below, they are expected to work. We do not promise it; users who hit issues outside Perlmutter file bugs and we fix them as bandwidth allows.

### Preconditions for any SLURM site

A SLURM cluster is in scope for this plugin if it meets all of:

1. **Shared filesystem visible to login + compute nodes.** The orchestrator and every Dask worker must be able to read/write the same path. POSIX-compatible. File locking ideally supported (Dask uses it for the scheduler-file).
2. **TCP from orchestrator host to compute nodes.** A Dask client on the orchestrator host must be able to open a socket to a Dask scheduler running on a compute node. Routable IPs preferred; SSH-tunnel rendezvous is a fallback we do not implement in v1.
3. **A container runtime on compute nodes.** `podman-hpc`, `singularity`/`apptainer`, or `docker`. Configured per site.
4. **Standard SLURM CLI** (`sbatch`, `squeue`, `scancel`, `sacct`).

Sites that don't meet these preconditions (e.g., kerberized filesystems with no shared-fs orchestrator host, air-gapped enclaves, or sites without container runtimes on compute nodes) are out of scope. We will not engineer around their constraints in v1.

## The deployment model we are designing for (and the one we are not)

This plugin is built for a specific deployment shape. Stating it up front:

### What we support

**Co-located orchestration.** The Dagster process — `lc run` — executes on a host that shares a filesystem with the SLURM compute nodes. Concretely on a typical SLURM site:

- A login node (`<cluster>.<center>.<domain>`)
- A compute node inside an interactive `salloc`
- A dedicated workflow / orchestrator node, where the site provides one (e.g. NERSC's workflow nodes)

This is the **same machine, same filesystem** assumption. The user's working tree, the recipe code, the `astra.yaml`, the container images, and the data inputs all live on the cluster's shared storage — visible identically to the orchestrator and to every Dask worker on every compute node.

**Why this matters:** we don't have to move code, data, or context across a network boundary. The orchestrator writes a file; the worker reads the same file. No SSH, no `pixi`/`conda-pack` shipping, no DBFS, no S3 staging, no scheduler-file rsync.

### Who we are designing for

**AI coding agents iterating in-cluster.** Lightcone's primary user is an AI agent (Claude Code, etc.) that:

- Runs *on* the cluster filesystem, in a working directory under the user's home or scratch space.
- Reads, edits, and writes recipe code in place.
- Invokes `lc run` against a long-running pilot to materialize assets.
- Iterates fast — dozens of `lc run` cycles per analysis is the working mode, not the exception.

For this user, queue wait per iteration is the difference between "agent does useful work" and "agent times out waiting." The persistent pilot is the load-bearing capability.

### What we explicitly do not support

- **Laptop-to-cluster orchestration.** A workstation/laptop submitting jobs to a remote SLURM center over SSH. This pattern requires shipping code, dependencies, and inputs across a network boundary. We're not building it. Users in that workflow should `ssh <cluster>` and run `lc` from there.
- **Cross-site federation.** Running one analysis across two sites at once.
- **Data transfer between orchestrator and compute.** If your code or data isn't already on the shared filesystem, get it there first; we don't help.

This is a deliberate narrowing. It buys us simplicity that broader-scope plugins (notably `dagster-slurm`) cannot have.

## Container support is the unit of execution

A non-trivial requirement: **recipes execute as containers, not as Python environments.**

ASTRA recipes are arbitrary shell commands; the most common form is `<container-runtime> run --image=<sha-pinned> -- <recipe-cmd>`. This is intentional:

- **Reproducibility.** Container hashes are the deterministic provenance unit; Python venvs aren't.
- **Heterogeneity.** Different recipes use different container images (Python 3.9 + cosmosis here, Julia + pyTorch there). One Python env on the cluster cannot serve all recipes.
- **Isolation.** A bad recipe inside a container can't corrupt the orchestrator's environment.

The container runtime is **per-site**. On Perlmutter it's `podman-hpc`; on a Singularity-only site it's `singularity exec`; on a developer's local SLURM box it might be `docker run`. Site config picks one; everything else is identical.

This shapes the design in three ways:

1. **The Dask worker's only job is to fork a subprocess.** The asset body is `subprocess.run([<runtime>, "run", ...])`. The worker doesn't import the recipe's libraries; it doesn't need to. So worker startup is fast and the worker's Python env is minimal — just enough to run Dagster, Dask, and shell out.
2. **`worker_init` only sets up the orchestrator-side bootstrap** (Python + Dask + lightcone), not per-recipe deps. Per-recipe deps are baked into containers.
3. **We don't use `dagster-pipes` for v1.** Pipes is the right answer when external code wants to stream structured metadata back to Dagster; for ASTRA's containerized shell recipes the asset body is a subprocess call whose exit code is the materialization signal. Pipes can be added in v1.1 if recipes adopt richer telemetry.

This is one of the largest assumption deltas from `dagster-slurm`, which leans heavily on `pixi`-packaged Python environments shipped to compute nodes.

## How this differs from `dagster-slurm` (the community plugin)

The official Dagster docs list a community-maintained `dagster-slurm` plugin (by Georg Heiler / ASCII supply networks). It's the closest prior art and we should be explicit about why we're building a different thing rather than adopting it.

| Concern | `dagster-slurm` (community) | This design |
|---|---|---|
| **Orchestrator location** | Designed around laptop / off-cluster orchestrator. Includes an SSH connection pool. | Same-filesystem orchestrator. No SSH. |
| **Allocation granularity** | One `sbatch` per asset (today). "Multi-asset fusion into a single allocation" is on the roadmap. | One `sbatch` per *user session* (the pilot). Many Dagster runs, many assets per run, all share one allocation. |
| **Queue wait per Dagster run** | One per asset → N per run → N×M for M iterative debug cycles. | Paid once at `lc pilot start`. Zero thereafter. |
| **Wire protocol** | Dagster Pipes over SSH + remote filesystem. | `dagster-dask` `cluster: existing`. Plain TCP to a Dask scheduler running on a compute node. |
| **Environment shipping** | Pixi-packaged Python envs sent to compute nodes; pluggable Bash / Ray / Spark launchers. | Containers (`podman-hpc`, `singularity`, etc.) baked once, referenced by digest. Worker env is minimal. |
| **Filesystem assumption** | Code is on the user's laptop; gets staged via SSH + remote fs. | Code is already on the cluster; orchestrator and workers see identical paths. |
| **Target user** | A data scientist on a workstation submitting to a remote HPC center. | An AI coding agent (or human) iterating in a cluster shell session. |

We're not adversarial to `dagster-slurm` — it's solving an important, broader problem. But its assumptions and ours diverge at the foundation. The persistent-pilot + co-located orchestrator + container-native combination is what we need; bolting it on top of `dagster-slurm` would mean replacing more of its surface than reusing.

If `dagster-slurm` ever lands its "session-based cluster reuse" roadmap item *and* drops the SSH assumption, a future merge becomes worth discussing. Today, it isn't.

## What we validated before designing

| Question | Answer | Source |
|---|---|---|
| Does `dagster-dask` support attaching to an existing scheduler? | Yes — `cluster: existing` with `address: <host>:<port>`. Dagster connects, dispatches, disconnects. No teardown logic. | docs.dagster.io/deployment/execution/dask |
| Can a login-node Dask client TCP to a compute-node Dask scheduler on Perlmutter? | **Yes — empirically confirmed 2026-04-25.** See "Phase 0 smoke test" below. | Hand-rolled smoke test on Perlmutter (`salloc` + scheduler-file + login-node `Client(...)`) returned a compute-node hostname. |
| Max walltime on Perlmutter regular QoS? | 24h. | docs.nersc.gov/jobs/policy/ |
| Anything Dask-specific to know about Perlmutter? | **Empirically: scheduler-file on `$PSCRATCH` (Lustre) works.** `$HOME`/`$CFS` not tested but documented as risky for Dask file locking. | NERSC + dask-jobqueue community guidance; smoke test 2026-04-25. |
| `dagster-dask` per-op resource tags (so heterogeneous CPU/GPU recipes share one cluster)? | Yes — Dask resource constraints + per-op tags. Workers declare `--resources GPU=1`. | dask.distributed resources docs |

## Decisions

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | Compute substrate | **Dask** (not Flux, not WorkQueue) | NERSC-blessed, no Cray PMI risk, no exotic conda deps, `dagster-dask` exists. We don't need Flux's hierarchy. |
| 2 | Dagster integration shape | **Use `dagster-dask`'s `dask_executor` with `cluster: existing`** | Already an official Dagster library. Zero new Dagster glue. |
| 3 | Pilot lifecycle | **Decoupled from `lc run`. Explicit `lc pilot start/stop`.** | The whole point — queue wait paid once, then unlimited fast iteration. |
| 4 | Pilot rendezvous | **Dask scheduler-file at `<scratch_root>/lightcone/pilots/<name>.json`** | Standard Dask pattern; survives login sessions; no service discovery infra; orchestrator and workers see same path. |
| 5 | Recipe execution unit | **Container shelled out from a Dask task** | Reproducibility, heterogeneity, isolation. Container runtime is per-site config. |
| 6 | Pilot scope | **One pilot per `--target` name; mixed CPU/GPU workers within a pilot** | Heterogeneous recipes share one allocation via Dask resource tags. Two pilots only if a user wants strict isolation. |
| 7 | Where the pilot manager lives | **`lightcone.engine.slurm_pilot` (in this repo, v1); targeted extraction as standalone `dagster-slurm-pilot` package once a second consumer materializes** | Generic-SLURM module from day one, NERSC-named only in user-facing site config. v1 lives in-repo to avoid premature packaging; the public name is committed up front so file paths, CLI strings, and docs don't churn at extraction time. |
| 8 | CLI surface | **New `lc pilot {start,stop,status,logs}` group** | Pilots are a thing the user manages, like a venv or a docker-compose stack. |
| 9 | Site abstraction | **`SiteConfig` with built-in `perlmutter` config; pluggable for others** | Mirrors today's `lightcone.engine.site_registry` pattern. Adding a site is a config PR, not an architecture change. |
| 10 | What to delete | **`lightcone.engine.parsl_backend`, `parsl` + `ndcctools` deps, `docs/hpc/parsl-pilot.md`** | Greenfield rewrite. The Parsl branch was the right experiment; this is what ships. |
| 11 | Walltime expiration | **Pilot dies cleanly at walltime cap; `lc run` against a dead pilot fails fast with "pilot expired, run `lc pilot start` to renew"** | Explicit > implicit. Auto-renewal is future work. |
| 12 | No Pipes for v1 | Asset bodies are `subprocess.run([<runtime>, "run", ...])`; exit code is the signal | Recipes are containers, not Dagster-aware Python. Pipes is gravy if recipes ever want richer telemetry. |
| 13 | First-deployment bar | **Perlmutter integration tests must pass before any release** | Generic by design, Perlmutter-verified by policy. |

## Architecture

### The pilot itself

The pilot is a single SLURM job whose payload is "run a Dask scheduler on the head node, run Dask workers on every node, write a scheduler-file the orchestrator can find, then idle until walltime."

Generated sbatch script (simplified, with site-config substitution):

```bash
#!/bin/bash
#SBATCH --job-name=lc-pilot-<target>
#SBATCH --nodes=<nodes>
#SBATCH --time=<walltime>
#SBATCH --qos=<qos>
#SBATCH --account=<account>
#SBATCH [<other site-specific SBATCH flags>]
#SBATCH --output=%x-%j.out

set -euo pipefail

# Site-specific bootstrap (from target.worker_init).
# This installs the *orchestrator* env (python + dask + lightcone).
# Per-recipe dependencies live in container images, not here.
<worker_init>

# Rendezvous file on shared filesystem — the same path the
# orchestrator on the login node will read. The same-filesystem
# assumption is load-bearing here.
SCHED_FILE="<scratch_root>/lightcone/pilots/<target>.json"
mkdir -p "$(dirname "$SCHED_FILE")"

dask scheduler \
    --scheduler-file "$SCHED_FILE" \
    --port 8786 \
    --dashboard-address :8787 &
SCHED_PID=$!

# One worker per node, picking up scheduler from the file.
# --resources lets recipes target GPU vs CPU workers via Dask tags.
srun --ntasks=$SLURM_NNODES --ntasks-per-node=1 \
     dask worker \
        --scheduler-file "$SCHED_FILE" \
        --nworkers $WORKER_THREADS_PER_NODE \
        --memory-limit "$WORKER_MEMORY" \
        --local-directory "<scratch_root>/lightcone/pilots/scratch" \
        --resources "$WORKER_RESOURCE_TAGS" &

# Hold the allocation until the scheduler dies (manual stop) or
# walltime expires.
wait $SCHED_PID
```

The scheduler-file is the standard Dask format. We don't invent a wire protocol; we use the one Dask already has.

### The pilot manager

A small module — `lightcone.engine.slurm_pilot` — that knows how to:

| Command | Behavior |
|---|---|
| `lc pilot start <target>` | Renders sbatch from target + site config; `sbatch`es it; records job ID + scheduler-file path in `~/.lightcone/pilots/<target>.state.json`; returns immediately. |
| `lc pilot status [<target>]` | Reads pilot state; checks job state via `squeue`; if running, opens the scheduler-file and reports worker count, idle/busy, walltime remaining. |
| `lc pilot stop <target>` | `scancel`s the job; removes scheduler-file and state. |
| `lc pilot logs <target>` | Tails the SLURM job's `.out` file. |
| `lc pilot wait <target>` | Block until pilot is RUNNING and Dask scheduler is reachable. |

State file (`~/.lightcone/pilots/<target>.state.json`) is the source of truth on the orchestrator side:

```json
{
  "target": "perlmutter",
  "job_id": "12345678",
  "submitted_at": "2026-04-25T10:00:00Z",
  "walltime_seconds": 86400,
  "scheduler_file": "/pscratch/sd/u/user/lightcone/pilots/perlmutter.json",
  "site": "perlmutter"
}
```

### How `lc run` uses the pilot

```python
# lightcone.engine.runner._run_slurm (after migration)
def _run_slurm(self, recipe, ...):
    from lightcone.engine.slurm_pilot import find_active_pilot
    from dagster_dask import dask_executor

    pilot = find_active_pilot(self.target_config)
    if pilot is None:
        raise ClickException(
            f"No active pilot for target '{self.target}'. "
            f"Start one with: lc pilot start --target {self.target}"
        )

    executor = dask_executor.configured({
        "cluster": {"existing": {"address": pilot.scheduler_address}}
    })

    # Build Dagster definitions with this executor; rest of the
    # runner is unchanged.
    ...
```

That's it. **Two imports, one config dict.** No custom executor, no Pipes client, no message-reader plumbing.

### Asset body (the container handoff)

Each Dagster asset's body is the container invocation we already build today in `assets.py`:

```python
def _materialize_recipe(context, ...):
    cmd = build_recipe_shell_command(recipe, ...)
    # cmd typically begins with: <container-runtime> run --rm <image-sha> ...
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception(f"recipe failed: {result.stderr}")
    return Output(metadata={"image": ..., "exit_code": 0})
```

When `dask_executor` runs this op, Dagster pickles the function, ships it to a Dask worker on a compute node, the worker runs it, the function shells out to the container runtime. The container is pulled (or already present) on the compute node; the recipe runs inside; data flows in/out via shared-filesystem mounts the container declares.

This shows why the same-filesystem assumption is load-bearing: the path the worker reads recipe inputs from must equal the path the orchestrator writes them to.

### Per-step resource tags (CPU vs GPU)

Recipes that need a GPU annotate the Dagster op; the `dask_executor` propagates it as a Dask task constraint:

```python
@asset(op_tags={"dagster-dask/resource_requirements": json.dumps({"GPU": 1})})
def my_gpu_recipe(...): ...
```

The pilot brings up workers with mixed resources:

```bash
srun --nodes=$((SLURM_NNODES-1)) dask worker --resources ""        ...
srun --nodes=1                   dask worker --resources "GPU=4"   ...
```

This expresses the same routing intent that the current PR's `pick_executor` solves, in Dask's native vocabulary.

## Configuration model

```yaml
# ~/.lightcone/targets/perlmutter.yaml
backend: slurm
site: perlmutter            # picks SiteConfig defaults
container_runtime: podman-hpc   # site default; override per target if needed
account: m1234

# Single pilot for this target, mixed CPU/GPU workers
pilot:
  nodes: 4
  walltime: 24h
  qos: regular
  constraint: cpu
  worker_init: |
    # Orchestrator-side bootstrap only — recipe deps live in containers.
    module load python
    source $HOME/.lightcone/envs/perlmutter/bin/activate
  workers:
    - nodes: 3
      threads_per_node: 64
      memory: 200GB
      resources: {}              # CPU pool
    - nodes: 1
      threads_per_node: 4
      memory: 200GB
      resources: {GPU: 4}        # GPU pool

# Optional override of site default; usually omitted
# scratch_root: $PSCRATCH
```

Single-pool case (very common — pure CPU analyses) collapses to:

```yaml
backend: slurm
site: perlmutter
account: m1234
pilot:
  nodes: 4
  walltime: 24h
  qos: regular
  worker_init: |
    module load python
    source $HOME/.lightcone/envs/perlmutter/bin/activate
```

`SiteConfig.perlmutter()` (in code) provides defaults for `scratch_root`, `container_runtime`, typical `worker_init`, and known QoS limits, so users only fill in account and sizing. Adding a new site (e.g., `SiteConfig.expanse()`) is a single PR — no architectural change.

## User experience

```bash
# Once per session (or per day)
$ lc pilot start --target perlmutter
queued: job 12345678  (4 nodes, 24h walltime, qos=regular)
waiting for scheduler ........ ready
pilot 'perlmutter' is live (4 nodes, 256 cores, 4 GPUs)

$ lc pilot status
TARGET       STATE    NODES  WALLTIME-LEFT  WORKERS  IDLE
perlmutter   RUNNING  4      23h 47m        4        4

# Iterate freely — no queue wait
$ lc run --target perlmutter
✓ asset_a    (1.2s queued, 84s wall)
✓ asset_b    (0.4s queued, 12s wall)

$ lc run --target perlmutter        # again — instant
$ lc run --target perlmutter        # and again

# When done
$ lc pilot stop --target perlmutter
scancelled job 12345678
```

## Failure modes

| Failure | Detection | User-facing message |
|---|---|---|
| `lc run` with no pilot | `find_active_pilot` returns None | "No active pilot for '<target>'. Start one with: `lc pilot start --target <target>`" |
| Pilot still queued | `squeue` shows PENDING | "Pilot is queued (position N). `lc pilot wait` to block, or `lc pilot stop` to cancel." |
| Pilot RUNNING but scheduler not ready | scheduler-file missing | Wait up to 30s with backoff, then "Pilot started but scheduler not ready — check `lc pilot logs`" |
| Pilot died (walltime, OOM, scancel) | `squeue` shows COMPLETED/FAILED | "Pilot '<target>' is dead. `lc pilot start` to renew." Stale scheduler-file removed. |
| Dask client cannot connect | TCP failure | "Cannot reach scheduler at <addr> — pilot may be unhealthy. `lc pilot logs`." |
| Recipe fails inside container | container runtime non-zero exit; Dask raises; Dagster surfaces | Standard Dagster error path; container exit code in the message. |

---

## Deploying on NERSC Perlmutter

This section is the contract for our first user. Everything in here is Perlmutter-specific; the design above is not. If we ever ship without this section verified, we have a regression.

### Site-specific values

| Setting | Perlmutter value | Why |
|---|---|---|
| `scratch_root` | `$PSCRATCH` | Lustre. See "Filesystem gotcha" below — this is non-negotiable for the scheduler-file. |
| `container_runtime` | `podman-hpc` | NERSC's blessed container runtime; pre-installed on compute nodes. |
| `worker_init` | `module load python; source $HOME/.lightcone/envs/perlmutter/bin/activate` | NERSC's Python is module-loaded; user's lightcone env activates over it. |
| `qos` (default) | `regular` | 24h walltime cap; appropriate for long-running pilots. |
| `qos` (debugging) | `debug` | 30 min cap, faster queue — for `lc pilot start` smoke tests. |
| `constraint` | `cpu` or `gpu` | Selects between CPU-only and GPU node pools. |
| `account` | user-supplied (`m1234` etc.) | Required by SLURM at NERSC; no default. |

`SiteConfig.perlmutter()` ships these as defaults.

### Filesystem gotcha: scheduler-file MUST live on `$PSCRATCH`

Dask's scheduler-file uses POSIX file locking. On Perlmutter:

- **`$PSCRATCH`** (Lustre) — supports file locking; **this is where the scheduler-file goes.**
- **`$HOME`** — does **not** reliably support the locking Dask requires.
- **`$CFS`** — community filesystem; locking semantics are not guaranteed for this workload.

Symptom of getting this wrong: pilot starts, scheduler appears to come up, but `dask worker` fails to register or the orchestrator's `Client(scheduler_file=...)` hangs/errors. We default to `$PSCRATCH` and refuse to use a non-Lustre path on Perlmutter without an explicit override.

### Walltime ceiling: 24h on regular QoS

Perlmutter regular QoS caps walltime at 24h. This is the upper bound on a single pilot's lifetime. After 24h:

- Pilot dies cleanly (SLURM signals, scheduler exits, allocation released).
- `lc pilot status` reports the pilot as dead with the reason.
- Next `lc run` fails with the "pilot expired, run `lc pilot start` to renew" message.

Auto-renewal (chained `--dependency=afterany:<old-jobid>` sbatch) is **out of scope for v1** but documented as a future enhancement. For long unattended runs, the user's options today are: (a) cron `lc pilot start` on a schedule, (b) wait for v1.1 auto-renewal, (c) request a non-default QoS with longer walltime if their account supports it.

### QoS pre-flight validation

Today's `parsl_backend.validate_pilots_against_qos` queries Perlmutter's QoS table (cached locally via `lc target refresh`) and checks `(nodes, walltime)` against per-QoS limits. That logic ports over to `slurm_pilot` unchanged — pilot pre-flight runs the same checks against the same cache before sbatch.

The cluster info cache lives at `~/.lightcone/clusters/perlmutter.json`. Other sites populate equivalent caches; the validation is generic SLURM, not NERSC-specific.

### Network: login → compute TCP works

NERSC documents (CUG 2022) that every Perlmutter node — login and compute alike — has a routable IP on the high-speed Ethernet fabric. Login-node Dask client → compute-node Dask scheduler over plain TCP works. We do not need an SSH tunnel.

This is verified in **Phase 0** below. Sites that don't share this property would need a different rendezvous strategy; we make no claim there.

### Container runtime: `podman-hpc` specifics

- Pre-installed on every Perlmutter compute node.
- `podman-hpc run` is invoked from inside the Dask asset body — same as today's docker/podman path.
- Image pulls happen on first use per node; recipe authors should pin image digests.
- `$PSCRATCH` and `$CFS` paths are typically auto-mounted into the container; verify per recipe.
- GPU recipes need `--gpu` flag (or the equivalent) to expose the Perlmutter GPUs into the container.

### Workflow nodes

NERSC provides dedicated workflow nodes for long-running orchestrators that don't belong on a login node. For users running pilots that span days, the orchestrator process should live on a workflow node. The plugin doesn't care which host it runs on as long as the shared-fs precondition holds; we document the workflow-node option in the user guide.

### Phase 0 smoke test — PASSED 2026-04-25

A hand-rolled validation we ran on Perlmutter before writing plugin code:

```bash
# On Perlmutter
salloc -N 2 -t 30 -q debug --account=<account>

# On the head compute node (in salloc shell):
SCHED=$PSCRATCH/lc-test/sched.json
mkdir -p $(dirname $SCHED)
module load python
source <env>
dask scheduler --scheduler-file $SCHED --port 8786 &
srun --ntasks=2 --ntasks-per-node=1 dask worker --scheduler-file $SCHED &

# On a separate login-node shell:
python -c "
import os
from dask.distributed import Client
import socket
c = Client(scheduler_file=os.path.expandvars('\$PSCRATCH/lc-test/sched.json'))
print(c.gather([c.submit(socket.gethostname) for _ in range(4)]))
"
```

**Result:** the login-node Python returned compute-node hostnames. Every load-bearing assumption in the design held empirically:

- **`$PSCRATCH` file locking works** for the Dask scheduler-file rendezvous.
- **Login → compute TCP works** over Perlmutter's high-speed Ethernet fabric — no SSH tunnel required.
- **`dask worker` bootstraps cleanly** from a scheduler-file written by a separate scheduler process on the head compute node.
- **`dask_executor`'s underlying connection model** (which is what `cluster: existing` uses under the hood) is therefore confirmed reachable from a login-node orchestrator.

**Practical gotcha learned during smoke test:** when invoking `Client(scheduler_file=...)` from a Python REPL or script, the path must be expanded by Python (`os.path.expandvars` or a literal). Python won't expand `$PSCRATCH` inside a string literal the way bash does — passing `'$PSCRATCH/sched.json'` causes `Client` to silently wait/poll for a file that will never appear. The pilot manager (Phase 1) handles this correctly inside the sbatch script, but it's worth flagging in user-facing docs for anyone running pieces by hand.

This was the only real unknown in the design. With it cleared, the rest of the architecture is unblocked.

### CI: Perlmutter integration tests block release

A nightly (or pre-release) CI job on a NERSC workflow node runs the full integration suite against a real Perlmutter pilot:

- `lc pilot start --target perlmutter --qos debug` against a tiny test allocation.
- A representative ASTRA analysis with one CPU recipe and one GPU recipe.
- `lc pilot status` and `lc pilot stop`.

Releases that don't pass this suite don't ship. This is the difference between "generic by design" and "Perlmutter-verified by policy."

---

## Phased delivery

| Phase | Scope | Exit criteria |
|---|---|---|
| **0. Smoke test on Perlmutter** ✅ | The bash sequence in "Deploying on NERSC Perlmutter" above. | **PASSED 2026-04-25** — login → compute TCP works; `$PSCRATCH` Lustre locking works for the scheduler-file. |
| **1. Pilot manager** | `lightcone.engine.slurm_pilot`: `start_pilot`, `stop_pilot`, `pilot_status`, `find_active_pilot`. sbatch script renderer. State file. | `lc pilot start/stop/status` works manually against Perlmutter. |
| **2. `lc run` ↔ pilot integration** | Rewrite `_run_slurm` to use `dagster-dask` + active pilot. Drop Parsl/ndcctools. | One ASTRA recipe materializes through the pilot via the configured container runtime. Round-trip < 1s for a no-op asset. |
| **3. Heterogeneous workers** | Multi-pool `pilot.workers` schema; resource-tag routing; cpu/gpu split tested on Perlmutter. | A mixed CPU+GPU analysis runs end-to-end. |
| **4. QoS pre-flight + ergonomics** | Port `validate_pilots_against_qos` to validate against the cluster cache before sbatch. Rich CLI output. `lc pilot logs --follow`. | Bad QoS/walltime combos fail before sbatch. |
| **5. Perlmutter CI** | Nightly integration job on a NERSC workflow node. | Release gate is in place. |
| **6. Docs + cleanup** | `docs/hpc/dagster-slurm-pilot.md` + Perlmutter quickstart. Delete `parsl_backend.py`, drop deps, retire `docs/hpc/parsl-pilot.md`. | Zero references to Parsl/WorkQueue in shipped code or docs. |
| **7. (Future) Auto-renewal** | `lc pilot renew` chains a successor with `--dependency=afterany:<old>`. | Out of v1 scope. |
| **8. (Future) Second site** | A non-NERSC SLURM site (e.g., a university cluster) added via a `SiteConfig.<name>()` PR. | Validates the generic-SLURM claim with a real second consumer. |

## What stays vs. what goes

**Preserved verbatim:**
- The user-facing `lc run` CLI.
- The agent-target contract in `CLAUDE.md` (agent picks `--target`, not per-axis flags).
- ASTRA recipes — they don't change at all. They're still container-runtime shell commands.
- The site registry pattern in `lightcone.engine.site_registry`.
- QoS pre-flight validation logic (new caller).

**Goes away:**
- `lightcone.engine.parsl_backend` (~350 lines).
- The `parsl` and `ndcctools` dependencies.
- Today's multi-pilot `pilots:` schema (replaced by simpler `pilot:` schema with optional `workers:` list — migration path documented).
- `docs/hpc/parsl-pilot.md` (replaced).

**New:**
- `lightcone.engine.slurm_pilot` (~300 lines estimated; structured as a self-contained module so extraction to standalone `dagster-slurm-pilot` is a `git mv` + namespace rename, not a redesign).
- `dagster-dask` and `distributed` dependencies (mainstream — both conda-forge and pip).
- `lc pilot` CLI group.
- `docs/hpc/dagster-slurm-pilot.md` + Perlmutter-specific quickstart.

## Open questions

1. ~~**Phase 0 outcome on Perlmutter.**~~ **Resolved 2026-04-25** — smoke test passed. Login → compute TCP and `$PSCRATCH` Lustre locking both work; no SSH-tunnel fallback needed.
2. **`podman-hpc` from inside a Dagster asset run on a Dask worker.** Should be a no-op — the worker is just a Python process on a compute node, podman-hpc is on PATH there. Worth confirming in Phase 2.
3. **Pilot sharing across users.** Out of scope for v1. Sharing would need an auth story Dask doesn't natively provide.
4. **Auto-renewal at the walltime cap.** Documented as future work; v1 tells the user.
5. **When does the pilot manager become its own `dagster-slurm-pilot` package?** Public name is committed; extraction timing is not. v1 ships in-repo; we extract when a second consumer materializes (sister project, open source release, or a request to upstream into `dagster-io/community-integrations`). The in-repo module is structured as a clean unit so extraction is a mechanical move, not a rewrite.
6. **Generic-SLURM validation beyond Perlmutter.** A non-NERSC site as Phase 8. Until that lands, "generic SLURM" is a design property, not an empirical claim.

## References

- [`dagster-dask` configuration](https://docs.dagster.io/deployment/execution/dask) — `cluster: existing` is documented; this is the load-bearing API.
- [Dask on HPC (manual scheduler+workers pattern)](https://docs.dask.org/en/stable/deploying-hpc.html)
- [`dask-jobqueue` SLURMCluster](https://jobqueue.dask.org/en/latest/generated/dask_jobqueue.SLURMCluster.html) — reference for SLURM-side bootstrap details.
- [Dask resources / per-task constraints](https://distributed.dask.org/en/latest/resources.html)
- [NERSC queue policies (24h walltime cap)](https://docs.nersc.gov/jobs/policy/)
- [NERSC Perlmutter running jobs](https://docs.nersc.gov/systems/perlmutter/running-jobs/)
- [Network Integration of Perlmutter (CUG 2022)](https://cug.org/proceedings/cug2022_proceedings/includes/files/pap117s2-file1.pdf) — confirms routable IPs across login + compute on the same fabric.
- [`dagster-slurm` (community plugin)](https://github.com/ascii-supply-networks/dagster-slurm) — closest prior art; assumptions diverge (see comparison table above).
- [Dagster SLURM integration listing](https://docs.dagster.io/integrations/libraries/slurm)
