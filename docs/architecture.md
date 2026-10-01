# Architecture

How lightcone-cli is put together, for someone about to change it. The
[user-guide concepts page](user/concepts.md) covers what the tool
promises; this page covers how the promises are kept.

## The split that everything else follows

```text
lc (CLI)                     engine                        ASTRA
─────────────                ─────────────────────         ─────────────
flags, rendering,     ──►    what a project is,     ──►    what a spec
exit codes                   how outputs are made          *means*
```

- **`cli/commands.py`** owns flags, console rendering, and exit codes —
  nothing else. It imports the engine *inside* command callbacks, so
  `lc --help` stays cheap. The engine never imports click and never
  prints.
- **The engine** owns everything about what a project is and how
  outputs get made. It raises `ProjectError`; the CLI's group class
  translates that into a clean error message, once, for every verb.
- **ASTRA** owns what a spec means. Scoping, `from:` references,
  conditional outputs, universe resolution, and the recipe placeholder
  grammar are all answered by `astra.resolve` and checked by
  `astra.validation` — never re-implemented here. When the spec's
  *meaning* looks wrong, the fix is a PR to astra-tools.

The engine ships as the `lightcone.*` PEP 420 namespace —
`src/lightcone/` has **no `__init__.py`**, so sibling distributions can
share the namespace. The engine is the host's `uv tool`, never a
project dependency: a project's lock carries only what the analysis
imports.

## One run, end to end

```text
lc materialize "$CLUSTER"
  │  guard: tools?  git identity?
  │  refuse: dirty tree
  │  plan: astra validate + resolve  →  Graph of Tasks
  │        (no tasks → converge the crate and stop; nothing connects)
  │  classify: current/behind outputs become values; other outputs may run
  │  connect: native identity + Dask readiness
  │  admit: resource requests for outputs that may run fit a worker
  │  fetch: git annex get (declared inputs not in this clone)
  │  converge: uv.lock ⇄ .venv   (and the image, containerized)
  ├─► workers: reset output file → sandbox → recipe → hash → manifest
  │            (never raise; return ok/current/behind/failed/blocked)
  └─  driver: consume results in one thread
        ok      → dataset.save   (commit + run record)
        failed  → dataset.restore (tree as clean as it started)
        then    → converge ro-crate-metadata.json (if licensed)
```

The division of labor is strict and load-bearing:

- **The driver owns git, alone.** Workers execute and return a
  `TaskResult`; the driver commits as results arrive, in one thread.
  Concurrent git operations race on the index lock — this split is not
  a preference.
- **Dask owns the ordering.** Tasks that may execute are submitted with their
  upstream futures or already-current values as arguments; there is no ready-set loop or
  hand-rolled topological sort on the execution path.
- **The worker never raises.** A recipe failure, a gate failure, an
  unreadable manifest — all come back as a state, so one failure
  doesn't abort every task in flight, and a run reports *all* its
  independent failures.
- **Dask accounts for task resources.** Workers advertise CPU, memory, and GPU
  budgets; submissions reserve the recipe's requirements. Omitted memory adds no
  RAM reservation. These coordinate scheduling rather than imposing per-recipe
  OS limits; the declared CPUs also size the recipe's numerical thread pools.
  Recipe `time_limit` is explicitly refused; allocation walltime remains supported.
  A GPU recipe reserves the worker's full GPU budget and inherits its allocation
  mask, even when it requests fewer GPUs. CPU recipes expose none; probes reserve
  the whole worker budget. Direct and podman-hpc probes use the allocation mask,
  while ordinary Docker and Podman probes use no GPUs.
- **Values are resolved once and handed down.** HEAD, the container
  runtime, and the foreign-write facts are read by the driver and
  passed to workers as values — a worker that asked git itself could
  get a different answer mid-run, and workers have no git anyway.

## Identity: two hashes, three states

`identity.py` computes two digests that deliberately answer different
questions:

- **`definition_version`** = hash(rendered recipe ‖ decisions) — what
  the spec says the output *is*. When it moves, the artifact
  contradicts the spec: **stale**, remade.
- **`env_version`** = hash(lock bytes ‖ interpreter pin ‖ install
  settings ‖ image document) — what the output *ran under*. When it
  moves, the artifact is merely from another time: **behind**,
  reported, left alone.

`assets.classify` is the one implementation of the rule, with two
callers: the worker (live input digests) and the read-only walk
(`None` for anything upstream that will run — "this is going to
change"). That single value is the entire difference between run and
check, which is what keeps `--check` honest. `behind` does not
propagate; `stale` wins when both apply; and a foreign write (an
output whose file or manifest was last touched by a commit that is not its own run
record) classifies stale through the same rule, as one more input
value.

Both hashes are length-framed (label, length, bytes per field), so a
boundary shift between concatenated fields cannot produce a collision.
The lock is hashed as raw bytes, never parsed — over-invalidation
costs a report line; a parse that disagrees with uv costs correctness.

## Storage: the repository is the record

`dataset.py` is the whole git + git-annex seam. The model is DataLad's:
git carries pointers and history, the annex carries bytes, and
`.gitattributes` routes content (`annex.largefiles=nothing` by
default; `data/` and `results/` opt out). A researcher only ever types
ordinary `git add` / `git commit`.

That ordinary `git add` dispatches git-annex from the *researcher's*
`PATH`, and a shell that cannot resolve it stages the raw bytes into
git history while exiting 0 — so `lc init` sets
`filter.annex.required=true`, which makes git refuse loudly instead
(every filtered command, not only `git add`).
Getting git-annex onto that `PATH` is the install's job, not the
repository's: `uv tool install lightcone-cli` puts it there alongside
`lc`.

Each output is committed with a **run record** — a `[DATALAD RUNCMD]`
commit message whose `cmd` reconstructs the engine
(`uv run --no-project --with lightcone-cli==<v>`) and re-executes the
worker entry point, so `datalad rerun` replays the making of an output
with the gates, the sandbox, and the manifest intact. Results are
committed *thin* (hard-linked to their annex object), which is safe
precisely because lc never writes an output in place — the worker
resets the directory first.

## The exec boundary

Every recipe and every `lc run` command goes through
`engine/sandbox/`: a `Policy` (mechanism-free path sets) is turned
into *a different argv that sandboxes itself* by a `Backend` —
Landlock via the stdlib-only shim `lightcone/_sandbox_exec.py`,
Seatbelt via `sandbox-exec`, the OCI mount table in containerized
mode, and `Unavailable` (wrap = identity) where no mechanism exists.
Because every backend is a pure argv rewrite, all of them are testable
on a host that can't run them, and the manifest's `hermeticity` field
records what was *actually* enforced — never what should have been.

There is one policy builder, `exec_policy`: probes and recipes share environment
and filesystem rules, with write scope and GPU visibility supplied by the caller.
GPU recipes and supported probes inherit the worker's allocation mask; CPU
commands get an empty mask.

## The container hatch

Containerized mode changes the recipe's world and nothing else.
`image.py` (pure) turns the `[tool.lightcone.image]` declaration into
a rendered Containerfile, an identity document, and a content tag;
`container.py` (impure) builds it, saves it as a `docker-archive`
inside the repository (`.datalad/environments/<tag>/image`, annexed),
and enters it. The engine never enters the image — driver, git, and
classification stay on the host; exactly two things run in-image: the
environment sync and each recipe exec, over a read-only rootfs with
the mount table as the whole policy. Execution pins the archive's
config-blob id, never a tag.

## Compute allocations

`engine.compute` owns allocation lifecycle through a small provider protocol.
A YAML catalog supplies ordered resource offers and stable native service
namespaces. When the implicit default file is absent, a built-in local catalog
provides all detected usable CPUs and RAM without setup. The `local` policy can
override that budget or disable local compute. Remote catalogs retain the default
local offer; explicit local connections supply their own offers. A runtime guard
disables local compute on recognized NERSC login nodes while permitting interactive
compute nodes. GPU offers need an explicit catalog. Missing explicit paths and invalid files
remain errors. No catalog is written and
no allocation starts until `compute launch` resolves resources and submits once. Slurm queries
and validated local OS identities are authoritative for allocations; Dask is the
authority for connected workers. Private scheduler/TLS files are connection
material, not a registry.

Allocation requests use SkyPilot-style CPU/memory exact or minimum quantities
and one accelerator type/count. Providers translate those requests into native
allocations; Lightcone does not depend on SkyPilot or carry its GPU alias registry.
Stock Dask workers inherit the allocation's native CUDA mask; Lightcone does not
probe GPU hardware. Container GPU access uses podman-hpc's native `--gpu` option.
See [GPU setup](user/cluster.md#gpu-allocations).

`compute.connect(CLUSTER_ID)` borrows a standard Dask client and closes only that
client on exit. Both execution commands require a cluster ID. The materialization
scheduler validates resource requests, then keeps its `submit`/`completed` seam.
Driver preparation and existing task runtime/sandbox checks remain unchanged.
Tasks use ordinary Dask scheduling;
there is no separate worker-selection or preflight layer, or site-marker guard. No execution command implicitly allocates compute.
See [compute internals](api/compute.md) and [deployment limits](user/cluster.md).

## The publication view

`crate.py` renders the repository as a Provenance Run Crate — a pure
function of repository state (sorted iteration, no clock, git injected
as a callable), which is what lets `materialize` converge
`ro-crate-metadata.json` byte-for-byte and commit only differences.
Run identity comes free from the manifests' `git_sha` (the driver
reads HEAD once per run), so one materialize maps onto one
`OrganizeAction` with no new manifest field.

## Repository at a glance

```text
src/lightcone/              # namespace — NO __init__.py
├── _sandbox_exec.py        # the Landlock shim — stdlib only, zero lightcone imports
├── cli/commands.py         # flags, rendering, exit codes — nothing else
└── engine/
    ├── project.py          # what a project is: convergence, discovery, mode
    ├── dataset.py          # the git + git-annex seam
    ├── identity.py         # env_version, definition_version, the lock scan
    ├── image.py            # the system layer, declared → rendered — pure
    ├── container.py        # runtimes, the build, the archived image — impure
    ├── crate.py            # the publication view — pure
    ├── assets.py           # an output: directory, manifest, state
    ├── plan.py             # the spec, read as a graph of tasks
    ├── worker.py           # making one output; the rerun entry point
    ├── materialize.py      # the driver: gates, Dask, the save/restore loop
    ├── run.py              # what `lc run` is
    ├── compute/            # common allocation API, local and Slurm providers
    ├── sandbox/            # the exec boundary
    └── templates/          # the scaffold's file content, as real files
```

Each module's page in [Engine Internals](api/index.md) carries its
public surface and the invariants that bind it.
