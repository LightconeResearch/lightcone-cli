# Design rationale: the locked environment is the execution environment

> **The normative specification is
> [execution-environment.md](execution-environment.md)** — now at
> **v4**, which descoped scale (>~10 nodes) per a requirements change
> and deleted this document's placement tiers, per-project images, and
> at-scale container mode after a 4-lens simplification review. This
> file is the long-form rationale and review record — background,
> alternatives analysis, empirical evidence — **and the documented
> re-add path for the scale-era mechanisms** if large-node-count work
> returns. Where the two disagree, the spec wins.

- **Status:** rationale & review record, revision 3 (v3.2 — uv-based; supersedes
  the pixi-based v2 and the container-canonical v1. Substrate evidence
  in [environment-substrate-evaluation.md](environment-substrate-evaluation.md);
  adoption evidence in [uv-vs-pixi-adoption.md](uv-vs-pixi-adoption.md).
  Revision 2 resolved a 6-agent review (identity/image coherence,
  dependency groups, read-only-tier semantics, the worker process
  contract, uv flag semantics verified against uv 0.12.3, Ray-vs-dask).
  Revision 3 resolves a second 4-agent review: the launcher/placement
  bootstrap, uv's PATH-fallback and project-discovery holes
  (empirically confirmed), install-selection settings escaping the
  identity (empirically confirmed), driver-side code attestation,
  interactive-allocation UX, gc liveness, and spec-completeness gaps.
  A third verification round (2 agents, both scoring 8/10 with
  architectural sign-off) produced the final amendments folded in
  below: the mid-run relock gate, the non-syncing exec-direct
  delegation hop, the UV_* namespace scrub, install-settings in
  `env_key`, and sidecar leases.)
- **Date:** 2026-08-15
- **Scope:** lightcone-cli CLI surface, `lc init` scaffold, engine
  environment handling, the dask execution fabric's environment
  contract, derived images, eval prompt
- **Coordination required:** ASTRA spec and the hub deployment
  contract — both tracked in Open questions
- **Not in scope:** changes to the Snakemake dask executor's
  scheduling semantics; hub deployment charts (only the worker-image
  and pod contract they consume); Windows (the scaffold targets
  linux-x86_64 and macOS-arm64; other platforms are added by editing
  `required-environments`)

## Summary

A Lightcone project's reproducibility guarantee — every materialized
output tied to a known, pinned environment — currently rests on a
content-addressed container image whose identity does not actually pin
an environment (it hashes build *inputs* while the base image and pip
resolution float). The v2 draft replaced the substrate with
`pixi.toml`/`pixi.lock`. This revision keeps v2's architecture —
lockfile as the single source of truth and identity, container demoted
to a derived per-venue artifact, `lc run CMD` as the environment
runner — but builds it on **uv**, for the reasons quantified in the
adoption report: uv is the presumptive default tool of the Python
ecosystem (and of every coding agent), the uv-lockfile-first +
derived-container pattern is shipped practice across the modern
execution-infrastructure tier (Flyte/Union ImageSpec, Metaflow
`--environment=uv`, Modal `Image.uv_sync`, ClearML — and Ray's own uv
runtime-env hook, which is this same design implemented inside a
fabric), and this project's workloads — dask over TCP, GPU via PyPI
CUDA wheels — no longer need the conda-forge system layer that was
pixi's decisive advantage.

Five load-bearing decisions:

1. **`pyproject.toml` + `uv.lock` + `.python-version` at the project
   root are the single source of truth for the execution environment.**
   uv is the only tool a user needs on any venue; `lc` itself arrives
   through it. The manifest's environment identity (`env_version`)
   hashes the declared environment: the lock's resolved artifacts, the
   interpreter pin, the install-selection settings, and the declared
   system layer.
2. **The engine is inside the experiment's lock.** `lightcone-cli`
   (which carries snakemake, dask, and the executor plugin) is a locked
   dependency of every project. Driver, SLURM workers, and Gateway
   worker pods all run the engine *from the project lock* — version
   skew becomes structurally impossible on every lc-managed branch,
   and detected-and-fail-fast on the caller-owned external branch.
3. **There is exactly one environment per project.** No dependency-
   group splits between prototyping and materialization, between venv
   venues and images, or between identity and runtime: what `lc run`
   probes is byte-for-byte what recipes execute against, on every
   venue. (Per-output environments are a deferred extension with a
   stated identity story — see Open questions.)
4. **The container is a cache of the locked environment, never its
   definition — and project code never enters an image.** Where a venue
   needs an image (Gateway worker pods; optionally podman-hpc at
   scale), `lc build` renders the environment into one, tagged by a
   hash of the *complete rendered build context*. Code reaches every
   venue through the filesystem the venue already shares (working
   tree, CFS/Lustre, NFS home). Editing code therefore **never**
   triggers an image rebuild; only changing the environment does —
   which is exactly when a rebuild is meaningful.
5. **One recipe wrapper on every venue: `uv run --locked --exact`** —
   with per-venue *enforcement posture*, not per-venue wrapping. Where
   the environment tier is writable (laptop), the wrapper converges a
   drifted environment to the lock before running. Where it is shared
   or read-only (SLURM common software, worker pods, caller-owned
   clusters), workers run with `UV_OFFLINE=1`: a warm environment is a
   no-op check, and any drift fails fast instead of thundering-herd-
   installing — never silently executing stale, never mutating a
   shared or foreign tier mid-run.

`lc run CMD [ARGS…]` (the environment runner) wraps
`uv run --locked --exact`; `lc materialize` (renamed from `lc run` in
stage 1) keeps its dask fabric — and the fabric stays dask (see "The
execution fabric" for the Ray evaluation).

## Background

### What the eval evidence established (unchanged from v2)

The CI evals' consistent failure mode is environment-boundary
confusion: `ModuleNotFoundError: scipy` on first materialize, host-side
probes failing on packages present only in the recipe env, agents
"fixing" imports by installing into the harness's own venv. Every
failure is a package-presence error; all of them are prevented by **one
canonical environment reachable through a run verb**. That evidence is
substrate-neutral — the substrate choice rests on reproducibility
strength, operational fit, tooling cost, and (newly weighed) adoption.

### Why the pixi-based v2 draft was revised

The substrate evaluation chose pixi for one decisive property: a single
`pixi.lock` pins the *system* layer (interpreter, BLAS, MPI, compilers)
that uv cannot see. Three findings since then change the weighing
(details and sources in [uv-vs-pixi-adoption.md](uv-vs-pixi-adoption.md)):

1. **Adoption is an order of magnitude apart and widening.** uv:
   ~196M PyPI downloads/month, a `uv.lock` in 32% of Python repos
   created in 2025, native Dependabot/Renovate/PyCharm/CI support,
   deep training-data presence in every coding agent. pixi: absent
   from every usage survey, ~12 Stack Overflow questions, a 5-person
   pre-1.0 vendor. For a tool whose primary interface is an agent
   working in a terminal, "the agent already knows uv" is a real
   reliability property, not a popularity contest.
2. **The uv-lockfile-first + derived-container pattern is battle-tested
   prior art.** Flyte/Union ImageSpec accepts a `uv.lock` and derives a
   container whose tag is a deterministic content hash; Metaflow ships
   pyproject + uv.lock to Kubernetes workers and re-materializes with
   uv; Modal builds images server-side from `uv sync --frozen`; ClearML
   agents execute `uv sync --locked`; Ray re-execs every worker
   through the driver's own `uv run` flags. The multi-stage uv Docker
   pattern and `hash(uv.lock)`-as-cache-key are officially documented,
   widely replicated standards.
3. **This project's workloads no longer exercise pixi's advantage —
   structurally for MPI, presumptively for the rest.** The execution
   fabric is dask over TCP — there is no Cray-MPICH ABI requirement in
   the materialization path (structural; verified against the fabric
   code). GPU stacks are pinnable from PyPI (NVIDIA's official
   `cuda-toolkit` wheels including `nvcc`; pinned PyTorch indexes).
   BLAS arrives vendored inside numpy/scipy wheels — identical
   binaries from the lock everywhere. The residual pixi-only cases —
   BLAS-*variant* control, non-PyPI tools without a container — are
   believed absent from current projects but have not been surveyed;
   Open questions carries the survey and the explicit pixi-fallback
   trigger criteria, so the fallback is a decision rule, not a comfort
   clause.

### What the dask fabric implies

v2 was written as if recipes execute wherever snakemake runs. They do
not. `lc materialize` starts a run-scoped dask cluster
(`engine/dask_cluster.py` — LocalCluster on a laptop, srun-launched
`dask worker`s inside a SLURM allocation, a created-and-culled Dask
Gateway cluster on the hub) and a custom Snakemake executor
(`snakemake_executor_plugin_dask`) submits each rule as a dask task. On
the worker, `_run_shell` launches a *child snakemake* that executes the
rule's `run:` block. Consequences the environment design must honor:

- **Workers need the full engine** — lightcone-cli, snakemake, dask,
  the executor plugin — plus every recipe dependency. The environment
  is one indivisible thing; there is no "orchestration layer"
  separable from the project environment in practice (the current
  design's separate orchestration image layer is where driver/worker
  version skew comes from — the executor's `_unpack_result` exists
  solely to tolerate it).
- **Three processes run per rule on a worker**, and each must land in
  the locked environment: (S1) the dask worker process itself, (S2)
  the child snakemake it spawns, (S3) the recipe subprocess. Snakemake's
  `RemoteExecutor` would by default embed the *driver's*
  `sys.executable` into the child command; today this is neutralized
  by the `--shared-fs-usage` set excluding software-deployment (so
  workers invoke plain `python` — see `_build_snakemake_cmd`), and the
  design keeps that flag **and** adds a belt-and-braces
  `get_python_executable()` override so the property no longer hangs
  on a side effect (see the venue table).
- **The Gateway branch is where the rebuild-per-edit pain lives.**
  Worker pods run the project image; the current scaffold bakes project
  code into the image (`WORKDIR /app`), so editing `src/fit.py` forces
  a rebuild-and-push before the next run — even though the executor
  already `cd`s into the NFS-mounted project directory that both the
  notebook pod and the workers share. The code was always reachable
  without the image; the image only ever needed to supply the
  *environment*.
- **Every venue already shares a filesystem between driver and
  workers**: the working tree (laptop), CFS/Lustre (Perlmutter
  allocation), NFS home (hub). "Code travels via the filesystem, env
  travels via the lock" is therefore implementable with zero new
  transport machinery.

## Design principles

- **Honest identity.** The manifest records the *resolved* environment
  (artifact URLs + hashes) plus the install-selection settings and the
  declared system layer, never the inputs to a non-deterministic
  build. The claim is pinned environment identity — not bit-identical
  outputs (BLAS kernel dispatch and thread scheduling vary by hardware
  under every substrate). Where the guarantee is weaker (host-provided
  system packages on venv venues, sdist builds, interpreter builds,
  apt-layer contents), the manifest says so in attestation fields
  rather than pretending.
- **One source of dependencies.** `pyproject.toml` declares, `uv.lock`
  pins; venvs, worker pods, and podman-hpc images are all derived from
  it. Nothing else declares packages — no requirements.txt, no
  authored Containerfile, no per-venue spec.
- **The engine is part of the experiment.** The lc/snakemake/dask
  versions that materialize an output are pinned by the same lock as
  the science code's dependencies, and covered by the same
  `env_version`.
- **The container is a cache.** Correctness comes from the lock; an
  image is a pre-warmed rendering of it for venues where warming from
  the filesystem is impossible (pods) or too slow at scale (Lustre,
  ~100+ nodes). Project code never enters an image.
- **Harness-agnostic by being boring.** A plain CLI command (`lc run`,
  and underneath it `uv run --locked --exact`) behaves identically
  under Claude Code, Codex, CI, and a human terminal. No settings
  plumbing, env files, hooks, or activation.
- **Opt-in, never imposed.** The locked environment is where *project
  code* runs, not a cage around the agent; agents legitimately work on
  the host outside it.
- **Stateless.** No long-lived containers or shells; the run-scoped
  cluster lifecycle stays exactly as it is.

## The design

### Environment definition: `pyproject.toml` + `uv.lock` + `.python-version`

`lc init` scaffolds, at the project root:

```toml
# pyproject.toml
[project]
name = "my-analysis"
version = "0.0.0"
requires-python = "==3.12.*"
dependencies = [
    "lightcone-cli==X.Y.Z",   # the engine: lc + snakemake + dask + executor
    "numpy",
    # … science deps accumulate here via `uv add`
]

[tool.uv]
required-version = ">=0.12"
# lock-time failure when a dependency that has no sdist lacks wheels
# for a target platform (sdist-capable packages can still fall back to
# source builds — see the hermeticity section)
required-environments = [
    "sys_platform == 'linux' and platform_machine == 'x86_64'",
    "sys_platform == 'darwin' and platform_machine == 'arm64'",
]
```

plus `.python-version` pinning an **exact interpreter patch** (e.g.
`3.12.8`, satisfied by uv-managed python-build-standalone builds on
every venue), and runs `uv lock`. `pyproject.toml`, `uv.lock`, and
`.python-version` are committed. `requirements.txt`, the authored
`Containerfile`, and the venv-bootstrap scaffold are removed; existing
projects are converged by `lc init` (see Migration).

**One environment, no group splits.** The scaffold declares no
dependency groups, and lightcone's execution path treats the project
environment as indivisible: recipes, `lc run` probes, images, and
`env_version` all cover the same resolved set. Projects may use PEP
735 groups, and uv's *defaults* decide what is installed (the `dev`
group by default) — lightcone does not fight those defaults with
per-context flags, because any identity/image/wrapper disagreement
about groups re-creates the probe-succeeds/materialize-fails failure
mode this design exists to kill. Instead, the **install-selection
settings themselves are part of the identity** (see Environment
identity): flipping `[tool.uv] default-groups` changes `env_version`
even though `uv.lock` is byte-identical — an empirically confirmed
hole in lock-only hashing. Two stated consequences: `uv add --group
dev pytest` re-materializes outputs exactly like any dependency edit
(prefer `uv tool install` for host-side tooling — the lc pattern —
over project dev groups); and packages locked in *non-default* groups
are hashed though never installed, a deliberate over-inclusion that
buys wrapper/identity agreement at the cost of occasional spurious
invalidation (the scaffold's no-groups default makes both cases rare).

**Virtual by default; packaged is a supported step up.** The scaffold
omits `[build-system]`: uv treats such a project as a *virtual*
project — dependencies are managed and locked, the project itself is
never built or installed. That matches a research repo (recipes invoke
`python src/fit.py`, not `import my_analysis`). A project that wants an
importable package adds a build backend (recommend `uv_build`); uv
then installs it *editable* from the working tree on venv venues —
code content stays outside `env_version` (it is code, attested by
`git_sha`), and images remain code-free because image builds always
pass `--no-install-project`. On **image venues**, packaged-project
support requires the runtime editable install into the pod environment
(a write, with build-backend availability constraints) and is
**deferred in v3** — `lc doctor` flags a packaged project that
declares the `gateway` venue; see Open questions.

**Venue declaration (informational only).** `[tool.lightcone]
venues = ["local", "perlmutter", "gateway"]` (default `["local"]`)
feeds `lc init --gpu`'s guidance and `lc doctor`'s checks. It is never
part of any identity hash, and execution does not require it —
venue detection at run time stays what the fabric code already does.

**Bootstrap bar (goal 1).** uv is the single prerequisite on every
venue, and it is the same tool that installs `lc`
(`uv tool install lightcone-cli`). A laptop user needs no Docker, no
conda, no pixi, no second package manager — `uv tool install
lightcone-cli && lc init && lc run python …` is the whole on-ramp. If
`lc` was obtained some other way and uv is absent, `lc` prints the
official one-line installer and stops; it does not bundle bootstrap
machinery.

### `lc` and the project lock: the launcher contract

The globally installed `lc` (a `uv tool` shim of the same codebase) is
a **launcher** with five responsibilities, executed in order. The
governing rule: **the launcher owns everything that must happen before
the locked engine can exist** — discovery, placement, hygiene, and the
first convergence — and then hands off by *direct exec*, never through
a second `uv run`.

1. **Project discovery, lc's way.** Walk up for `astra.yaml` (the
   existing `_project_root()` rule). uv's own walk-up discovery is
   never trusted: a monorepo-root or vendored `pyproject.toml` can
   differ from the lightcone project (empirically confirmed
   divergence). If no project is found, project verbs fail with lc's
   error — the launcher never lets uv pick a project.
2. **Placement and tier selection.** Resolve the venue from the site
   registry (the launcher ships the same registry data as the engine —
   it *is* the same package, unpinned), pick the environment tier
   (site tier when warm or writable; scratch fallback otherwise — see
   Environment placement), and export the placement environment:
   `UV_PROJECT_ENVIRONMENT`, `UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR`,
   `UV_LINK_MODE`. Placement paths are keyed by **`env_key` =
   sha256(uv.lock bytes ‖ .python-version bytes ‖ canonical
   install-settings JSON)[:16]** — computable by any launcher version
   with no lock parsing (the install-settings come from a `tomllib`
   read of `[tool.uv]`), so launcher/engine skew cannot misplace an
   environment; the placement-path schema is declared stable.
   Install-settings are *in* the key because they change the installed
   set against a byte-identical lock — without them, two
   configurations would share a path and `--exact` syncs would
   converge each other's packages away. (`env_key` is a *cache key*,
   not the identity — `env_version` remains the manifest's identity
   and is computed by the pinned engine only.)
3. **UV_\* hygiene.** Scrub the `UV_*` namespace before anything
   touches uv: unset every `UV_*` variable except the placement set it
   just exported (and, worker-side, the deliberate overlay —
   `UV_OFFLINE`, `UV_PYTHON_DOWNLOADS`). Ambient variables like
   `UV_NO_BINARY` or `UV_PYTHON` are an unhashed channel into exactly
   the install-selection semantics the identity covers; scrubbed
   non-empty overrides are logged. The recipe wrapper's environment
   gets the same scrub.
4. **Preflight and first convergence.** Check the placed environment:
   lock↔pyproject staleness (an lc-side file check — pointed errors
   never depend on uv's message formats), marker consistency,
   writability. Where the env is cold or drifted *and the tier is
   writable*, the launcher performs the convergence itself —
   `uv sync --locked --exact` with captured output — and prints the
   removal notice ("removed N packages not in uv.lock — add
   dependencies with `uv add`"). Where the tier is read-only and the
   env is cold/stale, it fails here with the venue message ("run
   `lc env sync` from a login node"), *before* uv can emit a raw
   `Permission denied`. Convergence is mechanical uv invocation, safe
   in the unpinned launcher; everything semantic (env_version, marker
   stamping, manifests) belongs to the engine it is about to exec.
5. **Delegation, per-verb, by direct exec:**

   | Verbs | Where they run | Why |
   |---|---|---|
   | `materialize`, `run`, `build`, `env sync` — and any verb the launcher does not recognize | **exec `<placed-env>/bin/lc …`** with `LC_DELEGATED=1` as the recursion guard | semantics must match the locked engine. Direct exec (no second `uv run`) means there is no PATH-fallback hole at all: if `<placed-env>/bin/lc` does not exist after a successful sync, the error is precise — "lightcone-cli is not in this project's lock — run `uv add lightcone-cli==X.Y.Z`". Unknown verbs delegate because lock semantics are the safe default (the non-delegating set below is closed) |
   | `status`, `verify`, `export`, `env gc` | tool environment, directly | manifest-only or filesystem-janitor work, offline, no sync — a fresh clone's `lc status` must not trigger a multi-gigabyte materialization, and `env gc` must run even when the tier is quota-exhausted and a sync would fail; cross-version manifest compatibility is `SCHEMA_VERSION`'s job |
   | `init`, `doctor`, `--version` | tool environment, directly | must work before a lock exists; `lc --version` prints both the launcher version and, inside a project, the locked engine version |

   The delegated engine still self-verifies on startup
   (`lightcone.__file__` inside the expected prefix) as
   belt-and-braces, and stamps the environment marker (a semantic act
   the launcher cannot perform — it requires `env_version`).

`uv run lc materialize` typed directly still works — the engine it
starts performs the same discovery, detects a placement mismatch
(`sys.prefix` ≠ placed path), and re-execs once through the launcher
path (guarded by `LC_DELEGATED`); the only residue is uv's default
`./.venv` from the outer hop, which `lc env gc` offers to remove on
placement-managed sites.

`lc init` on a project that already pins an engine never changes the
pin without `--upgrade-engine`.

This retires the executor's version-skew tolerance
(`_unpack_result`'s bare-int fallback) on lc-managed branches; its
removal is gated on the external-scheduler branch's fingerprint check
landing first, so a skewed caller-owned worker gets a remedy, not a
`TypeError`.

### Environment identity

`code_version`'s environment input changes from the resolved image tag
to `env_version = sha256(canonical JSON of the env-input document)`,
mirroring `code_version`'s existing canonical-JSON convention
(`sort_keys`, compact separators):

```jsonc
{
  "schema": 1,
  "python": "3.12.8",                       // from .python-version
  "packages": [                             // the lock projection, sorted by (name, version, url)
    ["numpy", "2.1.0", "https://…/numpy-…whl", "sha256:…"],
    …
  ],
  "install_settings": {                     // pyproject knobs that change what gets installed —
    "default_groups": [...],                //   empirically these alter the env while uv.lock is
    "no_binary": bool, "no_binary_package": [...],   // byte-identical. This is the CLOSED, audited
    "no_build": bool,  "no_build_package": [...]     // set for the supported uv range; widening
  },                                        //   required-version obligates a re-audit, and golden
                                            //   fixtures cover the per-package variants too
  "system_packages": ["texlive-latex-base"],// sorted [tool.lightcone] system-packages
  "containerfile_extra": "sha256:…"         // or null
}
```

The **lock projection** covers every package in `uv.lock` except the
project's own root package, per source type:

- *registry*: one row per locked artifact — `(name, version, artifact
  URL, sha256)`;
- *git*: `(name, version, URL including the resolved commit, null)`;
- *path / directory / editable dependencies*: **refused by default** —
  `lc` errors at lock-ingestion time ("path dependencies are outside
  the integrity guarantee — publish the package, vendor it, or
  override with `[tool.lightcone] allow-path-deps = true`"). Under the
  override, the row carries a content hash computed by a **dedicated
  tree hasher** — `git ls-files`-based when the path is a git repo,
  otherwise an explicit exclude set (`.git`, `.venv`, `__pycache__`)
  with symlinks not followed — *not* the results-directory
  `sha256_dir()` (which has no ignore rules and would hash a sibling
  repo's `.git` churn into the identity); cached per (path, HEAD,
  dirty) per run. Path deps are additionally refused when the site
  registry declares a read-only env tier (uv revalidates directory
  deps at run time — a write).

The projection is over the lock's *content* — the `version` and
`revision` serialization header fields are excluded by name — so
lock-format churn cannot spuriously invalidate outputs. Golden tests
pin the projection (and the default-groups flip) against lock
fixtures.

The declared system layer is **inside** `env_version`: an output
produced with `texlive` declared is not the same experiment as one
produced without, even though uv cannot see the difference — this
closes the hole where system-dependency edits would be invisible to
`code_version`. The *ambient* channel to the same install-selection
semantics — `UV_NO_BINARY`, `UV_PYTHON`, and friends exported in a
shell — is closed by the launcher's `UV_*` scrub (responsibility 3):
what the identity hashes is the whole surface that can steer an
install, declared or environmental.

Two honest scope notes, stated rather than discovered:

- **`env_version` is platform-independent by choice.** `uv.lock` is
  universal; the projection covers all locked platforms, so a re-lock
  that changes only another platform's wheels moves `env_version` even
  though this venue's binaries are unchanged. The design accepts that
  rare spurious invalidation in exchange for one identity across the
  laptop→HPC→hub path (a per-platform projection would fragment
  identity across exactly the venues we unify); the manifest records
  `platform` alongside.
- **The interpreter *build* is attestation, not identity.** The
  version pin is hashed; the python-build-standalone build uv
  materializes for it is selected by the uv release and differs per
  platform. The manifest records `python_build` (the full
  `cpython-3.12.8-…` tag) and `uv_version`; two runs differing only in
  interpreter build share an `env_version`, in the same residue class
  as kernel/glibc. (Image venues do not even carry this residue: the
  image pins its interpreter bytes, and the image tag covers the uv
  release that chose them.)

**The invalidation blast radius is real, accepted, and surfaced.**
Because the engine and every group are in the identity, both an engine
upgrade (`uv lock --upgrade-package lightcone-cli`) and a dev-tooling
add re-materialize every output. This is deliberate: the engine writes
the manifests, wraps the recipes, and generates the Snakefile — an
identity that excluded it would certify environments differing in the
one component that touches every output; and group-splitting the
identity was rejected above. The cost is contained by the exact engine
pin (upgrades are deliberate acts), steered by the docs (host tooling
via `uv tool`, not dev groups), and **surfaced at decision time**:
when `lc` detects that the current `env_version` differs from the one
in existing manifests, it prints the scope — "environment changed:
N materialized outputs are now stale". (Projecting the engine's
subtree out of `env_version` into attestation was considered and
rejected: it trades auditability for convenience exactly where
provenance tooling should not.)

Additional manifest fields, all attestation (outside `code_version`):
`platform`, `python_build`, `uv_version`, `worker_runtime`
(`venv` | `image`), `env_tier` (`project` | `site` | `scratch`), image
tag + digest when an image was used (carried by the existing
`LIGHTCONE_WORKER_IMAGE` plumbing), `sdist_built` (locked packages
that resolved to sdists — their builds ran a host toolchain the lock
cannot see), per-rule `git_sha` + `git_dirty` (see the code-state
contract), and the fingerprint-check strength (`marker-match` |
`versions-only` | `unavailable`).

### `lc run CMD [ARGS…]` — the environment runner

```
lc run python src/fit.py --optimizer nelder_mead --output /tmp/probe
lc run python -c "import scipy; print(scipy.__version__)"
lc run            # bare: interactive shell inside the environment
```

Semantics:

- **Equivalent to `uv run --locked --exact CMD`**, implemented via the
  launcher contract: preflight + `uv sync --locked --exact`
  convergence, then CMD exec'd from the placed environment. `--locked`
  semantics: a stale lock fails with a pointed message, never silently
  re-resolves. `--exact` is load-bearing: uv's default sync is
  additive-only (empirically verified), so without it a stray
  `uv pip install` would persist across runs and let a recipe import
  packages outside the lock while the manifest stamps a locked
  `env_version`. With `--exact`, the environment is converged to
  exactly the lock — including *removing* extraneous packages — before
  every execution.
- **Convergence is lc-driven, uv-executed** — performed once by the
  launcher's preflight step (responsibility 4), as
  `uv sync --locked --exact` with captured output. This is what lets
  `lc` print its own messages — the removal notice and the
  venue-specific errors — instead of parsing uv's unstable stderr:
  staleness (`uv.lock` vs `pyproject.toml`) and marker drift are
  checked lc-side from the files, so pointed errors do not depend on
  uv's message formats across the `>=0.12` range. By the time the
  user's command runs, the environment is already exactly the lock.
- **Stateless, exit code propagated, stdio inherited** — identical
  under every harness. Bare `lc run` opens `$SHELL` inside the
  environment.
- Old-grammar guardrail (from stage 1): when the first argument matches
  a declared output id, print `did you mean: lc materialize best_fit?`.

The boundary rule survives verbatim with one word changed: **"if a
`ModuleNotFoundError` in this command would mean 'fix
`pyproject.toml`', it belongs in `lc run`."** Project-code execution
goes through `lc run`; project tooling and everything else stays on the
host. Agents are taught the rule, not an enforcement mechanism; the
manifest chain remains the backstop.

Two honest notes carried forward: the old container wrap mounted only
`$PWD`, mechanically enforcing path discipline; direct execution sees
the host filesystem — convention plus the manifest backstop replace
the mount on venv venues. And on the hub, `lc run` executes in the
notebook pod (its system layer) while recipes execute in the derived
image's system layer — same lock, same Python packages, different OS
layer; `worker_runtime` keeps the distinction inspectable, and G4 is
exact at the Python layer, which is where the eval failures lived.

### Environment placement

uv's defaults put the venv at `./.venv`, the cache at `~/.cache/uv`,
and managed interpreters at `~/.local/share/uv` — correct on a laptop,
pathological on shared filesystems. The launcher applies placement
from the site registry (see the launcher contract); the values:

- **Laptop / generic**: defaults; `.venv` in the project (IDE- and
  agent-discoverable), self-heal permitted.
- **Perlmutter (site registry)**: environments are
  **content-addressed by `env_key`, mirroring images**:
  `UV_PROJECT_ENVIRONMENT=/global/common/software/<account>/<project>/envs/<env_key>`,
  `UV_PYTHON_INSTALL_DIR` beside them, `UV_CACHE_DIR` co-located on
  the same filesystem (uv hardlinks cache→env only within one
  filesystem), `UV_LINK_MODE=hardlink` stated explicitly. `<account>`
  resolves from project config (`.lightcone/lightcone.yaml`
  `account:`, seeded by `lc init` from `SBATCH_ACCOUNT` or by
  prompting; pointed error when absent); `<project>` is the ASTRA
  project name. Keying by `env_key` (lock + interpreter bytes) rather
  than `env_version` means identity-only edits (system-package
  declarations) do not force a pointless re-sync; concurrent runs with
  different locks are disjoint by construction; warm checks are
  trivially truthful. `/global/common` is small-file-optimized,
  **read-only from compute nodes**, and quota'd (default 10 GB / 1M
  inodes, shared per account) — `lc doctor` reports usage, and a
  CUDA-class environment may need a quota increase.
- **Perlmutter interactive fallback (scratch tier).** When the driver
  is on a compute node (salloc/sbatch) and the site env for the
  current `env_key` is absent or stale, failing with "go to a login
  node" would strand the interactive workflow (an agent that ran
  `uv add scipy` mid-allocation could not proceed at all). The
  registry therefore declares a **writable fallback tier**:
  `$SCRATCH/.lightcone/envs/<env_key>`, content-addressed identically;
  the engine syncs there, proceeds, records `env_tier: scratch` in
  manifests, and prints once: "using scratch-tier environment; run
  `lc env sync` from a login node to place it on /global/common".
  Same lock, same identity — only the placement (and its at-scale
  metadata behavior) differs.
- **Hub notebook pod**: driver venv placed per hub config (off
  NFS-home where the deployment provides local scratch); worker pods
  use the baked image contract, never a forwarded driver path.

**Environment lifecycle.** Every convergence the engine performs —
`lc run`, `lc materialize` preflight, `lc env sync`, `lc build` —
finishes by writing/refreshing the marker `lightcone-env.json`
(`env_version`, `env_key`, lc version, `python_build`) into the env it
materialized; marker absence on an otherwise lock-consistent env means
"restamp", never "error" (uv recreates venvs wholesale on interpreter
changes — empirically confirmed — and a user's plain `uv sync` is
legitimate). `lc materialize` and `lc env sync` also touch a **lease
file** (job id + expiry) at run start — in a **writable sidecar**,
`<project>/.lightcone/leases/<env_key>.json` (gitignored), *not*
inside the env: the site tier is read-only from the compute nodes
where runs live, so an in-env lease could never be written by the
processes that need it, and env mtimes there record last sync, not
last use. `lc env gc` (login-node-only on HPC sites) removes
content-addressed envs beyond `--keep N` (default 3, by sidecar-lease
recency falling back to marker mtime), skipping any env with an
unexpired lease or whose job id is still in `squeue` — closing both
the gc-vs-running-job race and the shared-account race. Interpreters
and cache are uv's to manage (`uv cache prune` documented); `lc env
sync` prints the orphan count with a gc hint.

### `lc env sync` — environment materialization as an explicit step

`uv sync --locked --exact` under the placement rules, exposed as its
own verb because on Perlmutter the durable tier is **login-node-only**
(read-only on compute, where the SLURM-branch driver lives). The flow:
sync from a login node once per lock change (`lc env sync` — the
existing `_abort_on_perlmutter_login` guard is updated in stage 2 to
allow exactly this verb, and `lc materialize` on a login node performs
the sync and then refuses cluster start with the submit guidance);
inside allocations the preflight finds the env warm, or falls back to
the scratch tier as above. The preflight — env present and
marker-consistent, else writable, else pointed error — runs for **both
`lc materialize` and `lc run`**, before cluster start / before exec,
so a cold env on a read-only tier surfaces as lc's message, not uv's
raw `Permission denied (os error 13)` (the recipe wrapper additionally
pattern-matches uv's permission/offline errors as a backstop and
prefixes the venue remedy).

### `lc materialize` and the dask fabric

Behavior preserved: generate the Snakefile, start the run-scoped
cluster, submit rules as dask tasks, write manifests. Environment
mechanics:

**The per-venue process contract.** Three processes run per rule on a
worker: (S1) the dask worker, (S2) the child snakemake, (S3) the
recipe. The executor overrides `get_python_executable()` to return
`"python"` — never the driver's `sys.executable` — and the
`--shared-fs-usage` setting that already produces this behavior is
kept and pinned by a test; S2 therefore always resolves from the
worker's `PATH`, which each venue contracts to lead with the locked
environment. S3 is always the uniform wrapper
`uv run --locked --exact --project . -- bash -c '<recipe>'` — the
explicit `--project .` (cwd is contractually the project root after
the executor's `cd`) keeps the never-trust-uv-walk-up rule
exception-free — resolved under the venue's baked/injected placement,
after `run_rule`'s env_key gate (see the code-state contract). A
generator test asserts the emitted job command contains no absolute
interpreter path and carries the project pin.

| Branch (`dask_cluster.py`) | S1 (worker) | S2 (child snakemake) | S3 (recipe) env source | Enforcement posture | Code |
|---|---|---|---|---|---|
| LocalCluster (laptop) | project `.venv` | `.venv` via PATH | `.venv` | writable: self-heal (`--exact` converges) | working tree |
| SLURM allocation (srun workers) | content-addressed env (site or scratch tier), inherited from the placed driver | same, via PATH | same, via `UV_PROJECT_ENVIRONMENT` | `UV_OFFLINE=1`, `UV_PYTHON_DOWNLOADS=never` in worker env: warm ⇒ no-op, drift ⇒ loud fail | CFS/Lustre working tree |
| — at-scale mode of the SLURM branch (site-thresholded, optional) | image via `srun podman-hpc run --net host -v "$PWD":"$PWD" -v /global:/global … dask worker` | image PATH (`/opt/venv`) | image env via baked contract | offline (image is complete) | `$PWD` bind-mount |
| Dask Gateway (hub / GKE) | image **is** the pod: `/opt/venv` | image PATH | baked contract | offline (baked `UV_OFFLINE=1`) | NFS home working tree (executor already `cd`s there) |
| Pre-existing scheduler (`DASK_SCHEDULER_ADDRESS`) | caller-owned | caller-owned PATH | caller-owned | executor injects `UV_OFFLINE=1` + `UV_PYTHON_DOWNLOADS=never` into the job env — lc never mutates or cold-installs a caller's environment; fingerprint gate below | caller-owned |

Notes on the at-scale mode: it exists because NERSC's benchmarks show
shared-filesystem environments losing to squashfs images at ~100+
nodes. dask workers require reachable advertised addresses, so
`--net host` is mandatory — and NERSC documents `--network=host` as
incompatible with podman-hpc's `--gpu` flag, so the mode is initially
scoped to CPU rules (GPU-at-scale via CDI device injection is a spike
question). It is a launch-wrapping mode of the SLURM branch, not a
fifth branch.

**Rebuild-per-edit is eliminated by construction.** The image tag is a
pure function of the rendered build context (next section), which
contains no project code. Editing `src/fit.py` changes no input to the
tag; the next `lc materialize` reuses the existing image and picks the
new code up from the shared filesystem. Changing the environment
(deps, system packages, interpreter pin) changes the tag — the one
case where a rebuild is *correct*, and `lc` performs or requests it
explicitly rather than silently.

**Warm-only workers, enforced rather than assumed.** The driver (or
`lc env sync` on read-only-tier sites) materializes the environment
exactly once before fan-out. Workers on every cluster venue run with
`UV_OFFLINE=1` and `UV_PYTHON_DOWNLOADS=never`: a warm environment
makes the per-rule `uv run --locked --exact` a metadata check plus
exec (no network, no writes — verified against a read-only venv on uv
0.12.3, including git-sourced deps from a warm env), and any drift
fails immediately and loudly instead of racing 100 concurrent installs
onto a shared filesystem. (`--no-sync` was considered and rejected: it
silently disables the `--locked` staleness check — verified — which
would trade the guarantee for the optimization.) The justification is
portability and the shared-FS install herd — *not* missing internet;
Perlmutter compute nodes can reach PyPI, which is exactly why
enforcement matters. On the writable laptop venue, concurrent per-rule
syncs on one `.venv` are serialized by uv's environment lock; the
Perlmutter spike's checklist includes confirming the concurrent
`--exact` behavior empirically.

**The code-state contract.** Code rides the live shared filesystem, so
a mid-run edit can change what later rules execute. The **driver**
captures code state at each rule dispatch — `git_sha` (cheap HEAD
read) and `git_dirty` (`git status --porcelain`, TTL-cached a few
seconds) — and passes it into the job command for `write_manifest` to
record. Driver-side capture is deliberate: worker pods have no git
binary and would trip git's dubious-ownership check on NFS trees, and
the dispatch-to-execution gap is seconds against a threat model of
mid-run human/agent edits. `lc materialize` warns once at start on a
dirty tree. Dependency edits mid-run fail subsequent rules by design
— but **not** via `--locked`, which passes when an atomic `uv add`
updates lock and pyproject *consistently* mid-run (on writable venues
`--exact` would then silently converge workers to the new lock while
manifests stamp the run-start identity — the exact G3 violation this
design exists to prevent, and venue-asymmetric, since read-only tiers
catch it only by accident). Instead the driver captures **`env_key` at
run start and embeds it in every dispatched job command**; worker-side
`run_rule` re-hashes the shared `uv.lock`/`.python-version`/
install-settings before invoking the recipe and fails on mismatch with
"lock changed mid-run — re-run lc materialize". Fail-fast, not
prevention: the run-scoped cluster and per-rule attestation bound the
blast radius, and `lc verify` surfaces dirty-tree outputs distinctly.

**Worker fingerprint check** (extends the existing
`_assert_worker_resources` pattern). At cluster connect, the executor
`client.run`s a probe that reports, per worker: the environment marker
(`lightcone-env.json` in venvs, `LC_ENV_VERSION` ENV in images), the
`importlib.metadata` version of lightcone-cli, and
`os.path.isdir(workdir)` (catching a missing NFS mount before the DAG
starts, not at rule 40). Mismatch fails fast with the venue-specific
remedy. On the **pre-existing-scheduler branch** the marker may not
exist; the check degrades to version-level attestation
(`importlib.metadata` names/versions against the lock projection's
name/version set), and — because this is the one branch where lc does
not own the substrate — a fingerprint below `marker-match` **refuses
to run by default**, with `--allow-unverified-cluster` proceeding and
recording the achieved strength (`marker-match` | `versions-only` |
`unavailable`) in every manifest. The strength name is `marker-match`,
not "verified", deliberately: the marker attests the sync that wrote
it, not the current bytes of every installed file. The check is
point-in-time over currently-connected workers; on Gateway the
homogeneous image makes late joiners equivalent, and a
`SchedulerPlugin` re-check on worker-added events is the hardening
step if the external branch ever needs more.

Snakemake stays entirely env-oblivious (no `--sdm`, no directives) —
recipes remain opaque shell strings; unchanged from v2, and still
cheaper than the unmerged Snakemake software-deployment framework.

### Derived images: `lc build` (the environment cache renderer)

For the Gateway venue (always) and the podman-hpc at-scale mode
(optional), `lc build` renders the environment into an OCI image.
The generated Containerfile follows Astral's documented multi-stage
pattern, with one deliberate deviation: the interpreter comes from
`uv python install` (the same python-build-standalone build family as
the venv venues) rather than a python base image, keeping the
interpreter source uniform across venues.

- builder stage: digest-pinned base;
  `COPY --from=ghcr.io/astral-sh/uv@sha256:<pinned> /uv /bin/`;
  `uv python install` into `/opt/python`
  (`UV_PYTHON_INSTALL_DIR=/opt/python`, so `pyvenv.cfg` records an
  image-stable interpreter path); `uv sync --locked --exact
  --no-install-project --compile-bytecode` into `/opt/venv`
  (`UV_PROJECT_ENVIRONMENT=/opt/venv`) from `pyproject.toml` +
  `uv.lock` **only** — no project source is ever copied in;
- final stage: slim digest-pinned base + declared system packages +
  `/opt/python` + `/opt/venv` + the `uv` binary (worker-side
  `uv run --locked` needs it), world-readable (`chmod -R a+rX` — no
  uid baking; multi-user hubs map arbitrary uids);
- **baked ENV contract** (the image's, never forwarded from the
  driver): `UV_PROJECT_ENVIRONMENT=/opt/venv`,
  `UV_PYTHON_INSTALL_DIR=/opt/python`, `UV_PYTHON_DOWNLOADS=never`,
  `UV_OFFLINE=1`, `UV_CACHE_DIR=/tmp/uv-cache`,
  `LC_ENV_VERSION=<env_version>`, `PATH=/opt/venv/bin:…`. (The
  existing `_worker_environment` forwards `HOME` from the driver for
  snakemake's sake; the baked contract makes uv indifferent to it.)

**Image identity: the tag hashes the complete rendered build
context.**

```
tag = lc-env-<sha256(rendered Containerfile text ‖ env-input document)[:16]>
```

The rendered Containerfile embeds the base-image digests, the uv
binary digest, the interpreter version, the system-package list, the
`Containerfile.extra` stage, and the generator's output shape; the
env-input document (the same canonical JSON `env_version` hashes)
carries the lock projection and the install-selection settings that
steer the builder's `uv sync`. The base and uv digests are **generator
constants shipped with the engine**; since `lc build` always runs the
locked engine, new constants reach a project only through an engine
relock — at which point tag and `env_version` move together, and the
tag *additionally* distinguishes renderings across engine versions
(two projects on different engine pins never share a tag for the same
lock).
This is deliberately a distinct identity from `env_version`:
`env_version` is what the *project declares*; the tag is what a
*specific rendering* contains. The manifest records both. One honest
residue, stated like the sdist one: the apt layer is name-pinned only,
so two builds of one tag at different times can hold different
system-package *versions* — the build records a `dpkg -l` snapshot
into the image (`/opt/lightcone/dpkg-snapshot.txt`, surfaced into the
manifest as attestation) rather than pretending apt is pinnable.

`lc build` is incremental by construction (tag hit ⇒ no-op);
`lc materialize` on an image venue resolves the tag for the current
build context, triggers the venue's builder if absent — Cloud Build on
the hub, docker/podman locally — or fails with the exact command to
run. **The Cloud Build path is a rework, not a reuse**: the existing
`image_identity`/`_populate_build_context` (which hash Containerfile +
requirements and stage the project tree) are replaced by the new tag
function and a context of exactly three files — rendered
Containerfile, `pyproject.toml`, `uv.lock` — pushed to the same
Artifact Registry with the `lc-env-<hash>` ref shape.

**Declared system packages.** A project needing OS-level libraries
declares them:

```toml
[tool.lightcone]
system-packages = ["texlive-latex-base"]   # apt names; in env_version and the tag
```

feeding the generated Containerfile's apt layer, with
`Containerfile.extra` (a stage `FROM` the derived env image, content
hashed into both `env_version` and the tag) as the documented escape
hatch replacing the fully user-authored Containerfile. On venv venues
these packages are host-provided: `lc doctor` reports presence, and
`worker_runtime: venv` in the manifest marks the weaker attestation.
This is the honest edge of the uv substrate — a project for which
host-provided system deps on the direct path are unacceptable is the
pixi fallback's territory.

**GPU.** CUDA-enabled projects pin their PyTorch index explicitly in
`pyproject.toml` via `[tool.uv.index]` (with `explicit = true`) +
`[tool.uv.sources]` — the only project-mode mechanism (uv's
`--torch-backend` exists solely in the `uv pip` interface; in
particular there is no auto-detection to mis-fire inside image
builds). `lc init --gpu` scaffolds the pinned-index block, choosing
the CUDA level against the **minimum** host driver across the
project's declared venues (a `cu13x` pin can lock out a venue whose
driver trails); `lc doctor` compares the locked CUDA level against
`nvidia-smi` where present, and the manifest records the host driver
as attestation. NVIDIA's `cuda-toolkit` PyPI wheels (including `nvcc`)
cover toolkit needs inside the lock. Multi-node GPU collectives (NCCL
over Slingshot) are out of scope for the dask-over-TCP fabric and are
called out as such.

**Hermeticity posture.** `required-environments` (scaffolded) makes
locking fail when a package **without an sdist** lacks wheels for a
target venue — an upfront error instead of a Perlmutter surprise. It
does not prevent sdist fallback for packages that have one: uv offers
ban-side controls only (`no-build`, `no-build-package`), no
per-package allowlist, so the design does not pretend to a
"wheels-only with opt-in" setting that cannot be expressed. Instead:
lock ingestion reports any sdist-resolved packages, the manifest
records them (`sdist_built`), and projects wanting hard hermeticity
set `no-build = true` themselves (documented, not scaffolded; the
setting is hashed via `install_settings` either way). sdist builds run
the host toolchain — the residue the substrate evaluation already
named — and the attestation keeps it visible.

### `lc doctor` — the environment health surface

Load-bearing in six places above, so specified here rather than
implied: `lc doctor` is read-only, runs in the tool environment, exits
non-zero on hard failures only, and checks — per detected venue —
uv presence/version vs `required-version`; lock/pyproject consistency;
placement-tier usage vs quota; declared system packages present on the
host (venv venues); packaged-project × gateway-venue conflict;
locked CUDA level vs `nvidia-smi`; hub contract items (workdir mount,
registry pull access) where detectable; and sdist/path-dep reports.
Shipped in migration step 4 alongside the placement machinery it
inspects.

### The execution fabric: dask today, with the seam kept explicit

The user-level question "could Ray replace dask here?" was researched
as part of this revision (KubeRay, Ray-on-SLURM, the Ray uv
runtime-env hook, dask-gateway's health). Verdict: **keep dask; keep
the fabric-touching surface confined to `cluster_for_run` + the
executor plugin (it already is); put Ray on a re-evaluation clock.**

- Ray's uv integration (driver launched under `uv run` ⇒ every worker
  re-exec'd under the same flags, `working_dir` auto-shipped) is *this
  design implemented inside a fabric* — independent confirmation of
  the architecture — but it is young: multiple open P1-class issues in
  2025–26 (default hook breaking pip runtime-envs, Python-version
  mismatches, argument parsing), the wrong maturity for a
  reproducibility product's core this year.
- The one capability Ray uniquely adds — shipping code + env to
  workers with **no shared filesystem** (`working_dir` via GCS,
  ≤500 MiB, node-cached) — solves a problem this design has already
  eliminated by construction on every current venue. It is therefore
  the **designated escape hatch** if a shared-FS-less venue ever
  appears (superseding the dask `UploadDirectory` note of earlier
  drafts).
- Where dask is ecosystem-weakest — dask-gateway is in maintenance
  mode (~1 release/year, volunteer-maintained, YARN backend dropped in
  2026.3) — Ray is weakest for lightcone's interaction model: no
  Gateway equivalent, Ray Client is officially "for experts only"
  (30-second disconnect kill, exact version matching), and the blessed
  Ray Jobs API is batch-shaped, inverting the live-driver-in-notebook
  flow `lc materialize` uses on the hub. dask/distributed core itself
  is healthy (monthly releases, funded maintainers).
- Resource semantics map 1:1 (both schedule on logical, unenforced
  cpus/memory/gpus), so a later per-venue swap — e.g. the k8s branch
  to KubeRay RayJob if dask-gateway stalls — is a contained change
  behind the existing seam, not a rewrite. Re-evaluate when: the Ray
  uv hook's P1s close and it is stable-by-default; dask-gateway misses
  another annual release; or a no-shared-FS / Ray-native venue becomes
  a deployment target.

Operational risk, stated: the Gateway venue depends on dask-gateway's
continued maintenance and on the deployment exposing the standard
`image` / `environment` / `worker_cores` / `worker_memory` cluster
options (the code already errors helpfully when it doesn't). The
project lock pins dask-gateway (engine-in-lock covers the client
side), and the `DASK_SCHEDULER_ADDRESS` branch is the documented exit.

**Hub deployment contract** (the pod-level preconditions, now explicit;
consumed by the deployment charts, verified by `lc doctor` on the
hub): worker pods mount the same NFS home at the identical path as the
notebook pod; the pod uid can read the project tree and write
`results/`; nodes hold pull auth for the Cloud-Build target registry
(an unpullable image currently surfaces only as the 600 s zero-worker
timeout — the fingerprint check's workdir probe and `lc doctor`
shorten that loop); the gateway exposes the standard cluster options.

### What this removes and keeps

Removed from the user surface: the authored Containerfile,
requirements.txt, the venv-bootstrap scaffold, the build-input image
tag as identity, per-venue recipe wrapping differences, the
driver/worker engine-version skew on lc-managed branches (and
eventually its tolerance shim), and pixi-substrate machinery from v2
(binary bootstrap, lock-format projection quirks, IDE shims — a uv
`.venv` is natively understood by every IDE).

Kept, slimmed: `lc build` + Cloud Build (reworked to the code-free
rendered context), the site registry (now also carrying placement
tiers), the entire dask cluster/executor fabric, `podman-hpc migrate`
for the at-scale mode, and the manifest chain.

New surface, kept deliberately small: `lc env sync` / `lc env gc`, the
launcher contract, the fingerprint probe, `lc doctor`'s check list,
and the `[tool.lightcone]` table (`system-packages`,
`allow-path-deps`, `venues`, `account` via project config).

## Alternatives considered

- **pixi-canonical (the v2 draft).** Strictly stronger single-file
  pinning (interpreter, BLAS variants, MPI, system libs in one lock)
  and the right choice if this project's direct-path workloads needed
  conda-forge's system layer. The fabric evidence says they don't
  (dask-over-TCP, PyPI CUDA wheels, vendored BLAS); the project survey
  in Open questions closes the remaining presumption. The costs are
  real: a tool agents don't know, a second package manager next to the
  uv that installs `lc`, bespoke bootstrap machinery, IDE shims, an
  order-of-magnitude-smaller ecosystem, and no battle-tested
  workflow-system precedent. **Retained as the documented fallback**
  with explicit triggers (see Open questions): the architecture
  (lock → env_version → derived image) is substrate-shaped, so a pixi
  variant swaps the definition layer without touching the fabric.
- **Container-canonical (v1), hardened.** Digest-pin the base and lock
  inside the image. Still adds code while deleting none, still walls
  laptops behind Docker, still fragments identity across sites without
  new registry infrastructure — and still forces the rebuild loop this
  revision exists to kill. Scored last by all three evaluation judges.
- **uv without the engine-in-lock rule** (lc/snakemake/dask as a
  separate tool layer, only science deps locked). Reproduces today's
  driver/worker skew and requires shipping a second environment to
  workers; the executor's own history (`_unpack_result`) is the
  evidence against it. Rejected.
- **Ray as the fabric now.** Evaluated above; rejected for now on
  maturity of the uv hook and absence of a Gateway-equivalent hub
  story, with defined re-evaluation triggers and a per-venue swap
  path. Ray `working_dir` + uv is the designated escape hatch for a
  shared-FS-less venue.
- **Runtime code-shipping to workers on dask** (`UploadDirectory` /
  wheel upload at connect). Superseded by the Ray escape hatch above;
  rejected while the shared-FS invariant holds.
- **Snakemake-native deployment (`--sdm conda/apptainer`).** Unchanged
  from v2: keys caching on unpinned YAML, silently falls back from pin
  files, no Gateway story, and the plugin framework (PR #3339) remains
  an open draft; the pixi/uv plugins that would build on it (issues
  #3915, #3251) are unstarted. Revisit if the framework merges.
- **Venv + per-harness activation.** Ruled out in v2 for
  activation-plumbing reasons that remain true; `uv run --locked
  --exact` is the run-verb resolution of that whole problem class.

## Migration plan

Stage 1 (substrate-independent, unchanged from v2 — ship first if not
already landed): `run` → `materialize` rename; `lc run CMD` backed by
the current substrate; output-id hint; docs/eval-prompt grammar; tests.

**Stage 2 — uv substrate.** Named constants used below: recursion
guard `LC_DELEGATED=1`; convergence-consent flag
`--accept-containerfile-loss`; `lc verify` pre-migration state
`pre_migration`; the Containerfile diff baseline is "any historical
scaffold template modulo the version-pin line".

1. `lc init`: scaffold `pyproject.toml` (engine dep, required-version,
   required-environments) + `.python-version`; `uv lock`; converge
   existing projects — requirements.txt → dependencies mechanically;
   the authored Containerfile is **refuse-and-report**: if it deviates
   from the baseline beyond the pip-install lines, `lc init` emits the
   diff plus a checklist ("these RUN apt-get lines need
   `[tool.lightcone] system-packages` entries or Containerfile.extra")
   and requires `--accept-containerfile-loss` — never a silent drop of
   a system layer. `lc init` stops writing `container:` into
   astra.yaml; the emitted spec is validated against the current ASTRA
   release.
2. Launcher contract: astra.yaml-first discovery; placement + tier
   selection (env_key incl. install-settings); `UV_*` scrub;
   preflight + first convergence with captured output; per-verb
   direct-exec delegation (`<env>/bin/lc`, `LC_DELEGATED`); engine
   self-verification; unknown-verb delegation; dual `--version`.
3. Engine: `env_version` (the canonical env-input document — golden
   tests including the default-groups flip and a path-dep fixture)
   replaces the image tag inside `code_version`; manifest gains the
   attestation fields (`platform`, `python_build`, `uv_version`,
   `worker_runtime`, `env_tier`, image tag/digest, `sdist_built`,
   per-rule `git_sha`/`git_dirty`, fingerprint strength);
   `SCHEMA_VERSION` bump; `lc verify` reports `pre_migration`
   manifests distinctly. During the ASTRA window the Snakefile
   generator ignores `container:` with a deprecation warning;
   `wrap_recipe`/`resolve_container_spec` are replaced by the uniform
   uv wrapper.
4. Fabric + placement: recipe wrapper → `uv run --locked --exact`;
   `get_python_executable()` override + `--shared-fs-usage` pinned by
   test; generator test for absolute interpreter paths; worker env
   overlay `UV_OFFLINE=1` / `UV_PYTHON_DOWNLOADS=never` on all cluster
   venues including the external branch; `lc env sync`/`lc env gc`
   with leases; scratch-tier fallback; `_abort_on_perlmutter_login`
   updated per the env-sync section; site-registry placement values
   (Perlmutter tiers, `account` resolution); fingerprint probe +
   marker lifecycle; external-branch refusal gate
   (`--allow-unverified-cluster`); driver-side code-state capture;
   run-start env_key capture + worker-side `run_rule` gate;
   `lc doctor`; sweep operator-facing error strings for pre-uv
   remediation advice (`pip install distributed`, sbatch-activation
   wording).
5. `lc build`: Containerfile generator (code-free,
   `--no-install-project`, baked ENV contract, dpkg snapshot);
   rendered-context tag; `[tool.lightcone] system-packages`;
   `Containerfile.extra`; Cloud Build rework (new three-file context
   stager, `lc-env-<hash>` refs, `image_identity`/`compute_image_tag`
   replaced); Gateway branch resolves/builds by tag.
6. ASTRA coordination: environment declaration = project
   pyproject/uv.lock; `container:` derived/optional; WRROC archives
   `uv.lock` as the checkable environment artifact. (Steps 1/3 are
   written to be safe in the interim window regardless of ASTRA's
   timeline.)
7. Eval: re-baseline with the new grammar; the eval doubles as the
   live test that the boundary rule lands better when `lc run`
   requires nothing but uv.
8. Cleanup (post-window, gated on the external-branch fingerprint
   check): drop `_unpack_result`'s bare-int tolerance; mark pixi-era
   design docs superseded.

Test obligations per step, mirroring the repo's test patterns: golden
projection fixtures (step 3); launcher discovery/delegation/self-check
unit tests (step 2); env-sync preflight + lease/gc tests against a tmp
tree (step 4); generated-Containerfile snapshot + tag determinism
tests and a `snakemake -n` parse test (steps 3/5); CliRunner coverage
for the new verbs (steps 1/4).

No backward compatibility (pre-1.0 stance, unchanged): existing
projects converge via `lc init`, existing manifests report as
`pre_migration` under `lc verify`.

**The Perlmutter spike (gates stage-2 step 4 defaults).** One day on
Perlmutter before freezing the SLURM-branch defaults: (a) verify a
no-op `uv run --locked --exact --offline` against the read-only
content-addressed env performs zero writes; (b) measure first-rule and
steady-state wrapper latency at ~32 nodes on `/global/common`; (c)
smoke-test the podman-hpc at-scale mode with `--net host` (and probe
CDI GPU injection); (d) confirm interpreter + venv + cache co-location
and quota headroom for a CUDA-class lock; (e) exercise the
salloc-interactive scratch-tier fallback end-to-end; (f) confirm
concurrent `--exact` syncs serialize cleanly on one env.

## Open questions

1. **Site threshold for the podman-hpc at-scale mode** — node count at
   which SLURM workers switch from shared venv to containers
   (evaluation data suggests ~10–20 nodes as the crossover region);
   per-site override in the registry; gated on the spike.
2. **Project survey / pixi-fallback triggers** — inventory current
   lightcone projects for conda-forge-only needs (BLAS-variant
   control, non-PyPI tools needed on the *direct* path). Triggers that
   would activate the pixi variant of this design: a real project
   blocked by either need, or the CUDA-on-PyPI path regressing.
   Absent a trigger, the presumption in Background stands.
3. **Packaged projects on image venues** — runtime editable install
   into the pod env (writable `/opt/venv`, offline build backend).
   Deferred; `lc doctor` flags the combination.
4. **Per-output environments** — PEP 735 groups map naturally
   (`lc run --group heavy`, per-group projection hashes); deferred
   until a real project needs it; the one-environment rule is exactly
   what this extension would relax deliberately rather than
   accidentally.
5. **ASTRA spec evolution** — the `container:` field's
   derived/optional future and whether the spec should reference the
   environment declaration explicitly; WRROC archiving of `uv.lock`.
   (Cross-repo dependency; migration steps are written to be safe in
   the interim.)
6. **`lc` on alternative install channels** — is `uv tool install`
   plus pip enough, or is a conda-forge package worth maintaining for
   hub base images?
7. **Wheel variants** — PEP 825 (with Astral as co-author) would let
   uv pin hardware-specific binaries natively; re-check early 2027 and
   fold into the GPU guidance when it ships.
8. **Warm-start ergonomics** — `uv run --locked --exact` on a warm env
   is milliseconds on local disk; the spike measures the shared-FS
   figure. If agent loops ever need less, a long-lived shell is a
   contained future optimization, explicitly not built now.
9. **Fabric re-evaluation clock** — revisit Ray per the triggers in
   "The execution fabric" (hook P1s closed / dask-gateway stalls /
   shared-FS-less venue).

## Evidence appendix

- CI eval history (PR #168): runs 1–4 — every remaining failure is
  environment-boundary confusion; evidence for one canonical env
  behind a run verb, substrate-neutral. Detail in
  [environment-substrate-evaluation.md](environment-substrate-evaluation.md).
- Substrate comparison and judge scoring (lockfile-first unanimous),
  identity-mechanism table, durability ranking:
  [environment-substrate-evaluation.md](environment-substrate-evaluation.md).
- Adoption quantification (uv vs pixi), shipped uv-lockfile-first
  systems (Flyte/Union, Metaflow, Modal, ClearML), canonical uv Docker
  pattern, CUDA-on-PyPI status, PEP 817/825 timeline:
  [uv-vs-pixi-adoption.md](uv-vs-pixi-adoption.md).
- Fabric ground truth: `src/lightcone/engine/dask_cluster.py` (four
  cluster branches, run-scoped lifecycle, `LIGHTCONE_WORKER_IMAGE`
  attestation, `_worker_environment` HOME forwarding),
  `src/snakemake_executor_plugin_dask/executor.py`
  (child-snakemake-on-worker model, `cd workdir_init`,
  `_unpack_result`), `src/lightcone/cli/commands.py`
  (`_build_snakemake_cmd` shared-fs-usage mitigation,
  `_abort_on_perlmutter_login`), `src/lightcone/engine/cloudbuild.py`
  (the context stager being replaced).
- Review-verified uv semantics (uv 0.12.3, empirical): `--locked`
  errors on stale lock and never re-resolves; `uv run` syncs
  additively by default and `--exact` removes extraneous packages;
  `--no-sync` silently disables the `--locked` check; a warm read-only
  venv runs fine under `--offline`, including git-sourced deps, with
  an empty cache; stale-lock detection is a local check; `--no-dev`-
  built envs trigger runtime installs under default `uv run`
  (motivating the one-environment rule); flipping `[tool.uv]
  default-groups` changes the installed set while `uv.lock` is
  byte-identical (motivating `install_settings` in the identity);
  `uv run` falls back to system PATH for commands absent from the env
  and only warns outside a project (motivating the launcher's
  discovery + self-verification); uv's project walk-up can resolve a
  different project than astra.yaml discovery; uv leaves old env paths
  behind when `UV_PROJECT_ENVIRONMENT` moves (motivating gc); venv
  recreation on interpreter change deletes marker files; virtual
  projects install deps only; `UV_PROJECT_ENVIRONMENT` is honored as
  an absolute path, per-project by design; the cache is
  concurrency-safe and must share a filesystem with the env for
  hardlinking; `--torch-backend` is uv-pip-interface-only;
  `required-environments` guards only sdist-less packages;
  path/directory lock entries carry no content hash and are
  revalidated at run time.
- Ray-vs-dask research (2025–26): Ray uv hook (Ray 2.43+, default-on
  later) + open P1s; `working_dir` limits; Ray-on-SLURM community docs
  + `ray symmetric-run`; KubeRay maturity vs dask-gateway's ~1
  release/year cadence; Ray Client "experts only"; no Snakemake–Ray
  executor exists; resource-semantics parity.
