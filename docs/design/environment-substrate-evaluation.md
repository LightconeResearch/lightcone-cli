# Evaluation: the execution-environment substrate (containers vs pixi vs uv)

- **Status:** findings report — evidence base for the revised
  `execution-environment.md`
- **Date:** 2026-08-14
- **Method:** 15-agent investigation — 7 parallel researchers (pixi,
  pixi-on-HPC/k8s, uv, Snakemake-native deployment, reproducibility
  fidelity, prior art, adversarial critique of the draft design), a
  3-judge panel scoring 4 candidate architectures from independent
  lenses, a completeness critic, and 4 adversarial fact-checks of
  load-bearing claims. Repo claims verified against source at the cited
  lines.

## TL;DR

**Recommendation: lockfile-first, pixi-preferred.** `pixi.toml` +
`pixi.lock` become the single source of truth for the environment and
the manifest's environment identity (a hash of the lock's resolved
artifacts). The container stops being the environment *definition* and
becomes a derived *transport* of the locked environment, kept only where
a site physically requires an image (GKE pod-is-the-image) or where
performance mandates one (Perlmutter at 100+ nodes). All three judges
picked this shape independently; it is also the pattern the ecosystem
has converged on (Nextflow Wave / nf-core, Snakemake's own pin-file +
`--containerize` guidance, the SciPy/Carpentries pixi-to-Apptainer
teaching standard).

The single most consequential finding is about the status quo, not the
alternatives:

> **The current guarantee does not hold.** The image tag hashes *build
> inputs* (Containerfile + requirements.txt — `container.py:368`), while
> the base image is an unpinned `python:3.12-slim` and dependencies are
> installed with unpinned `pip` (`commands.py:507`). The same tag —
> and therefore the same `code_version` — can name different resolved
> environments at different build times. Whatever substrate is chosen,
> the manifest's environment field must become either a resolved image
> digest or a lockfile hash. No system surveyed uses an unpinned
> build-input hash as its environment identity.

## What the eval evidence actually shows

The draft design's evidence (CI eval runs 1–4) was re-examined
adversarially. Every observed failure — `ModuleNotFoundError: scipy`,
the host-side `pandas` probe, harness-venv pollution — is a pure
package-presence error. Any single blessed environment reachable through
a run verb (`lc run` backed by a container, pixi, uv, or even a venv)
would have prevented all of them identically. The trace-analysis request
for "exec-in-container" presupposes the current substrate; the
underlying need is *exec-in-the-declared-environment*, which is
substrate-neutral.

Conclusion: the eval evidence settles "one canonical env + a run verb".
It does **not** settle container-vs-lockfile. That decision must rest on
reproducibility strength, operational fit, and tooling cost — which is
what the rest of this report weighs.

Likewise, of the draft's three venv rejections, two do not transfer to
lockfile substrates: "not versioned" (a lockfile is versioned, and more
pinned than the current image inputs) and "activation cannot be
plumbed" (run-verb tools need no activation — the draft itself cites
`uv run`/`pixi run` as the dominant prior). Only "wrong environment"
(host Python/BLAS/system libs) discriminates substrates — and
conda-forge pins the interpreter, BLAS, and most system libraries,
leaving only kernel/glibc-floor and non-conda-forge tools as
container-exclusive territory.

## What each identity mechanism actually pins

| Mechanism | Pins | Residue (unpinned) | Durability of "same id ⇒ same env" |
|---|---|---|---|
| **Build-input image tag (today)** | nothing durable — hashes the *inputs* to a non-deterministic build | base image drift, pip re-resolution | **fails over time**; the identity claim is false |
| **Digest-pinned OCI image** (base by digest + lockfile inside) | full userland: glibc, system libs, Python, wheels | kernel, hardware, GPU driver | deepest per-build identity, but images are local and registry-less here (`--pull=never`); builds are not reproducible, so laptop/Perlmutter/Cloud Build rebuilds of the same inputs yield *different* digests — a single cross-site identity requires new registry + archival infrastructure |
| **pixi.lock** | every conda artifact by URL + sha256 — including the Python interpreter, BLAS, compiler runtimes, MPI — plus PyPI wheels, per platform | host glibc (conda-forge floor 2.17), kernel; PyPI sdists/editable installs; host leakage (`LD_LIBRARY_PATH`, CUDA driver) | strong: conda-forge has an explicit no-deletion policy; identity is per-platform (same lock hash ⇒ different binaries on macOS vs linux-64 — record platform alongside) |
| **uv.lock** | Python wheels/sdists by sha256, one universal cross-platform file; uv-managed interpreter via `.python-version` (pin an exact patch) | everything outside PyPI: system libs, compilers, MPI, non-Python tools; sdist builds use the host toolchain | durable (PyPI immutable after 72h) but shallow — the largest residue of the pinned options |

Two important honesty notes that apply to *every* mechanism:

- None yields bit-for-bit output reproducibility (BLAS kernel dispatch
  per CPU, thread scheduling, GPU nondeterminism). The claim lightcone
  can make is **pinned environment identity**, never bit-identical
  outputs.
- Artifact durability ranks: conda-forge (no-deletion policy) ≈ PyPI
  (immutable post-72h) > container registries (retention is policy-
  governed; Docker Hub has twice announced deletion policies and twice
  reversed them — no contractual guarantee either way) > the current
  tag (nothing archived at all). Lockfile identities can re-materialize
  from public archives; digest identities need the specific blob to
  survive.

## Substrate deep-dives (key verified facts)

### pixi

- `pixi.lock` (format v7) pins per-environment, per-platform: conda
  packages by URL + version + build string + sha256/md5; PyPI deps as
  wheels + sha256. Checksums re-verified at install.
- One `pixi.toml` with `platforms = ["linux-64", "osx-arm64"]` solves
  and locks all platforms from any host — one lock covers mac laptop +
  Perlmutter + GKE image.
- **Not pip/uv-installable** — the PyPI `pixi` package is an unrelated
  Pixiv downloader. It *is* a single static binary with per-release
  `.sha256` files and attestations, so `lc` could bootstrap it with
  checksum verification, or it's a fair "one extra easy tool"
  (curl/homebrew) under the stated principles.
- `pixi run CMD` needs zero shell integration; `--frozen` executes
  exactly the lock. `pixi shell-hook` emits a standalone activation
  script usable without the pixi binary (batch jobs, containers).
- conda-forge covers BLAS/LAPACK, HDF5, compilers, and MPI — including
  `mpich=*=external_*` builds that bind Cray MPICH on Perlmutter (this
  is NERSC's own documented mpi4py recommendation). Caveat from the
  critic: pixi-specifically this is *operationally unverified* at NERSC
  — it inherits the conda-forge mechanism but no NERSC doc endorses
  pixi by name.
- Official container path: `ghcr.io/prefix-dev/pixi` images + a
  documented multi-stage pattern (`pixi install --locked` in a build
  stage, copy env + `shell-hook` entrypoint into a slim final stage) —
  image contents become a function of the lock. `pixi-pack` produces
  self-extracting offline archives (wheels only, no sdists).
- Maturity: pre-1.0 (0.76.2, Aug 2026), ~4 releases/month; lock format
  at v7 (bumped May 2026), backward- but not forward-compatible.
  Governance: VC-funded prefix.dev; the underlying rattler library was
  adopted by the conda org in 2024.
- Known operational trap (verified, closed-with-workaround): concurrent
  cold `pixi install` from many SLURM tasks on Lustre races
  (prefix-dev/pixi#5476). A pixi maintainer confirms `pixi run` is safe
  once the env is installed. **Design consequence: `lc` must
  pre-materialize the env once (driver side) before any fan-out; workers
  only ever run warm.**

### pixi on HPC — the scale boundary

A pixi env on disk is a standard conda prefix: multi-GB, tens of
thousands of small files, inheriting every shared-filesystem pathology
NERSC documents for conda envs. NERSC's published benchmarks
(`python-shifter` page): shared-FS Python imports vs containers are at
parity around 10 nodes (~4.9s vs ~4.3s), then diverge superlinearly —
~17s vs ~7s at 100 nodes, ~49s vs ~14s at 500. NERSC "strongly urges"
containers at 100+ nodes; podman-hpc's squashfs migration is the
site-blessed path (demonstrated at 900 nodes). Mitigation for the
direct path at small scale: place envs on `/global/common/software`
(read-only on compute nodes, small-file-optimized) via pixi's `[cache]`
/ detached-environments config.

**Conclusion: pixi and containers are complements at scale, not
substitutes.** Direct locked-env execution is right up to ~10–20 nodes;
an image rendered *from the same lock* is right beyond that.

### uv

- `uv.lock` is a universal cross-platform lockfile (one hash across
  OSes); `uv run --frozen` on a fresh clone with only uv installed
  reproduces the Python layer deterministically, including the
  interpreter (pin an exact patch in `.python-version`).
- `tool.uv.required-environments` can force lock-time failure if any
  package lacks a wheel for a named platform — turning the
  sdist-hermeticity hole into an upfront error.
- The holes are real for this project's workloads: mpi4py's PyPI wheels
  bundle MPICH/OpenMPI-ABI, not Slingshot-optimized Cray MPICH —
  performant Perlmutter use requires an sdist build against the host
  `cc`, an unlocked toolchain step. CUDA needs per-backend index
  plumbing and leaves the host driver unpinned. Non-Python tools have
  no representation at all.
- Fact-check correction: Perlmutter moved to SLES 15 SP6 (glibc 2.38)
  in Feb 2026, so both manylinux_2_28 and _2_34 wheels install fine —
  the glibc drift risk raised during research is moot.
- Verdict: uv.lock is an honest identity for the *Python layer only*.
  As the sole substrate it would certify "same environment" the manifest
  cannot actually see (BLAS, MPI, toolchains) — for scientific
  provenance, worse than an admitted gap.

### Snakemake-native deployment (can we delete code?)

Not today, and adopting it would *weaken* the guarantee:

- Stable `--sdm` offers only conda and apptainer. Conda env caching
  keys on the MD5 of the env *YAML* (a spec, not a lock); the pin-file
  mechanism (`snakedeploy pin-conda-envs`) **silently falls back to the
  unpinned YAML on failure** — best-effort, no better than today's
  build-input tags.
- The generic software-deployment plugin interface (`software:`
  directive, PR #3339) has been an open draft for ~17 months; pixi
  support is a no-PR backlog issue (#3915) that depends on it; the
  rootless-container plugin is explicitly WIP against the unmerged
  framework. Building on this now contradicts the minimal-custom-tooling
  principle more than the existing 5-line recipe wrapper does.
- Fact-check correction: Snakemake's `container:` *does* accept local
  SIF paths first-class (no registry needed) — the architecture doc's
  registry-based rationale for avoiding `--sdm apptainer` is overstated
  and should be reworded (the real residue: podman-hpc-storage tags
  aren't directly usable; a `podman save` → SIF conversion would fit).
- Nothing in Snakemake addresses the GKE pod-is-the-image case.
- The env-oblivious wrapper approach (recipes as opaque shell strings)
  is fully supported and unaffected — swapping what the wrapper wraps
  (`pixi run …` instead of `docker run …`) requires nothing from
  Snakemake. Revisit if #3339 merges and a pixi plugin ships.

### Prior art — where the ecosystem converged

The convergent pattern across every system surveyed: **a solver-agnostic
env spec is the source of truth, a lockfile pins it, and a container is
a derived per-site rendering.**

- **Nextflow Wave / nf-core** is the purest expression: per-process
  conda specs auto-built into frozen Docker *or* Singularity images
  (build templates include `conda/pixi:v1`), with a generated lockfile
  retained per container; nf-core migrated ~1300 modules to exactly
  this.
- **Snakemake's own guidance**: per-rule conda + pin files, optionally
  wrapped in apptainer; `--containerize` derives the Dockerfile from
  the conda specs.
- **Metaflow** `@conda`/`@pypi`: resolved per-step envs snapshotted to
  an object store, reused identically across local/k8s/Batch — pinned
  envs without user-built containers.
- **Dagster/Prefect/DVC** have no per-task env story (image per
  deployment, or the user's problem) — no help here.
- **Nix/Bazel hermeticity** is essentially absent from scientific
  practice (language overhead, privilege models, conda-ecosystem
  incompatibility) — rightly out of scope.
- No surveyed system derives its environment identity from an unpinned
  Dockerfile's build inputs.

## The candidates and the verdict

All candidates keep the substrate-independent core of the draft design:
the `lc run` → `lc materialize` rename, `lc run CMD` as the environment
runner, the boundary rule, and the manifest chain.

- **A. Container-canonical (draft + hardening):** digest-pin the base,
  lockfile inside the image, keep container.py/cloudbuild.py, laptop
  requires docker/podman.
- **B. Pixi-canonical:** pixi everywhere; containers only for k8s.
- **C. uv-canonical:** uv.lock + `uv run` everywhere; containers only
  for k8s.
- **D. Hybrid lockfile-first:** the lockfile is the single source of
  truth and identity hash; the container is an optional derived
  transport (k8s always; Perlmutter at scale), never user-authored.

Judge scores (custom-code ↓ / install bar / repro strength / agent-agnostic / operational):

| Candidate | simplicity judge | reproducibility judge | operations judge | winner votes |
|---|---|---|---|---|
| A | 2/2/4/5/3 | 2/2/4/5/3 | 2/2/4/5/3 | 0 |
| B | 4/5/4/5/3 | 4/5/5/5/3 | 4/5/5/5/4 | 0 |
| C | 5/5/3/5/3 | 5/5/3/5/3 | 5/5/3/5/3 | 0 |
| D | 3/5/5/5/5 | 3/5/5/5/5 | 3/5/5/5/5 | **3/3** |

Why the unanimity holds up under scrutiny:

- **A** is the only candidate that *adds* custom code while deleting
  none, is the worst mac-laptop story (Docker Desktop/colima, arm64
  emulation), and — because images here are local, registry-less, and
  non-reproducibly built — its "hardened digest identity" fragments per
  site unless new registry/archival infrastructure is added.
- **B** contradicts the HPC research it depends on: NERSC's own numbers
  say containers are necessary at 100+ nodes, so B would delete working,
  site-blessed podman-hpc machinery only to rebuild it later.
- **C** cannot honestly meet the guarantee for this project's HPC
  workloads (Cray MPICH, CUDA, non-Python tools).
- **D** is the only candidate with no bad site, and its residual custom
  code (lock → image rendering) is precisely the code every surveyed
  system also keeps.

**Refinement adopted from the simplicity judge and critic: narrow D to
pixi-only** (rather than "pixi preferred, uv accepted"). Dual-lockfile
support was D's main maintenance smell; uv remains how users install
`lc` itself and how dev tooling runs, but the *project environment* has
one definition.

## Risks and open design questions (from the adversarial passes)

Carried into the revised design doc; the material ones:

1. **Invalidation blast radius.** Hashing the whole lockfile into
   `code_version` means any dependency addition re-materializes every
   output. This is not a regression (the image tag already changes
   whenever requirements.txt does), but lock-format bumps (v7 was May
   2026) would add *spurious* invalidation. Mitigation: hash a
   normalized projection of the lock (the per-platform sorted list of
   artifact URLs + hashes), not the raw bytes — stable across format
   churn, still changes exactly when the resolved env changes.
2. **Transport must be recorded.** The same lock hash would label
   outputs produced host-side (full filesystem visibility) and
   in-container (restricted mount). The manifest should record
   `transport` (direct | podman-hpc | kubernetes) and the image digest
   when one was used, as attestation fields outside `code_version`.
3. **Orchestration-stack layering.** The worker pod image must carry
   lightcone-cli + snakemake + dask; today that's a deliberate separate
   layer outside requirements.txt. Under lockfile-first, the derived
   image keeps that layer; whether `lc` itself should be
   conda-forge-installable (so "just pixi" is fully true) is open.
4. **Escape hatch.** "Never user-authored Containerfile" removes the
   exit for deps outside conda-forge/PyPI (TeX, proprietary tools). The
   derived image should support a documented extension point (e.g. a
   user stage `FROM` the derived env image) rather than none.
5. **Sandbox loss.** Today's container wrap mounts only `$PWD`,
   mechanically enforcing path discipline; direct `pixi run` execution
   loses that. The boundary rule becomes convention-plus-manifest-
   backstop on the direct path — acceptable, but should be stated, not
   discovered.
6. **Agent re-lock UX.** An agent adding a dependency mid-analysis must
   re-lock (`pixi add` does this atomically); `--frozen` failures need a
   pointed error message, and env-change-triggered re-materialization is
   the intended behavior, not a bug.
7. **ASTRA coordination.** `container:` is part of the upstream ASTRA
   schema; lockfile-first needs a spec conversation (new env
   declaration, `container:` becoming derived/optional). Also WRROC
   export currently maps the image to a SoftwareApplication — the
   lockfile (archived in the crate) becomes the checkable provenance
   artifact.
8. **Vendor concentration.** pixi is pre-1.0 from a VC-funded company.
   Mitigations: pin the pixi version `lc` bootstraps/requires; the lock
   format is open and rattler is conda-org-adopted; degradation path is
   the uv-variant of D (with its known holes).
9. **Unverified-at-NERSC.** The no-container MPI story (conda-forge
   `mpich external_*` under pixi on Perlmutter) inherits a documented
   conda mechanism but has no pixi-specific NERSC endorsement — worth a
   1-day spike before committing the Perlmutter direct path.

## Sources (primary)

- pixi lock/run/config/containers: pixi.prefix.dev docs (lock_file,
  multi_platform_configuration, cli/run, pixi_configuration,
  deployment/container, deployment/pixi_pack), github.com/prefix-dev/pixi-docker,
  prefix-dev/pixi#5476, tech.quantco.com/blog/pixi-production
- NERSC: docs.nersc.gov — using-python-perlmutter, nersc-python,
  python-shifter (benchmark numbers), containers/podman-hpc/overview,
  systems/perlmutter/timeline (SLES 15 SP6, Feb 2026)
- uv: docs.astral.sh/uv — resolution, projects/sync, python-versions,
  guides/integration/pytorch, reference/storage
- Snakemake: snakemake.readthedocs.io stable deployment docs;
  snakemake/snakemake PR #3339, issues #3915, #2880, #971;
  snakemake-software-deployment-plugin-container
- Prior art: docs.seqera.io/nextflow/wave, nf-co.re/blog/2024/
  seqera-containers-part-2, docs.metaflow.org/scaling/dependencies,
  iterative/dvc#6115, carpentries-incubator reproducible-ml-workflows,
  arXiv:2511.04827
- Durability: conda-forge.org maintainer docs (no-deletion),
  docs.pypi.org (yanking/72h), docker/roadmap#152 + Docker blog
  (retention policy reversals)
