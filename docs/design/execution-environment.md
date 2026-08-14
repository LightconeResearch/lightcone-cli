# Design: the locked environment is the execution environment

- **Status:** draft for review (v2 — supersedes the container-canonical
  v1 draft; evidence base in
  [environment-substrate-evaluation.md](environment-substrate-evaluation.md))
- **Date:** 2026-08-14
- **Scope:** lightcone-cli CLI surface, `lc init` scaffold, engine
  environment handling, eval prompt
- **Coordination required:** ASTRA spec (the `container:` field becomes
  derived/optional; an environment declaration takes its place) — see
  Open questions
- **Not in scope:** hub/Kubernetes deployment changes

## Summary

A Lightcone project's reproducibility guarantee — every materialized
output tied to a known, pinned environment — currently rests on a
content-addressed container image. The evaluation found that this
identity does not actually pin an environment: the tag hashes *build
inputs* (Containerfile + requirements.txt) while the base image and pip
resolution are unpinned, so the same tag can name different environments
at different build times.

This revision replaces the substrate rather than patching it:

1. **`pixi.toml` + `pixi.lock` become the single source of truth for
   the project environment.** The lock pins every artifact — including
   the Python interpreter, BLAS, compilers, and MPI — by URL and sha256,
   per platform, in one committed file. The environment identity in
   every manifest becomes a hash of the lock's resolved artifacts.
2. **The container is demoted from definition to transport.** An OCI
   image is *derived from the lock* (never user-authored) and used only
   where a site physically requires one (GKE, where the pod is the
   image) or where performance mandates one (Perlmutter at 100+ nodes,
   per NERSC's own benchmarks). Elsewhere, recipes and interactive work
   run in the locked environment directly.
3. **`lc run` is renamed `lc materialize`**, and **`lc run CMD [ARGS…]`
   becomes the environment runner** — one command executed in the
   locked environment, identically on every site and under every agent
   harness. Bare `lc run` opens a shell in the environment.
4. **Agents are taught a boundary rule, not an enforcement mechanism**:
   project-code execution goes through `lc run`; project tooling and
   everything else stays on the host.

This is the shape all three evaluation judges independently selected,
and the pattern the ecosystem has converged on (Nextflow Wave /
nf-core, Snakemake's pin-file + `--containerize` guidance, the
SciPy/Carpentries pixi-to-Apptainer teaching standard): lockfile as
authority, container as per-site rendering.

## Background

### What the eval evidence actually established

The CI evals' most consistent failure mode is environment-boundary
confusion:

- **Eval run 1**: first `lc run` failed with `ModuleNotFoundError:
  scipy` — the recipe environment didn't have what the debug
  environment had.
- **Eval run 3**: the agent's host-side smoke test failed on `pandas`
  (declared in `requirements.txt`, absent from the harness venv), and
  the agent "fixed" it by installing into the harness's own tool venv.
- The automated trace analysis twice asked for a cheap way to execute
  commands in the declared environment interactively.

Every one of these is a package-presence error. What they establish is
the need for **one canonical environment reachable through a run
verb** — they do not establish that the environment must be a
container. Any blessed environment behind `lc run` would have prevented
all of them. The substrate choice therefore rests on reproducibility
strength, operational fit, and tooling cost — the evaluation's subject.

### Why the container-canonical v1 draft was revised

Three findings, in order of weight (full detail in the evaluation
report):

1. **The identity flaw.** The image tag = sha256(Containerfile +
   dependency files). With `FROM python:3.12-slim` unpinned and `pip
   install -r requirements.txt` unresolved, rebuilding the "same" tag
   months apart yields different environments — and `code_version`,
   which embeds the tag, cannot see the drift. Fixing this inside the
   container substrate requires a lockfile toolchain anyway, plus a
   registry and archival story for digests (images here are local,
   registry-less, and non-reproducibly built — each site's rebuild gets
   a *different* digest). Once a lockfile exists, it is the stronger
   and more durable identity: conda-forge and PyPI have no-deletion
   norms; container registries have retention policies.
2. **The laptop bar.** The stated principle is "get going with just uv
   or pixi". Container-canonical makes Docker Desktop/podman a hard
   requirement on laptops — the worst operational score of every
   candidate on macOS.
3. **The scale boundary runs the other way too.** NERSC's benchmarks
   show shared-filesystem environments at parity with containers up to
   ~10 nodes and strongly disfavored at 100+. Containers are needed *at
   scale on HPC* and *always on GKE* — but that makes them a transport
   for specific venues, not the definition of the environment.

### Why the venv path is a dead end (corrected)

The v1 draft rejected the venv on three grounds. Re-examined, two of
them were arguments against *activation*, not against non-container
substrates — and they do not transfer:

1. ~~"It isn't versioned."~~ A committed lockfile is versioned, and
   more pinned than the current Containerfile + requirements.txt pair.
   `pixi run` re-materializes the environment from the lock on a fresh
   clone automatically.
2. **"Activation cannot be plumbed portably" — true, and moot.** The
   empirical findings stand (Claude Code settings `env` values are
   literal; `BASH_ENV` PATH precedence is rebuilt away; `CLAUDE_ENV_FILE`
   is Claude-only; Codex closed all env-file proposals). But run-verb
   tools need no activation at all: `pixi run CMD` resolves and enters
   the environment in-process. The whole activation problem was an
   artifact of the venv model.
3. **"Even a perfectly-activated venv is the wrong environment" —
   true for venvs, mostly false for pixi.** A venv shares the host's
   Python, system libraries, and BLAS. A pixi environment ships its own
   interpreter, BLAS, compilers' runtimes, HDF5, and MPI from
   conda-forge — the named gaps are pinned. What remains outside the
   lock: host glibc (conda-forge floor 2.17 — every target clears it),
   the kernel, and tools absent from conda-forge/PyPI (see the escape
   hatch).

The venv is retired from the project entirely — not just from the agent
workflow. The pixi environment serves IDEs and Jupyter too
(`.pixi/envs/default/bin/python` is a normal interpreter path).

## Design principles

- **Honest identity.** The manifest records an environment identity
  that names the *resolved* environment (artifact URLs + hashes), not
  the inputs to a non-deterministic build. We claim pinned environment
  identity — never bit-for-bit outputs (BLAS kernel dispatch and thread
  scheduling vary by hardware regardless of substrate).
- **One source of dependencies.** `pixi.toml` declares, `pixi.lock`
  pins, everything else — direct execution, podman-hpc images, GKE pod
  images — is derived from it.
- **Harness-agnostic by being boring.** No settings plumbing, no env
  files, no hooks required for correctness. A plain CLI command works
  identically under Claude Code, Codex, CI, and a human terminal.
- **Opt-in, never imposed.** Agents legitimately work outside the
  project. The locked environment is where *project code* runs, not a
  cage around the agent.
- **Stateless.** No long-lived container, no sessions, no lifecycle to
  manage or leak.

## The design

### Environment definition: `pixi.toml` + `pixi.lock`

`lc init` scaffolds a `pixi.toml` (platforms: `linux-64`, `osx-arm64`;
default deps mirroring today's scaffold) and runs the initial solve to
produce `pixi.lock`. Both are committed. `requirements.txt` and the
user-authored `Containerfile` leave the scaffold; existing projects are
converged by `lc init` (see Migration).

pixi itself is a single static binary, not installable via uv/PyPI (the
PyPI `pixi` package is unrelated). `lc` treats it as the one extra
easy-install tool the principles allow: when absent, `lc` offers to
bootstrap the checksum-verified release binary into `~/.lightcone/bin`
(sha256 files and attestations ship with every release), or prints the
one-line official install command. The bootstrapped pixi version is
pinned in `lc`'s config to avoid an upgrade treadmill.

### Environment identity

`code_version`'s environment input changes from the resolved image tag
to `env_version`: a sha256 over a **normalized projection of the lock**
— the per-platform sorted list of artifact URLs + hashes — rather than
the raw file bytes. This changes exactly when the resolved environment
changes, and is stable across pixi lock-format bumps (the raw file was
rewritten at format v7 with zero environment change; hashing bytes
would have spuriously invalidated every output).

The manifest additionally records, outside `code_version`:

- `platform` (the lock identity is per-platform: the same lock hash
  resolves to different binaries on osx-arm64 vs linux-64),
- `transport` (`direct` | `podman-hpc` | `kubernetes`),
- the image digest, when a transport image was used (attestation, not
  identity).

Invalidation semantics are unchanged in kind from today — editing
dependencies already changed the image tag and hence every
`code_version` — but are now truthful: `env_version` moves iff the
resolved environment moves.

### `lc run CMD [ARGS…]` — the environment runner

```
lc run python src/fit.py --optimizer nelder_mead --output /tmp/probe
lc run python -c "import scipy; print(scipy.__version__)"
lc run            # bare: interactive shell in the environment
```

Semantics:

- **Wraps `pixi run --frozen`** — the lock is executed exactly as
  committed; a stale lock (manifest edited without re-solving) fails
  with a pointed message (`run: pixi lock is stale — run 'pixi lock'
  or 'lc env sync'`), never silently re-resolves.
- **Stateless, exit code propagated, stdio inherited** — identical
  under every harness.
- **Same environment as materialization by construction** on the
  direct transport, and same *lock* on every transport.
- On sites whose transport is an image (kubernetes), `lc run` executes
  on the host/pod directly — the pod already is the rendered lock.

The boundary rule and its litmus test survive verbatim from v1, with
one word changed: **"if a `ModuleNotFoundError` in this command would
mean 'fix `pixi.toml`', it belongs in `lc run`."**

One honest loss versus v1: the container wrap mounted only `$PWD`,
which mechanically enforced path discipline. Direct execution sees the
host filesystem. The boundary rule becomes convention plus the manifest
backstop on the direct path — acceptable (the manifest chain, not the
mount, was always the guarantee that mattered), but stated here rather
than discovered later.

### `lc materialize [OUTPUTS…]` — the provenance path (renamed from `lc run`)

Behavior preserved: generate the Snakefile, walk the DAG, execute
recipes, write manifests. The recipe wrapper changes per transport:

| Venue | Transport | Recipe wrapping |
|---|---|---|
| Laptop, HPC jobs ≲ site threshold | **direct** (default) | `pixi run --frozen bash -c <recipe>` |
| Perlmutter at scale (site config, default threshold ~16 nodes) | **podman-hpc** | current wrap: `podman-hpc run --rm -v "$PWD":"$PWD" -w "$PWD" <derived image> …` |
| JupyterHub / GKE | **kubernetes** | unwrapped — the worker pod is the derived image |

Snakemake stays entirely env-oblivious (no `--sdm`, no directives) —
recipes remain opaque shell strings, which the evaluation confirmed is
fully supported and cheaper than the still-unmerged Snakemake
software-deployment plugin framework. Revisit if PR #3339 merges and a
pixi plugin ships.

**Pre-materialization rule (hard requirement):** the driver runs
`pixi install --frozen` once before any fan-out; workers only ever
execute warm (`pixi run` on an installed env is safe; concurrent cold
installs on Lustre race — prefix-dev/pixi#5476). On Perlmutter the
environment lives on `/global/common/software` (small-file-optimized,
read-only on compute nodes) via pixi's detached-environments config,
managed by the site registry.

**Rename rationale (unchanged from v1).** `<tool> run CMD` is the
dominant prior (`uv run`, `pixi run`, `poetry run`, `docker compose
run`) — now literally true, since `lc run` wraps `pixi run`. DAG
engines use a domain verb (Dagster `materialize`, DVC `repro`), and
"materialize" is already lightcone's own vocabulary. The old-grammar
guardrail also survives: when `lc run`'s first argument matches a
declared output id, print `did you mean: lc materialize best_fit?`.

### Derived images (the transport renderer)

For the podman-hpc and kubernetes transports, `lc build` renders the
lock into an image using the documented multi-stage pattern
(`ghcr.io/prefix-dev/pixi` build stage, `pixi install --frozen`, copy
env + `pixi shell-hook` entrypoint into a slim final stage, prefix path
held constant across stages — pixi envs are not relocatable). The
Containerfile is generated, never user-authored; image content is a
function of the lock, so the image tag can remain content-addressed —
now over something that actually pins the environment. The GCP Cloud
Build path is retained as-is for the no-OCI-runtime hub case, building
the same generated Containerfile. The orchestration layer (lightcone-cli
+ snakemake + dask) stays a separate image layer, outside the project
lock, exactly as today.

**Escape hatch:** a project needing dependencies outside
conda-forge/PyPI (TeX, proprietary tools) may provide
`Containerfile.extra` — a stage built `FROM` the derived env image.
Its content is hashed into the image identity and recorded in the
manifest. This is the documented exit, replacing the fully user-authored
Containerfile.

### What custom code this removes and keeps

Removed from the user surface: the authored Containerfile, the
requirements.txt convention, the venv scaffold, and the build-input tag
as identity. Kept, slimmed and demoted: runtime detection, the recipe
wrap, `podman-hpc migrate`, and Cloud Build — the same residual
machinery every surveyed lockfile-first system also keeps (Wave,
nf-core, `snakemake --containerize`).

## Alternatives considered

- **Container-canonical (the v1 draft), hardened.** Digest-pin the base
  and lock inside the image. Adds code while deleting none; requires a
  container runtime on laptops; and without a shared registry (new
  infrastructure) the hardened identity fragments per site because
  image builds are not reproducible. Scored last by all three
  evaluation judges.
- **Pixi-only, no containers.** Contradicts NERSC's own scaling data
  (containers strongly urged at 100+ nodes) and the GKE pod-is-image
  requirement — would delete working podman-hpc machinery only to
  rebuild it.
- **uv-only.** Smallest install bar, but uv.lock attests only the
  Python layer: Cray MPICH needs an unlocked host sdist build, CUDA
  and non-Python tools are unpinned. The manifest would certify a
  sameness it cannot see — worse for scientific provenance than an
  admitted gap. Remains the degradation path if pixi's governance or
  stability fails.
- **Snakemake-native deployment (`--sdm conda/apptainer`).** Conda env
  caching keys on the YAML spec (not a lock) and pin files silently
  fall back to unpinned YAML — no reproducibility upgrade; no story for
  GKE; the generic plugin interface is a 17-month-open draft PR and
  pixi support is a no-PR backlog issue. (One v1 claim corrected:
  Snakemake's `container:` does accept local SIF paths — the registry
  argument in `docs/architecture.md` should be reworded, though the
  podman-hpc-storage mismatch stands.)
- **Remote sandbox services / agent-lives-in-container /
  container-first tooling.** Unchanged from v1: wrong shape for
  sessions; already migrated off.
- **Venv + per-harness activation.** Ruled out; see Background — with
  the correction that the fatal grounds were venv-specific, not
  substrate-general.

## Migration plan

Two stages, each shippable alone; the v1 no-backward-compat stance
holds (pre-1.0, no deprecation period).

**Stage 1 — the substrate-independent core (unchanged from v1, ship
first):** rename `run` → `materialize` in `commands.py`; add `lc run
CMD` backed by the *current* container substrate; output-id hint; docs,
CLAUDE.md, eval prompt grammar; tests. Nothing in stage 1 is thrown
away by stage 2 — only what backs the verb changes.

**Stage 2 — lockfile-first substrate:**

1. `lc init`: scaffold `pixi.toml`, solve, converge existing projects
   (generate pixi.toml from requirements.txt; retire venv and authored
   Containerfile); pixi presence check + bootstrap.
2. Engine: `env_version` (normalized lock projection hash) replaces the
   image tag in `code_version`; manifest gains `platform`, `transport`,
   optional image digest; `SCHEMA_VERSION` bump. `lc verify` treats
   pre-migration manifests as a distinct, reportable state (not
   `broken_chain`).
3. Wrapper: transport selection (direct default; site-config threshold
   for podman-hpc; kubernetes unchanged); driver-side
   `pixi install --frozen` before fan-out; detached-env placement on
   Perlmutter via site registry.
4. `lc build`: generate the multi-stage Containerfile from the lock;
   `Containerfile.extra` escape hatch; Cloud Build path reuses it.
5. ASTRA coordination: environment declaration in the spec;
   `container:` becomes derived/optional. WRROC export archives
   `pixi.lock` in the crate as the checkable environment artifact.
6. Eval: rewrite prompt/seeds for the new grammar; re-baseline
   turns/cost — the eval doubles as the live test of whether the
   boundary rule lands better when `lc run` needs no runtime installed.

## Open questions

1. **Site threshold for the podman-hpc transport** — default node count
   at which materialization switches from direct to image transport
   (evaluation data suggests ~10–20 nodes); per-site override in the
   registry.
2. **`lc` on conda-forge** — publishing lightcone-cli to conda-forge
   would make "just pixi" fully true (today `lc` itself arrives via
   uv). Worth it, or keep uv as the documented installer?
3. **NERSC spike** — the no-container MPI story (conda-forge
   `mpich=*=external_*` binding Cray MPICH under pixi) inherits a
   documented conda mechanism but has no pixi-specific NERSC
   endorsement. One day on Perlmutter before committing the direct
   transport's MPI claim.
4. **Per-output environments** — v1's per-recipe containers question,
   generalized: pixi named environments (`[feature]`/`[environments]`)
   map naturally to per-output envs in one lock; defer until a real
   project needs it, but the identity scheme (per-environment
   projection hash) should not preclude it.
5. **GPU locking ergonomics** — locking linux-64 CUDA envs from a mac
   laptop needs `CONDA_OVERRIDE_CUDA`; decide what `lc init` sets and
   what the manifest records about the host driver.
6. **Warm-shell ergonomics** — `pixi run` startup on a warm env is
   fast; if agent loops ever need faster, a long-lived shell is a
   contained future optimization — explicitly not built now (v1's
   position, unchanged).

## Evidence appendix

- CI eval history (PR #168): run 1 — 51 turns, $1.81, includes the
  scipy failure; run 2 — 34 turns, $1.13, zero errors with grammar
  guidance; run 3 — 59 turns, $1.43, host-side pandas probe and
  harness-venv pollution; run 4 — 46 turns, $1.06, residual
  host/container split flagged. Pattern: every remaining failure is
  environment-boundary confusion — evidence for one canonical env
  behind a run verb (substrate-neutral).
- Substrate comparison, judge scoring (D unanimous), fact-check
  corrections (Perlmutter SLES 15 SP6 / glibc 2.38 since 2026-02;
  Snakemake local SIF support; Docker Hub retention-policy history),
  and the full risk register:
  [environment-substrate-evaluation.md](environment-substrate-evaluation.md).
