# Findings: uv vs pixi adoption, and lockfile-first patterns in other workflow systems

- **Status:** findings report — supplementary evidence for revising
  [execution-environment.md](execution-environment.md) toward a
  uv-based substrate
- **Date:** 2026-08-15
- **Method:** 4 parallel web-research agents (uv adoption metrics, pixi
  adoption metrics, environment handling across 15+ workflow/ML-infra
  systems, uv's scientific-computing gaps and the wheel-variants
  standards track). Primary sources preferred; unverified claims
  flagged inline.

## TL;DR

The adoption gap is roughly **an order of magnitude on every measurable
axis**, and it is widening. uv is the presumptive default for new
Python projects (32% of Python repos created in 2025 ship a `uv.lock`);
pixi does not register in any usage survey. The "lockfile as source of
truth → container derived mechanically, tagged by a hash of the lock"
pattern the current design adopts via pixi **already exists in
production form built on uv**: Flyte/Union's ImageSpec, Metaflow
`--environment=uv`, Modal `Image.uv_sync`, ClearML agent — all shipped
in 2025. The scientific workflow managers (Snakemake, Nextflow) support
neither tool and remain conda-first.

The technical gap the substrate evaluation identified — uv.lock pins
only the Python layer — is unchanged in kind but has narrowed
materially on the CUDA axis and is precisely the gap a derived
container closes. A **uv + derived-container design** (the evaluation's
own named degradation path, candidate D with uv substituted) is
supported by stronger prior art than the pixi variant, at the cost of a
composite environment identity (lock hash + image identity) instead of
one lockfile that pins everything.

## 1. Adoption: the numbers

| Axis | uv (Astral) | pixi (prefix.dev) |
|---|---|---|
| GitHub stars | **88,746** | 7,567 |
| Contributors | 468 | 284 |
| Downloads | **~196M/month** (PyPI alone, pypistats.org 2026-08); most installs arrive via other channels and are not even counted | **~17.3M cumulative ever** (GitHub releases, all 142 releases summed); ~100k+/release-week currently |
| Survey presence | 11% in JetBrains/PSF 2024 survey (from 0% the year before, >30k respondents); most-admired technology in Stack Overflow 2025 (74.2%) | **absent from every survey found** — JetBrains, SO, conda-ecosystem; no survey reports a pixi share at all |
| New-project penetration | `uv.lock` in **32% of Python repos created in 2025**, 30% of 2026-Q1 repos (aleyan.com census of top-100k GitHub repos) | ~8.5k `pixi.lock` files on all of GitHub (code search); ~5k workflows use `setup-pixi` |
| Bot/ecosystem support | Dependabot **native**, Renovate native, PyCharm 2025.3 **default backend**, GCP Buildpacks default installer (Python ≥3.14), Databricks bundles require it, Airflow docs list "pip or uv" | Renovate native; **Dependabot: no** (open since 2023); no native PyCharm/VS Code support (conda-shim workaround) |
| Stack Overflow | (not counted — large) | **12 questions** total on the tag |
| Version / stability | 0.12.5 (2026-08-14); still no 1.0 | 0.76.2 (2026-08-10); still no 1.0, **no public 1.0 roadmap found**; lock format v7 (bumped May 2026) |
| Company | Astral — **acquired by OpenAI, March 2026**; commitment to keep uv/Ruff/ty open source; pyx registry wound down and its GPU-packaging infra open-sourced (June 2026) | prefix.dev — **5 people**, Berlin; undisclosed seed (2022, 468 Capital + Costanoa); Pro plan €42/mo; rattler library transferred to the conda org (Oct 2024) |

Where pixi *is* adopted, it is deep and domain-specific: conda-forge's
own maintainer tooling (`pixi run rerender`, `conda_install_tool:
pixi`), robotics (RoboStack "recommended for new installations", Open
Robotics standardized on it for Windows dev), QuantCo (40+ repos in
production), and the SciPy 2025 tutorial track (two pixi tutorials, one
now a SciPy Proceedings paper). An arXiv paper (2511.04827) claims
~5,300 adopting projects.

Corrections to the substrate evaluation's context:

- **NERSC does not mention pixi (or uv) anywhere in its Python docs** —
  the evaluation already flagged the pixi-at-NERSC story as
  operationally unverified; this confirms there is no site endorsement
  on either side. NERSC still teaches conda/mamba + containers at
  scale.
- Among centers that do name a tool: **CSCS explicitly documents uv**
  ("pip, uv and Conda can all be used", uv venv examples, squash-into-
  image guidance), ORNL's ExCL docs recommend uv, UF HiPerGator has a
  full uv endorsement page (with "use pixi for non-Python deps" as the
  caveat). No HPC center endorses pixi by name.
- The scientific-Python teaching consensus (pyOpenSci guide, Scientific
  Python dev guide) is **uv as the primary recommendation, pixi when
  conda-forge binaries are needed** — and pixi *embeds uv* as its PyPI
  resolver, so the community frames them as complements, not rivals.

### Sustainability reading

Both tools are pre-1.0. The risks differ in kind: pixi's is
concentration (5-person VC-funded company, no 1.0 roadmap, though the
lock format is open and rattler now lives in the conda org); uv's is
governance (OpenAI now owns Astral — resources are no longer a
question, but priorities could shift; the stated open-source commitment
and the open-sourcing of pyx's GPU packaging are the positive signals,
and uv's adoption is now so broad that a community fork would be viable
if needed). On the "will an agent or a new user already have and know
this tool" axis — the axis that matters for lightcone's agent-facing
UX — uv wins outright: it is in every harness's training data, ships in
agent sandboxes, and is how `lc` itself is installed today.

## 2. Prior art: who has shipped what (2025–2026)

The substrate evaluation surveyed prior art through a conda-centric
lens (Nextflow Wave, nf-core, Snakemake pin-files). A second tier of
systems has since converged on the *same lockfile-first shape built on
uv*:

| System | uv integration (shipped) | Pattern |
|---|---|---|
| **Flyte / Union.ai** | `ImageSpec(requirements="uv.lock")`; Flyte v2 `Image.with_uv_project(pyproject_file=…, uvlock=…)` | **Container derived from the lock; image tag = deterministic hash of spec + lock content + source root; registry checked before rebuild.** The closest existing analogue to lightcone's content-addressed `lc-<name>-<hash>` scheme — with the identity flaw already fixed. |
| **Metaflow** (2.15.8, May 2025) | `--environment=uv` packages the whole uv project (pyproject + uv.lock) and re-materializes it on Kubernetes/Batch workers | Lock executed frozen on remote workers. Caveat: forgoes Metaflow's content-addressed S3 env snapshots (their availability guarantee), per-flow not per-step. |
| **Modal** (July 2025) | `Image.uv_sync()` — runs `uv sync --frozen` from pyproject + uv.lock in a server-side derived image | No user Dockerfile; images content-addressed from the spec. |
| **ClearML agent** (v1.9/3.0) | `package_manager.type: uv`; if the repo has a uv.lock, the agent runs `uv sync --locked`, overriding the pip-freeze snapshot | Lock as authoritative env on remote execution. |
| **Dagster** | `uvx create-dagster` canonical; "uv is the future of Python project management for Dagster projects" (maintainer statement) | Scaffolding-level only; isolation unit is the code-location venv. |
| **Coiled** | package sync scans the local (uv-managed) env and replicates deterministically on cluster VMs | Transparent replication; lockfile recommended client-side. |

Meanwhile the scientific WMS tier has shipped **nothing** for either
tool: Snakemake's uv issue (#3251) is open-stale with no maintainer
response; its pixi issue (#3915) is blocked on the software-deployment
plugin framework (PR #3339), still an open draft at ~17 months;
Nextflow has no uv issue at all and its direct-pip provider requests
(#4664, #4671) remain unshipped. This doesn't hurt lightcone — the
design already keeps Snakemake env-oblivious — but it removes any
"align with Snakemake's pixi direction" argument: that direction is
indefinitely stalled either way.

Supporting infrastructure for uv.lock-as-identity is first-class and
officially documented:

- `hashFiles('uv.lock')` as cache key is Astral's own documented CI
  pattern; `setup-uv` computes cache validity from the lock hash.
- The multi-stage Docker pattern (SHA-pinned `ghcr.io/astral-sh/uv`
  binary, copy pyproject + uv.lock, `uv sync --frozen
  --no-install-project` deps layer, copy source, final stage copies
  only `.venv`, `UV_PYTHON_DOWNLOADS=never`) is the de-facto standard
  Python container build, replicated across Astral docs, Hynek
  Schlawack, Microsoft ISE, Depot.
- `uv2nix` reproduces a uv.lock bit-for-bit as Nix derivations.
- `uv export --format requirements.txt` (with hashes) bridges to
  pip-only contexts.

No dedicated "Dockerfile generator from uv.lock" tool exists — lightcone
generating one is exactly the thin residual code every surveyed system
also keeps.

## 3. The technical gap, re-measured

The evaluation's core objection to uv-canonical stands in kind: uv.lock
pins wheels and the interpreter, not BLAS/MPI/HDF5/compilers. What has
changed:

- **CUDA (much better).** NVIDIA now publishes an official
  `cuda-toolkit` metapackage on PyPI (13.3.1, June 2026) with extras
  including **nvcc itself**, cudart, cublas, cusolver, cufft — the
  toolkit is pip/uv-installable and lockable. uv's `--torch-backend`
  (`auto`/`cu118`…`cu130`/rocm/xpu) plus `[tool.uv.index]` with
  platform markers is the documented PyTorch path. The host *driver*
  remains unpinned (true under pixi and containers too). Known footgun:
  `--torch-backend=auto` on a GPU-less build host silently selects CPU
  wheels — pin explicitly in any container build.
- **Wheel variants (the 2027 fix, not the 2026 one).** PEP 817 → PEP
  825 (package format, split Feb 2026) are active Drafts with authors
  from NVIDIA, Meta/PyTorch, Astral, Quansight, Anaconda; a major PEP
  825 revision landed 2026-08-13 and the delegate (Paul Moore) is
  reviewing favorably. Experimental variant-enabled uv and PyTorch 2.8
  variant wheels exist. Mainline uv has **not** shipped variant
  support; realistic ecosystem readiness is 2027. Variant-based
  MPI pinning has no provider at all yet.
- **MPI (unchanged).** mpi4py against Cray MPICH is still an sdist
  build with `MPI4PY_BUILD_MPICC`/`--no-binary` against the host `cc` —
  uv passes these through (`[tool.uv] no-binary-package = ["mpi4py"]`),
  but the toolchain step is unlocked, and no HPC center documents the
  uv variant of the incantation yet. On the container path this is
  moot: NERSC's podman-hpc + `cray-mpich-abi` is the site-blessed
  mechanism regardless of what installed the Python layer.
- **BLAS variant selection (unchanged).** Impossible on PyPI — numpy/
  scipy wheels vendor one OpenBLAS. If a project needs MKL vs OpenBLAS
  control outside a container, only conda-forge provides it.

## 4. Implications for the design decision

What the new evidence changes in the evaluation's weighing:

1. **The prior-art claim inverts.** The evaluation found the
   lockfile-first pattern "converged" in conda-flavored systems. The
   2025 wave shows the *same* pattern shipped on uv by the ML-infra
   tier — and Flyte's ImageSpec is a closer match to lightcone's
   content-addressed image scheme than anything in the conda camp.
   uv-lockfile-first + derived container is established practice, not a
   novel construction.
2. **Agent/user familiarity was underweighted.** The eval scored
   candidates on install bar but not on "does the agent already know
   this tool". Every coding agent has deep uv training data and most
   sandboxes ship it; pixi is a long-tail tool an agent must be taught.
   For a product whose primary UX is agent-driven, this is a real
   scoring axis, and it is lopsided.
3. **The vendor-risk comparison shifted.** Astral's acquisition
   resolves its sustainability question (while raising a governance
   one); prefix.dev remains a 5-person pre-1.0 company with no public
   1.0 commitment. The eval's mitigation for pixi risk was "degrade to
   the uv variant of D" — the new evidence suggests starting there.
4. **The identity cost is real and must be stated honestly.** One
   pixi.lock pins interpreter + BLAS + MPI + system libs in a single
   per-platform artifact; `env_version` = one lock projection hash.
   Under uv + container, the environment identity is **composite**:
   `env_version = H(uv.lock projection)` for the Python layer, plus —
   whenever the container transport is used or system deps are declared
   — the derived image identity (digest-pinned base + generated
   Containerfile + any system-package declaration). Sites that need
   the system layer pinned (GKE, Perlmutter at scale) are exactly the
   sites where the container transport runs, so the pinning lands where
   it is needed; the residue is direct-transport execution relying on
   host system libs (host BLAS is *not* in play — PyPI wheels vendor
   their own — but host glibc, MPI, and any non-Python tools are).
   The manifest's existing `transport` + image-digest fields carry
   this honestly.
5. **What is genuinely lost vs pixi:** single-file whole-stack pinning;
   conda-forge's `mpich=*=external_*` no-container MPI story at small
   scale (under uv, vendor-MPI mpi4py on the direct path is an unlocked
   host build — record as attestation, or push MPI workloads to the
   container transport); BLAS-variant control outside containers; and
   non-Python tools (TeX etc.) always require the container escape
   hatch rather than a conda dependency. None of these affect the
   laptop/pure-Python majority path; all of them have a container
   answer.

### Sketch: candidate D-uv (uv + derived container)

- `pyproject.toml` + `uv.lock` (+ `.python-version` pinning an exact
  patch) are the committed source of truth. `tool.uv.required-environments`
  forces lock-time failure for platforms lacking wheels.
- `lc run CMD` wraps `uv run --frozen`. Stale lock → pointed error,
  never silent re-resolve. Same boundary rule, litmus test reworded to
  "fix `pyproject.toml`".
- `env_version` = hash of a normalized uv.lock projection (sorted
  artifact URLs + sha256s). uv.lock is universal (one file, all
  platforms), so unlike pixi the hash does not vary per platform —
  still record `platform` in the manifest since resolved binaries
  differ.
- System-level needs are declared, not authored: a small optional
  declaration (e.g. apt packages / CUDA toolkit extras) feeds the
  generated multi-stage Containerfile (digest-pinned base, Astral's
  documented uv pattern). Declaring any system dep — or a site
  requiring image transport — routes materialization through the
  container, whose identity joins the manifest. `Containerfile.extra`
  escape hatch as in the current draft.
- Transports unchanged from the draft: direct (laptop, small HPC) /
  podman-hpc (Perlmutter at scale) / kubernetes (GKE). Pre-
  materialization warm-install rule carries over (`uv sync --frozen`
  driver-side; uv's cache is designed for concurrency but the
  warm-before-fan-out rule stays cheap insurance on Lustre).
- Bootstrap story collapses: uv is the one tool, it installs `lc`
  itself (`uv tool install lightcone-cli`), no binary bootstrap
  machinery, no pixi-version pin in config.

### Open items this evidence does not settle

- Whether any current lightcone project actually needs BLAS-variant
  control or no-container vendor MPI at small scale — the two cases
  where pixi is strictly stronger. If yes, the pixi design stands on
  its merits; if no, D-uv covers the real workload with the more
  adopted tool. (The NERSC spike in the design's open questions would
  answer the MPI half — now for the container path instead.)
- GPU lock ergonomics from mac laptops (torch-backend pinning replaces
  `CONDA_OVERRIDE_CUDA`; needs a concrete `lc init` default).
- Re-check wheel variants (PEP 825) around early 2027 — if accepted and
  shipped in uv, the CUDA/MPI pinning story changes again in uv's
  favor.

## Sources (primary, fetched 2026-08-15)

- uv metrics: pypistats.org/packages/uv; api.github.com/repos/astral-sh/uv;
  lp.jetbrains.com/python-developers-survey-2024; survey.stackoverflow.co/2025;
  aleyan.com/blog/2026-why-arent-we-uv-yet (repo census);
  openai.com/index/openai-to-acquire-astral + CNBC/Bloomberg 2026-03-19;
  pydevtools.com/blog/astral-winds-down-pyx-open-sources-gpu-packaging
- pixi metrics: api.github.com/repos/prefix-dev/pixi (7,567 stars, 142
  releases, 17.3M asset downloads summed); api.anaconda.org/package/conda-forge/pixi;
  formulae.brew.sh/api/formula/pixi.json; GitHub code search
  (setup-pixi 5,048 workflows; 8,512 pixi.lock files); StackExchange
  API (12 questions); tech.quantco.com/blog/pixi-production;
  robostack.github.io; arXiv 2511.04827; conda.org/blog/2024-10-01-rattler-to-conda
- Workflow systems: docs.metaflow.org/scaling/dependencies/uv;
  docs.flyte.org ImageSpec + union.ai/docs/v2 (with_uv_project, tag
  hashing); modal.com/docs/guide/images (uv_sync);
  clear.ml/docs clearml_agent_execution_env;
  github.com/snakemake/snakemake #3251 #3915 PR#3339;
  github.com/nextflow-io/nextflow #4664 #4671 #5219;
  docs.astral.sh/uv/guides/integration/docker + github + dependabot + renovate;
  github.com/pyproject-nix/uv2nix
- Scientific gaps: pypi.org/project/cuda-toolkit (13.3.1, nvcc extra);
  docs.astral.sh/uv/guides/integration/pytorch (torch-backend);
  peps.python.org/pep-0817 + pep-0825 (Drafts; 825 revised 2026-08-13);
  astral.sh/blog/wheel-variants; pytorch.org/blog/pytorch-wheel-variants;
  mpi4py.readthedocs.io/en/stable/install; docs.nersc.gov (no uv/pixi);
  docs.cscs.ch/build-install/python (uv documented);
  docs.rc.ufl.edu/software/uv; docs.excl.ornl.gov quick-start;
  pyopensci.org python-package-guide; learn.scientific-python.org
  dev-environment; pydevtools.com uv-vs-pixi-vs-conda
