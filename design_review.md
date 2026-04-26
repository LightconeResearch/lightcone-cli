# Design Review: Execution & Provenance in lightcone-cli

> Working document. Captures what we actually need from the execution layer, then critiques the current design against those needs and outlines a path forward.

---

## Part 1 — What matters to us

The execution layer of `lightcone-cli` exists to materialize the outputs declared in `astra.yaml`. Everything below describes properties we want from that layer, ordered roughly by how non-negotiable they are.

### 1.1 Verifiable execution (the integrity property)

> For every result file in `results/<universe>/<output>/`, we must be able to prove how it was produced.

Concretely:

- **The file was produced by `lc run`**, not dropped by an LLM agent, copied from elsewhere, or hand-edited.
- **By which version of the recipe code** — the exact command/script content used.
- **In which container / environment** — image hash, not just a tag.
- **From which inputs** — the exact upstream artifacts (and their content), not just their declared identity.
- **With which decision parameters** — the universe's parameter values, hashed.

The failure mode we are designing against is: an agent, working interactively, writes plausible-looking files into `results/` and claims success. Today the only thing standing between us and that failure mode is a process boundary: "the agent only has access to `lc run`." That's a policy, not a guarantee. We want a guarantee that survives an agent (or a human) trying to fabricate results.

The right shape of guarantee is **content-addressed provenance**: each output is bound to a hash chain that goes back to (a) the project commit, (b) the recipe code, (c) the container image, (d) the input file hashes, (e) the bytes on disk. A fabricated file fails the chain.

### 1.2 Reproducibility

> Re-running the same recipe with the same inputs and the same code should produce the same output, and we should be able to detect when any of those preconditions changes.

Specifically we need:

- **Staleness detection.** If a recipe script changes, or its container changes, or one of its inputs changes, downstream outputs are stale. The system must surface this without the user having to track it manually.
- **Deterministic container builds.** A given Containerfile + dependency files must produce a deterministic image tag. This is already done well (`engine/container.py:compute_image_tag`); the property carries forward.
- **Inputs as first-class.** External file inputs declared in `astra.yaml` must be observed (hashed or fingerprinted) so that changes to them invalidate downstream.

Reproducibility is weaker than verifiability — verifiability is about "did this happen the way we say"; reproducibility is about "could it happen again." We want both.

### 1.3 Transparent, agent- and human-friendly usability

> The user (and the agent acting on the user's behalf) interacts with the system through a small set of obvious commands. The mental model is: "outputs are things; `lc run` materializes them; `lc status` tells you what's done."

Commands we should be able to keep small and obvious:

- `lc run [outputs...]` — materialize outputs, resolving the dependency graph.
- `lc status` — what's materialized, what's stale, what's missing, what's broken.
- `lc verify` — the integrity check, separate from status.
- `lc cluster …` — when (and only when) HPC execution is needed.

Things we want to **avoid** in the surface:

- A deployed platform. We are explicitly per-project, no shared infrastructure.
- Long-running daemons in the user's path. The user runs commands; commands return.
- Concepts that don't map to ASTRA primitives. The user thinks in outputs, recipes, universes, decisions — not in assets, ops, jobs, sensors, partitions, run queues.

### 1.4 Frictionless execution across local, HPC, and (eventually) Kubernetes

> A recipe should run the same way on a laptop, a SLURM allocation, and a Kubernetes pod, with the only configuration being a `--target` choice.

The hard constraints:

- **No mandatory services that the user has to install or operate.** The system should bundle what it needs or degrade gracefully.
- **Shared filesystems that don't support POSIX locking semantics (NFS, Lustre, GPFS) must be tolerated**, not just on the data path but on any state we persist.
- **Per-project state, not per-user.** Two projects on the same machine must not collide. State should live under the project directory by default.
- **Cluster lifecycle should not be the user's burden.** Starting/stopping the cluster should be optional; a single `lc run` on HPC should "just work" within an allocation, even if persistent clusters are also supported.

### 1.5 The `astra.yaml` invariant

> `astra.yaml` is the only source of truth. The execution layer is a function of the spec.

This means:

- All inputs, outputs, recipes, decisions, containers come from the spec.
- Output paths are derived (`results/{universe_id}/{output_id}/`) — no customization.
- Per-recipe overrides exist only where the spec allows them.
- The execution layer must not require parallel state files that drift from the spec.

This is well established in the current design and we want to preserve it.

### 1.6 Auditability and offline introspection

> A finished project (or a finished universe within a project) should be auditable without the execution layer running.

A reviewer should be able to clone the project, look at `results/` plus any provenance metadata co-located with the outputs, and answer: was this output produced from this code, with these inputs, on this date? Without booting a database, without launching a webserver, without hitting any external service.

This pushes provenance toward **on-disk artifacts co-located with the output**, not toward a database that must be live to be useful.

---

## Part 2 — Review of the current design

The current execution layer (on the `dask` branch) is built on Dagster + Dask + bundled Postgres. This section evaluates it against the requirements above.

### 2.1 What the current design gets right

- **`astra.yaml` as source of truth** is honored. Asset definitions are derived (`engine/assets.py:374-443`), output paths are conventional (`engine/io_manager.py`), per-recipe overrides work.
- **Container builds are deterministic.** `engine/container.py:compute_image_tag` hashes Containerfile + dependency files. This is exactly the right primitive and we should build on it, not replace it.
- **Backend dispatch is clean.** Docker / podman / venv / local fall back gracefully (`engine/runner.py:189-218`). The recipe doesn't know which backend ran it.
- **HPC cluster lifecycle is real.** `lc cluster start/attach/stop` (`engine/clusters/`) handles the SLURM + scheduler + storage stack — this is hard work that mostly works.
- **The CLI surface is small.** `lc run`, `lc status`, `lc cluster` are the user-facing verbs. Dagster's UI exists (`lc dev`) but is optional.

### 2.2 Where the current design falls short

#### 2.2.1 Provenance is weak

Today, "was this output materialized?" is answered by querying the Dagster event log:

```python
instance.get_latest_materialization_events([asset_keys])  # status.py:98
```

The materialization event records `exit_code`, `output_path`, and `backend` (`engine/assets.py:363-369`). It does **not** record:

- A hash of the recipe code (the command string, the script file content)
- A hash of the inputs consumed
- A hash of the bytes actually written to disk

The event and the file on disk are **separable**. A Postgres row that says "asset K was materialized" does not prove the file at `results/<u>/<o>/` is the file that materialization wrote. An agent that writes to `results/` does not invalidate any row. A row that exists does not require the file to exist.

This means the integrity property in §1.1 is currently delivered only by the process boundary "the agent only has `lc run`" — not by the data model.

#### 2.2.2 Staleness is invisible

There is no notion of code version or data version on assets. `code_version` is unset on every `@asset`, which (per Dagster docs) means Dagster falls back to using the run id as the code version — so every run looks like a code change, defeating any built-in staleness logic. We're not querying `instance.get_latest_materialization_code_versions(...)`.

Consequence: if a recipe script is edited, downstream outputs do not show as stale. The user has to know.

External file inputs are declared as inert `AssetSpec`s with `external: true` (`engine/assets.py:101-106`). Dagster has no observation hook for them, so when the user changes an input file, nothing flags downstream as stale.

#### 2.2.3 Validation is ad hoc

The asset body calls `validate_output()` from `engine/validation.py` and emits warnings via `context.log.warning`. There is no first-class concept of "this output passed its checks." `lc status` cannot distinguish "materialized" from "materialized but failed validation." `lc verify` is implemented as a Claude skill that re-derives integrity from scratch by reading files and shelling out to `lc status`.

This is the right place for `@asset_check` and we don't use it.

#### 2.2.4 Postgres is mandatory and operationally painful on HPC

Postgres became required on the `dask` branch because SQLite + shared HPC filesystems is broken (correctly diagnosed). The bundled solution is `pixeltable-pgserver`, with the socket forced onto `/tmp` because home filesystems reject `chmod` on socket files (commits `78e90f6`, `80792e8`).

The cost:

- Every project carries a Postgres data directory at `.lightcone/pg/`.
- Every `lc run` (ephemeral mode) spins up and tears down a Postgres daemon.
- The cluster lifecycle becomes "scheduler + workers + Postgres" — three services to keep alive, instead of one.
- We've added a system dependency (libpq, glibc constraints from pixeltable-pgserver wheels) to a tool that otherwise has no system requirements.

Worth noting: this pain is **downstream of one design decision** — using Dagster's event log as the source of truth for materialization status. If provenance lived next to the artifacts (as on-disk manifests), the event log becomes secondary, and SQLite (per-run sharded by default; not the contention point we feared) may suffice.

#### 2.2.5 Dagster is doing a fraction of what it ships

We use: `@asset`, `AssetKey`, `AssetSpec`, `Definitions`, `MaterializeResult` (metadata only — no `data_version`), `define_asset_job`, `execute_job`, `DagsterInstance.get_latest_materialization_events`, `reconstructable()`, dagster-dask.

We do **not** use: `code_version`, `DataVersion`, `@asset_check`, `@observable_source_asset`, partitions, sensors, schedules, IO managers (the file at `engine/io_manager.py` is a 28-line path helper not registered with Dagster), resources, run tags, structured `MetadataValue` types, `AutomationCondition`, executors other than `dask_executor` and the implicit `in_process_executor`.

That doesn't mean Dagster is wrong; it means we are using it as a thin DAG executor and event log. The features we *aren't* using are exactly the features that would deliver §1.1, §1.2, §1.3 — which is the point of this review.

#### 2.2.6 Cluster lifecycle complexity

`lc cluster start/attach/stop` is necessary because the Dagster + Dask + Postgres stack assumes a persistent set of services across runs. If we didn't need a persistent event-log database, and if we used the in-allocation `multiprocess_executor` for parallelism, the cluster command would collapse to "get a SLURM allocation."

The current attach mode (run inside `salloc`, spawn scheduler + workers + Postgres in the allocation) is a good design *given* the stack we have. The question is whether the stack should be slimmer.

#### 2.2.7 In-memory vs filesystem asset passing — non-issue, but worth naming

Dagster's asset model assumes Python values flow between assets. We deliberately bypass this: scripts write to known filesystem paths, downstream scripts read from those paths. The asset body returns `MaterializeResult` with no `value`. The IO manager is a path helper, not a Dagster IO manager.

This is correct for our domain (large outputs, multi-language scripts, container isolation). We should stop calling the file at `engine/io_manager.py` an "IO manager" — it's a path resolver, and the name implies a Dagster contract we're not implementing.

### 2.3 The shape of the fix

The good news: most of what we need exists in Dagster and we aren't using it. The headline change is to switch from "Dagster as orchestrator + opaque event log" to **Dagster as a content-addressed materialization layer** by adopting `code_version` + `DataVersion` + asset checks + observable source assets.

This is also independently useful: even if we eventually replaced Dagster with a custom executor, the same content-addressed model would carry over. The work isn't Dagster-specific lock-in; it's the right model for verifiable research artifacts.

#### 2.3.1 Recommended changes, in priority order

**1. Set `code_version` on every asset; return `DataVersion` from every materialization.**

```python
code_version = sha256(recipe_command + container_image_tag + universe_params_json)

@dg.asset(code_version=code_version)
def _asset(context) -> dg.MaterializeResult:
    result = runner.execute(...)
    manifest_hash = sha256_of_output_dir(out_dir)
    return dg.MaterializeResult(
        metadata={...},
        data_version=dg.DataVersion(manifest_hash),
    )
```

**Delivers §1.1 and §1.2.** The materialization event in the event log now cryptographically commits to the code, the inputs (via upstream `data_version` propagation), and the bytes on disk. An agent dropping a file produces no event. A code edit produces a new `code_version` and `lc status` can surface staleness via `instance.get_latest_materialization_code_versions(...)` without any daemon.

Estimated cost: ~80–150 lines in `assets.py` + a new `provenance.py` module for hashing.

**2. Write a co-located manifest file per output.**

In addition to the Dagster event log, write `results/<u>/<o>/.lightcone-manifest.json` containing the same data — `code_version`, `data_version`, container image, input data versions, run id, git sha, timestamp. The manifest is signed by the data version (the manifest's content hash is the data version).

**Delivers §1.6.** Auditability survives losing the database. The manifest is the on-disk projection of the materialization event. `lc verify` becomes "for each output, recompute the data version and check it against the manifest, then check the manifest against the (optional) event log."

Estimated cost: ~50 lines.

**3. Add `@asset_check(blocking=True)` for manifest signature verification.**

```python
@dg.asset_check(asset=key, blocking=True)
def manifest_signature(context):
    recorded = read_manifest(out_dir)
    actual = sha256_of_output_dir(out_dir)
    return dg.AssetCheckResult(passed=(recorded == actual), ...)
```

**Delivers the agent-faked-file detection** as a first-class mechanism in the event log, and gates downstream recipes on integrity. Run automatically as part of materialization (`check_specs=[...]` on the asset) and also runnable standalone via `lc verify`.

Estimated cost: ~30 lines plus extending `lc verify`.

**4. Convert external inputs to `@observable_source_asset`.**

```python
@dg.observable_source_asset(key=key)
def _obs():
    return dg.DataVersion(f"{path.stat().st_mtime_ns}-{path.stat().st_size}")
```

Run via `dg.materialize([obs_assets])` at the start of `lc run` (or as a separate `lc observe-inputs` step).

**Delivers external-input staleness propagation** — currently invisible.

Estimated cost: ~40 lines, replacing the `AssetSpec(metadata={"external": True})` pattern.

**5. Tag runs and use structured metadata.**

```python
dg.materialize(..., tags={"git_sha": ..., "lc_version": ..., "cluster_id": ..., "permission_tier": ...})
metadata = {
    "command": MetadataValue.md(f"```\n{command}\n```"),
    "container": MetadataValue.text(container_tag),
    "output_path": MetadataValue.path(str(out_dir)),
    "manifest": MetadataValue.json(manifest_dict),
}
```

**Delivers cross-run queries** ("show all outputs from commit X") and richer `lc status` output. Tiny change, big provenance win.

Estimated cost: ~20 lines.

**6. Spike split-form SQLite storage on HPC; drop Postgres if it works.**

`SqliteEventLogStorage` is per-run sharded — concurrent runs do not contend. The contention may be in run storage (single file). Configure storage in legacy split form:

```yaml
run_storage:    # local-disk
event_log_storage:    # shared-FS, per-run sharded
schedule_storage:    # rarely written
```

If this works on the production HPC site, drop `pixeltable-pgserver`, `dagster-postgres`, the socket-on-`/tmp` workaround, and the persistent-Postgres lifecycle. Big simplification of §1.4.

Estimated cost: 1 day spike + ~50 lines of config; conditional adoption.

**7. Add `multiprocess_executor` for the local-no-cluster path.**

Today the no-cluster path uses Dagster's default `in_process_executor` (serial). Switching to `multiprocess_executor` gives workstation users free intra-asset parallelism without bringing Dask into the picture for local work.

Estimated cost: ~15 lines.

**8. Rename `ASTRAIOManager` → `OutputPathResolver`.**

It's not an IO manager. The name advertises a Dagster contract we're not implementing. Drop the misleading name; the class itself stays.

Estimated cost: trivial.

#### 2.3.2 Things to skip

- **Universes as partitions.** Daemon-required for multi-partition runs, breaks our key-as-path-prefix layout, awkward dynamic-add semantics. Revisit if a project ever has >100 universes.
- **Real Dagster IO managers.** Our scripts pass data via filesystem paths, not Python values. The current pass-through is correct; it just shouldn't claim to be an IO manager.
- **Declarative Automation / `AutomationCondition`.** Requires the daemon. We can express the same staleness logic ourselves on top of `code_version` + `DataVersion`.
- **`ConfigurableResource` for the runner.** Current "runner is a Python object passed at definition time" is fine; refactoring is gold-plating.
- **`celery_executor`.** Heavier than Dask, no benefit for our deployment story.

#### 2.3.3 Open questions

- **What goes into `code_version` for outputs that are aliases (no recipe, just reference a sub-analysis output)?** Probably the upstream's `code_version`, transitively. Needs a small design pass.
- **Hashing strategy for large external inputs.** sha256 of multi-GB files is expensive. Default to `(mtime, size)` fingerprint with an opt-in `--strict-input-hashes` flag for paranoid mode?
- **Universe params hashing.** Decision parameters are user-provided; do we canonicalize (sort keys) before hashing? Yes, but worth being explicit.
- **Should the manifest be signed cryptographically (e.g., with a project key) or is content-addressing sufficient?** Content-addressing prevents fabrication-after-the-fact; signing adds tamper-evidence over the manifest itself. Probably content-addressing is enough for v1.
- **Does `lc verify` need a `--strict` mode that re-runs assets and compares data versions?** That's the strongest possible check (recompute and compare), but expensive. Worth offering, not as default.

### 2.4 What this does *not* address

This review focuses on the execution and provenance layer. It does not address:

- The agent-side surface (skills, prompts, hooks). Those should be updated to use `lc verify` once it has a real integrity guarantee, but the agent layer is not in scope here.
- The cluster lifecycle UX itself, beyond the implication that dropping Postgres simplifies it. Whether `lc cluster` should exist at all (vs. always running ephemerally inside an allocation) is a separate design question that becomes more tractable once the stack is slimmer.
- The Dagster-vs-custom-executor question. With recommendations 1–5 in place, the case for staying on Dagster is stronger; the case for replacing it is also cleaner because the provenance model is no longer tangled with the orchestrator. Either way, we should do 1–5 first.

---

## Summary

The current design is a competent thin layer over Dagster that delivers DAG execution and a basic event log, at the cost of a heavy stack (Dagster + Dask + Postgres) on HPC. It does not deliver the verifiability property we actually care about; it delivers a process-boundary approximation of it.

The shortest path to the property we want is to **adopt the Dagster features we're already shipping but not using** — `code_version`, `DataVersion`, asset checks, observable source assets — combined with a co-located on-disk manifest. This delivers a real cryptographic provenance chain, makes `lc verify` meaningful, and makes the Postgres requirement potentially droppable. The code cost is on the order of a few hundred lines. The conceptual cost is low: the user-facing surface (`lc run`, `lc status`, `lc verify`) doesn't change.

After that work, the question "do we still need Dagster?" becomes a clean one to ask, because provenance no longer depends on the answer.
