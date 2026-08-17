# Spec: the locked environment is the execution environment

- **Status:** specification, v6.1 (uv-only, sandbox-enforced,
  container hatch — full-stack). Normative. v6 replaced v5's
  dual-substrate layer with a single-substrate ladder: uv alone by
  default, hermeticity via OS sandboxing, and an on-demand container
  hatch (decision record:
  [substrate-default-tradeoffs.md](substrate-default-tradeoffs.md)).
  **v6.1 resolves the 2026-08 multi-agent review**
  ([execution-environment-v6-review.md](execution-environment-v6-review.md)):
  containerized mode now runs the **entire execution stack inside the
  image** — engine, workers, recipes, probes — closing the review's
  host-sync deadlock (a bare host can never be asked to build
  lock-level system dependencies); the Perlmutter direct wrap is
  brought to the honest mount policy; image execution is
  digest-pinned; the sandbox policy gains the ELF loader and a
  per-recipe HOME/XDG contract; the denial message, `lc status`
  visibility, command spelling, and `lc run` rename guard are
  specified as the primary UI they are; and the review's
  simplifications land (per-output `network:` deferred, two flags
  deleted). Scale (>~10 nodes) remains out of scope. Evidence:
  [execution-environment-rationale.md](execution-environment-rationale.md),
  [uv-vs-pixi-adoption.md](uv-vs-pixi-adoption.md),
  [hermeticity-enforcement.md](hermeticity-enforcement.md),
  [environment-substrate-evaluation.md](environment-substrate-evaluation.md).
- **Scope:** CLI surface, `lc init` scaffold, engine environment
  handling, the container hatch (`lc build`, generated Containerfile,
  image identity), the dask fabric's environment contract, recipe
  sandboxing, eval prompt. Not in scope: executor scheduling
  semantics; hub charts (they consume the §5/§7 contracts); native
  Windows (WSL2 is supported via the Linux path); >~10-node scale.
- **Goals:** (G1) start with uv alone; a project adds exactly one
  more tool (podman), and only when its dependencies leave what uv
  can lock; (G2) uniform execution laptop → Perlmutter (2–4 nodes) →
  hub/GKE; (G3) honest pinned environment identity — *pinned
  identity, never bit-identical outputs*; (G4) probing is
  byte-for-byte the recipe environment, sandbox and container
  included — one documented exception: direct-mode hub notebook
  probes share the lock but not the OS layer (§5); (G5) code edits
  never trigger an image build; environment edits rebuild only
  containerized projects' images — exactly when a rebuild is
  meaningful; (G6) recipes mechanically cannot use tools or files
  outside the declared environment wherever an enforcement mechanism
  exists — and every output records exactly what enforcement it ran
  under.

## The design in one paragraph

A project is `pyproject.toml` + `uv.lock` + `.python-version` — uv is
the only substrate. In **direct mode** (the default) the environment
lives in the project tree on every venue (`.venv`); no image is ever
built; every recipe runs inside a **sandbox restricted to the
declared set** — Landlock (Linux), Seatbelt (macOS), a podman-hpc
wrap (Perlmutter), the pod's OS layer (hub). When a project needs a
dependency uv cannot source — R, Julia, TeX, MPI, compilers, system
libraries a locked package links against — it declares a **system
layer** (the `[tool.lightcone.image]` table, optionally
`Containerfile.extra`), which flips the project into **containerized
mode**: lc renders the locked environment *plus* the system layer
into a content-addressed image, and from then on **the image is the
execution world** — driver, workers, recipes, and probes all run from
the image's baked environment; no host environment exists for the
project at all. Because `uv sync` runs inside the image build, after
the apt layer, lock-level system dependencies (rpy2 needing R, sdist
builds needing headers) resolve where the system layer actually is.
The container is a *cache of the lock plus the declared system
layer*, never a definition; project code never enters an image; code
reaches every venue through the filesystem the venue already shares.
One execution discipline in both modes: converge once (sync the
`.venv` / build the image), then execute without writing to the
environment. The manifest chain is unchanged in shape: `env_version`
(covering the system layer) sits inside `code_version`; a double
mid-run relock gate ties every recipe to the lock live at run start;
image execution is pinned to the digest the driver resolved; a
`hermeticity` field records mechanism, file scope, and network
posture per output.

## 1. The environment ladder

Two modes, one substrate. Mode is **derived, not configured**:
declaring a system layer *is* the escalation.

- **direct** (default): no per-project image, and no container on
  laptops. (Two venue-provided static images exist in direct mode and
  are not project artifacts: the hub's runtime image, and the same
  image used by Perlmutter's recipe wrap — §5, §7.)
- **containerized**: triggered solely by the presence of
  the `[tool.lightcone.image]` table (or `Containerfile.extra`).
  Per-project, never per-output. **Full-stack**: every process that
  touches the run — driver engine, dask workers, child snakemake,
  recipes, `lc run` probes — executes inside the project image from
  its baked `/opt/venv`. There is no host `.venv`; the host keeps
  only the thin launcher (§4). One environment, one place; engine
  version coherence is preserved because everything runs from the
  image = the lock.

**Rules that keep it thin (normative):**

- **lc never wraps dependency management.** `uv add` is uv's
  business; declaring the image is one TOML table (§2); lc wraps
  execution and identity only.
- **The nudge is the sandbox denial.** Direct mode's denial UX (§7)
  is where users meet the hatch. lc never escalates silently.
- **Escalation is reversible, and its cost is announced.** Deleting
  the declaration returns the project to direct mode. Declaring it
  moves `env_version` — **every materialized output goes stale, not
  just the rule that needed the tool** — and lc says so at
  escalation time, exactly as it does for any environment edit (§3).
  De-escalation leaves the old image inert; reclaim with
  `podman image prune` (an lc-side image GC verb is a §12 re-add
  candidate).
- **The root Containerfile is never user-authored.** The image is
  generated (§3); `Containerfile.extra` is the bounded escape — a
  stage `FROM` the derived image, content-hashed into identity, and
  **fully rendered by the generator** (a declaration the mode
  derivation honors but the generator ignores is banned half-state).
  The **BYO digest-pinned per-output container** remains the
  schema's escape for truly arbitrary cases (§8).
- **Packaged projects are refused in containerized mode.** The image
  is built `--no-install-project` (code never enters an image), so a
  packaged project's `import my_analysis` would fail inside its own
  container — the design's founding failure mode. `lc` refuses at
  mode-detection time: "containerized mode requires a virtual
  project (no `[build-system]`) — restructure, or use a declared
  per-output container (BYO)."

## 2. Environment definition

**The uv project (both modes).** `pyproject.toml` +
`.python-version` (exact interpreter patch) + `uv lock`;
`[tool.uv] required-version` and `required-environments` scaffolded;
`lightcone-cli` in dependencies (lock pins exact — the engine is
inside the experiment's lock). One environment, no group/feature
splits (identity hashes the install-selection settings, §3; extra
groups draw an advisory: "outside lc's guarantees"); virtual by
default — packaged projects are supported in direct mode via
editable install (the project's **own package is exempt** from the
path-dep refusal; everything else path-like is refused) and refused
in containerized mode (§1); GPU via pinned indexes
(`[tool.uv.index]` + `[tool.uv.sources]`), host driver attested;
bootstrap = uv alone. `lc init` also scaffolds an **agent notes
stanza** (AGENTS.md, or appended to an existing one) carrying the
boundary rule — *"a `ModuleNotFoundError` under `lc run` means fix
`pyproject.toml` with `uv add`, never install into another env"*,
the containerized-mode habit (*"`uv add` runs on the host, bare —
add `--no-sync` in containerized projects; never `lc run uv add`"*),
and the four-verb map (`lc run` probes, `lc materialize` executes,
`lc status` reports, `lc verify` audits), so the design's primary
interface works without reading this spec.

**The image declaration (containerized mode).** One TOML table is
the whole user-facing surface of the container hatch; its presence
is the escalation (§1), and every key is hashed into `env_version`
and the image tag:

```toml
[tool.lightcone.image]
# base — optional. Digest-pinned OCI ref replacing the generator's
# default base (an engine constant). The canonical use is a vendor
# userland, e.g. NVIDIA CUDA:
base = "nvcr.io/nvidia/cuda:12.4.1-runtime-ubuntu22.04@sha256:9f2c81d4…"

# system-packages — apt package names, installed before uv sync.
system-packages = ["texlive-latex-base", "r-base-core", "libhdf5-dev"]
```

- **`system-packages`** (list of apt names, sorted into the
  identity). Both classes of system dependency are supported,
  because the environment is built *inside* the image after the apt
  layer: tools recipes invoke (`latex`, `Rscript`, `julia`) **and**
  libraries locked packages need at build or import time
  (`libhdf5-dev` for an sdist h5py, R for rpy2). The docs still
  steer users to check PyPI first — h5py wheels bundle libhdf5,
  NVIDIA ships `nvcc`, BLAS rides inside numpy — the hatch is for
  what genuinely has no wheel.
- **`base`** (optional digest-pinned OCI ref). A tag-only ref is
  **refused** — "pin the digest" — because the image must remain a
  pure function of the repo plus the engine (the unpinned-base
  identity hole is what killed the v1 design). Registry
  authentication is the venue's (`podman login`, Cloud Build's
  service account); lc never stores credentials. GPU guidance:
  the default base + PyPI CUDA wheels is the first choice; a
  vendor `base` is the step up when a vendor userland is genuinely
  required.

**The base contract** (the Modal lesson: base flexibility only with
a published contract — each violation is a build-time refusal with a
pointed error, never a downstream mystery):

- **linux/amd64 required**; linux/arm64 is additionally used for
  Apple-silicon local runs when the ref provides it, else amd64
  under emulation with a one-line performance warning;
- **glibc-based** — manylinux wheels and uv-managed interpreters
  require it; musl/Alpine bases are refused;
- **apt-capable (Debian/Ubuntu family) iff `system-packages` is
  nonempty** — otherwise refused, naming the two escapes (a
  Debian-family base, or `Containerfile.extra`);
- **a POSIX shell at `/bin/sh`** (build stages run through it);
- **nothing else** — no Python, no pip, no uv (unlike Modal's
  python-on-`$PATH` requirement, the base never supplies the
  interpreter); `ENTRYPOINT`/`CMD`/`USER` are inert — lc invokes
  with the entrypoint cleared, an explicit argv, and the invoking
  uid (rootless).

**Dependency edits are plain `uv add`, in both modes.** Escalating
never changes the dependency verbs: `uv add`/`uv remove`/`uv lock`
run **on the host, bare** — they are manifest-and-lock surgery, and
in containerized mode the environment is *derived* (the image), so
the next `lc materialize` picks the change up through the ordinary
rebuild path. (`lc run uv add …` is never the answer, and cannot
work: probes have no in-tree write scope, §4.) The three cases,
empirically grounded (uv 0.12.3, §13):

- **Wheels or static-metadata sdists (the overwhelming majority)**:
  plain `uv add` resolves and locks on the bare host with no system
  layer needed. Its auto-sync side effect materializes a host
  `.venv` that is **inert** in containerized mode — lc ignores it,
  `lc status` notes it ("stray host .venv — inert in containerized
  mode"), and `uv add --no-sync` avoids creating it.
- **A legacy sdist whose *metadata* build needs the system layer**
  (no wheels, no static metadata — e.g. mysqlclient without
  `mysql_config` on the host): `uv add` fails at resolution and
  **rolls back cleanly** — `pyproject.toml` and `uv.lock` are left
  untouched (verified). The documented remedy is uv's purpose-built
  mechanism, `[tool.uv.dependency-metadata]`: declare the package's
  metadata statically so resolution never builds it; the *actual*
  build then happens inside the image, where the system layer
  exists. (The setting flows into `uv.lock`, so identity is
  covered by the lock bytes.)
- **A package needing the system layer only at build/install time**
  (static metadata, e.g. rpy2): resolution and locking need no
  build, so the lock is the deliverable and the host auto-sync's
  fate is irrelevant — `uv add --no-sync` sidesteps the doomed host
  install entirely, and the agent notes name it as the
  containerized-mode habit.

**Above the base, uv takes over — identically for every base.** The
layering is fixed by the generator, never user-ordered: (1) the apt
layer (`system-packages`); (2) the pinned uv binary
(engine-constant digest); (3) `uv python install` of the exact
`.python-version` interpreter into `/opt/python`; (4)
`uv sync --locked --exact --no-install-project --compile-bytecode`
into `/opt/venv` from `pyproject.toml` + `uv.lock` only; (5) the
`Containerfile.extra` stage, if declared — a stage `FROM` the
derived image, its content hashed into both `env_version` and the
tag, rendered by the generator; (6) the final ENV contract (§11
step 6). The base contributes the OS layer and nothing else — it
can never supply the interpreter or a Python package, which is what
keeps environment identity uniform across bases.

## 3. Identity

**`env_version` = sha256(uv.lock bytes ‖ `.python-version` bytes ‖
canonical install-settings JSON ‖ canonical `[tool.lightcone.image]`
JSON (base digest-ref, sorted `system-packages`) ‖
`Containerfile.extra` content hash-or-null)** — computed by the
locked engine; sits inside `code_version`; also the mid-run gate's
baseline. Direct-mode projects hash empty system fields, so the
formula is one formula, not two. The install-settings set is v4's
closed, audited list (`default-groups`, `no-binary[-package]`,
`no-build[-package]`, `config-settings`,
`no-build-isolation[-package]`). When lc detects that the current
`env_version` differs from existing manifests, it prints the blast
radius — "environment changed: N materialized outputs are now
stale" — including at escalation time (§1).

**Image identity (containerized only).**
**`tag = lc-env-<sha256(rendered Containerfile text ‖ env-input
document)[:16]>`**. The rendered Containerfile embeds the
digest-pinned base (the generator's default — an engine constant —
or the project's declared `base`, §2), the digest-pinned uv binary,
the interpreter version, the apt list, and the `Containerfile.extra`
stage; the default-base and uv digests are generator constants
shipped with the locked engine, so new constants reach a project
only through an engine relock — tag and `env_version` move together;
a declared `base` moves them through the repo, like any environment
edit. Builds are incremental (tag hit ⇒
no-op). **The build records the produced digest** (project-local
record + OCI label carrying `env_version`), and **execution is
digest-pinned**: the driver resolves tag → digest once at run start,
embeds the digest in every job command, and `run_rule` asserts it —
the image-side twin of the mid-run relock gate; a tag that resolves
to a different digest than the build record is a loud error, never a
silent substitution. **Code edits change no input to the tag** — the
image contains no project code, ever (G5).

**Honest residue, stated:** the apt layer is name-pinned only — two
builds of one tag at different times can hold different system
package *versions*. The build records a `dpkg -l` snapshot and the
manifest stores its **sha256 (content hash), with the snapshot text
archived beside the build record** — so the attestation outlives
image garbage-collection. (Pinning via snapshot.debian.org is a §12
hardening candidate.) This is attestation-grade system-layer
identity, accepted with eyes open in the decision record.

**Lock scan**: refuse path/directory/editable dependencies except the
project's own package; report PyPI `sdist_built`; advisory on
non-default dependency groups. All v4 honest boundaries carry over
(raw-bytes over-invalidation; set-level-not-byte-level checks).

**Manifest schema (normative field list — the single enumeration the
`SCHEMA_VERSION` bump implements and golden tests pin):** the v5-era
core (`schema_version`, `code_version`, `data_version`,
`env_version`, `recipe`, `decisions`, `input_versions`, `git_sha`,
`git_dirty`, `lc_version`, `host`) plus: `uv_version`; `platform`
(os-release, kernel, glibc, arch); `python_build`; `worker_runtime`
(`host` | `container`); `image` (`{tag, digest}` when a container
ran — project image or a static runtime image); `dpkg_snapshot_sha256`
(containerized); `sdist_built` (+ `cc --version` line when
nonempty); `env_snapshot` (locale, TZ, threading knobs);
`gpu_driver`; `hermeticity` (§7). **Scoping rule:** when
`worker_runtime: container`, every environment-describing field
(`platform`, `python_build`, `env_snapshot`, `gpu_driver`) is
captured *inside* the boundary — which is automatic in v6.1, since
`run_rule` itself executes there. `lc verify` surfaces dirty-tree and
unsandboxed outputs distinctly; pre-migration manifests report
`pre_migration`.

## 4. The `lc` entrypoint

Canonical, documented invocation on every venue: **bare
`lc <verb>`**, provided by the `uv tool install lightcone-cli` shim.
(`uv run lc <verb>` is an accepted equivalent where the shim is not
installed — it cannot be the documented form, since it does not work
before a lock exists.) The shim is a launcher: (1) **discover** by
`astra.yaml` walk-up — uv's native walk-up discovery is never
trusted: every uv invocation carries `--project <root>`; (2)
**detect the mode** (§1); (3) **scrub** the ambient `UV_*` namespace
(the closed v4 list); the values re-injected afterwards come from
**`site_registry.py`** (e.g. Perlmutter's `UV_CACHE_DIR` +
`UV_LINK_MODE`), with a matching ambient value honored as an
override — explicit flags beat ambient variables (§13), so the scrub
is defense-in-depth on lc's always-pass-explicit-flags posture; (4)
**converge**: direct mode — `uv sync --locked --exact
--compile-bytecode` where writable (captured output, prune notice);
containerized mode — resolve tag → digest against the build record;
**`lc materialize` builds a missing image** (printing "building
`lc-env-<hash>` — first run after an environment change; ~minutes"),
**`lc run` never builds** — it errors with the exact `lc build`
command; (5) **delegate by direct exec**: direct mode — exec
`<env>/bin/lc` (`LC_DELEGATED=1`); containerized mode — `podman run`
into the digest-pinned image and exec `/opt/venv/bin/lc` there. The
delegation interface — argv passthrough + `LC_DELEGATED=1` — is
declared **minimal and frozen**: a tool-env launcher of any version
must be able to delegate to a project-locked engine of any age.
`status`/`verify` and pre-lock verbs run in the tool env. `lc build`
on a direct-mode project is an explanatory no-op ("direct mode — no
image to build; declare `[tool.lightcone.image]` to
containerize").

**`lc run CMD`** stays the thin probe verb and is mode-faithful
(G4): direct mode ≡ `uv run --locked --exact CMD` from the project
root, inside the §7 sandbox (`--no-sandbox` opts out); containerized
mode runs the same command inside the digest-pinned image under the
same policy. Bare `lc run` opens a shell in the recipe environment
and announces it ("opening a shell inside the recipe environment
(sandboxed)"). A probe has no output, so its read allowlist is the
union of all declared inputs, and its **write scope is the tmp
scope only** (§7) — never in-tree. **Rename guard** (v6 reassigns
`lc run` from pipeline execution to probing): a first argument that
matches a declared output id errors *before any exec* with "outputs
are materialized, not run — did you mean: `lc materialize
best_fit`?".

**`lc status`** stays manifest-driven — the invariant is now
**offline and local-only** (it additionally reads `pyproject.toml`
and the local image store, never the network) — and gains three
header lines answering the questions nothing else surfaces:

```
mode:    containerized (3 system packages)        # or: direct
image:   lc-env-9f2c81d44a1b03e7 — built (digest sha256:…)   # or: needs build
sandbox: landlock (fs: declared, network: unenforced)        # this host
```

The denial message and the podman-missing error both point here.

## 5. Venues and placement

| Venue | direct | containerized |
|---|---|---|
| Laptop (Linux/WSL2/macOS) | `.venv` in-tree; nothing to configure | + rootless **podman** (docker accepted); the full stack runs in the image. macOS runs it in `podman machine` — a one-time Linux VM setup, announced in the first denial (§7); recipes execute linux builds resolved from the same universal `uv.lock`; no GPU inside the VM, stated. **Mount-source preflight**: every mount source (project root, declared inputs, scratch) must lie under the machine's shared directories (`podman machine inspect`); a source outside them is a *refusal* naming `podman machine set --volume` — never a silently empty mount |
| Perlmutter (2–4 nodes) | Project on **CFS, not `$HOME`**; cache placement supplied by the site registry (§4); CFS writable from compute ⇒ sync works mid-salloc; `scratch.py` redirects + Lustre run lock kept; `_abort_on_perlmutter_login` kept, materialize-scoped. The recipe wrap uses the **static runtime image**: materialize preflight checks it and, from a compute node, errors with the exact login-node command (`lc … --pull-runtime-image`, wrapping `podman-hpc pull` + `migrate`) | **spike-gated** (§11): full stack via `srun podman-hpc run --net=host … dask worker` from the project image (built/migrated once on a login node; preflight prints the exact command). `--net=host` is required for dask reachability and is what the network posture honestly records. GPU rules hang on the `--gpu`/`--net` CDI spike; until it passes, containerized GPU-on-Perlmutter refuses with the BYO/direct alternatives named |
| Hub / GKE (few workers) | Static deployment-managed runtime image (by digest); project env + interpreter + code ride same-path NFS home; lc passes the env location as **`LC_PROJECT_ENV`** through the standard `environment` cluster option; the shim execs `<env>/bin/…` and **fails loudly** when unset/absent (never a PATH fallback — §13); deployment image recorded as attestation. G4's documented exception lives here: notebook-pod probes share the lock but not the workers' OS layer; `worker_runtime` keeps it inspectable | worker pods run the **project image** (full stack: the dask worker, the child snakemake, and the recipe all in-image); code via the NFS workdir mount; Cloud Build renders from a three-file context (rendered Containerfile, `pyproject.toml`, `uv.lock`). **Deployment contract**: the notebook image for a containerized project is derived `FROM` the project image (one generated layer adding Jupyter), so the driver runs the same OS layer and `/opt/venv` — restoring G4 exactness on this venue; where the deployment cannot provide it, containerized-mode materialize refuses at preflight, naming the contract item and the BYO/direct alternatives |
| External scheduler | Caller-owned; no-write posture + fingerprint gate as v4 | same; image availability and digest pinning are the caller's contract, verified by the connect probe |

## 6. `lc materialize` and the fabric

Flow unchanged (Snakefile → run-scoped cluster → rules as dask tasks
→ manifests). The integrity core:

- **Converge once, then never write to the environment.** Direct
  mode: driver preflight `uv sync --locked --exact` before cluster
  start; workers run `uv run --no-sync` with the offline overlay
  (`UV_OFFLINE=1`, `UV_PYTHON_DOWNLOADS=never`) and scrub-list unsets
  in every job command. Containerized mode: the *build* is the
  convergence; at run time nothing syncs anywhere — the driver
  resolves tag → digest (building via the §4 rule if absent) and
  every process runs from the immutable image env.
- **The worker sequence** — inside `run_rule` (which in containerized
  mode itself executes inside the image):
  1. **pre-gate** — re-hash the `env_version` inputs (mounted project
     tree) against the run-start value in the job command;
  2. **env check** — direct: assert the env prefix exists and
     `uv sync --locked --exact --check` (true no-write env-vs-lock
     verification); containerized: **assert the running image's
     digest equals the driver-resolved digest** in the job command;
  3. **exec the recipe through the boundary** — direct: the sandbox
     shim (§7); containerized: the same shim, applying in-container
     Landlock (§7);
  4. **post-recipe re-gate** before `write_manifest`.
- **Mid-run relock gate** (double) and **driver-side git capture** —
  verbatim from v4.
- **Connect-time probe** in `_connect_client` (all branches,
  self-contained closure): workdir mount + engine version + (
  containerized) image digest; `--allow-unverified-cluster` on the
  external branch.

## 7. Hermeticity enforcement

**The guarantee (G6):** a recipe cannot use executables or files
outside the declared set wherever a mechanism exists, and every
manifest records exactly what ran. Research:
[hermeticity-enforcement.md](hermeticity-enforcement.md).

**The default filesystem policy** (one policy, both modes):

- **write**: own `results/<u>/<o>/` output dir + scratch + `/tmp` +
  `/dev/shm` (+ `/dev/null`) — protecting sibling outputs,
  manifests, and `astra.yaml` from a misbehaving recipe. The
  per-output escalation for recipes that legitimately write
  intermediates in the tree is **ASTRA-declared only** — the
  `sandbox: writable-project: true` key on the output (no CLI flag:
  a behavior-changing choice must live in the repo, reproducible
  from files a year later); such outputs record
  `hermeticity.fs: project-rw`.
- **read**: the project tree; declared ASTRA inputs outside it; the
  OS baseline — `/usr`, `/lib`, `/etc`, `/proc`, `/sys`,
  `/dev/urandom`, locale/SSL data.
- **HOME and XDG, normative in both modes**: the boundary sets
  `HOME`, `XDG_CONFIG_HOME`, `XDG_CACHE_HOME`, `XDG_DATA_HOME`, and
  `MPLCONFIGDIR` to a **fresh per-recipe directory under the
  writable tmp scope** (the Bazel/nix move). The real `$HOME` is
  neither readable nor writable: matplotlib/astropy/R work on first
  import, and the dotfile-steering channel stays closed — an
  implementer must never "fix" a HOME failure by mounting `$HOME`
  RO.
- **execute — two tiers (direct mode)**: the `.venv` binaries
  **plus an enumerated, versioned utility set** (`sh`, `bash`,
  `env`, coreutils, `grep`/`sed`/`awk`, `tar`/`gzip`) **plus the
  realpath'd ELF loader(s)** (`/lib64/ld-linux-*`, musl's loader
  where present) — Landlock checks EXECUTE on the interpreter's
  open, so without the loader every dynamically linked binary,
  python and bash included, fails EACCES; shared libraries need only
  the read baseline. The allowlist is a maintained policy surface,
  recorded in the manifest by version, with a policy unit test that
  execs a dynamically linked binary. In **containerized mode the
  image contents are the exec set** — everything present was
  declared.
- Known consequence, stated: env-RO denies `__pycache__` writes
  **for project-tree sources** (the env itself is pre-compiled:
  `--compile-bytecode` in both the direct-mode converge and the
  image build) — residual first-run slowdown for in-tree modules,
  visible in `--sandbox-debug`.

**Boundary placement — direct mode: the exec-shim.** The sandbox
wraps **the recipe, not uv**: step 3's command is

```
uv run --no-sync … -- python -m lightcone._sandbox_exec -- bash -c '<recipe>'
```

uv, its config, and its caches are trusted plumbing outside the
boundary (protected by the no-write worker posture). On Linux,
`run_rule` builds the Landlock **ruleset FD before fork** and passes
it down (`pass_fds` + env); the shim performs exactly two raw calls —
`prctl(PR_SET_NO_NEW_PRIVS)` + `landlock_restrict_self(fd)` — then
`os.execv`'s bash. No `preexec_fn`. **Open verification item
(spike): FD survival through uv's spawn/exec chain** — a Landlock FD
cannot be reopened; if uv drops non-stdio FDs, the documented
fallback is step 3 exec'ing the resolved interpreter directly,
bypassing the `uv run` hop. On macOS the shim execs
`sandbox-exec -f <generated profile> /bin/bash -c '<recipe>'` — the
profile generated with **realpath'd** paths including `TMPDIR`, plus
`(allow ipc-posix-shm*)`/`(allow ipc-posix-sem*)` and localhost
`network-outbound`. `sandbox-exec` cannot nest — a recipe invoking it
fails by design. The LocalCluster worker needs no exclusion: only
step 3 (the recipe exec, §6) is wrapped — this is why the design
composes with the fabric.

**Boundary placement — containerized mode: mounts bound the world,
in-container Landlock scopes the recipe.** The engine container
mounts: project tree RW at its identical absolute path (the engine
writes results and manifests); each declared external input RO;
scratch and a tmpfs `/tmp` RW; nothing else from the host. The env
is baked (`/opt/venv`) — no env mounts. Within the container, step
3 applies the **same Landlock exec-shim** per recipe (the container
is Linux on every host OS, macOS included), scoping each recipe to
project-RO + own-output-RW exactly as direct mode does. In-container
Landlock requires kernel ≥5.13 and a seccomp profile admitting the
`landlock_*` syscalls (default in current podman/GKE profiles;
probe-checked per job, never assumed). With it, file scope is
`declared` (mechanism `podman+landlock`); without it, the honest
weaker value `project-rw` (mechanism `podman`).

**The mechanism matrix:**

| Venue × mode | Mechanism | File scope | Network |
|---|---|---|---|
| Linux laptop / WSL2, direct | **Landlock** (vendored ctypes; floor ABI 1 / kernel 5.13; probe per job, ABI recorded) | `declared` | `unenforced` (ABI ≤ 3 has no network control; ABI 4 cannot express a loopback carve-out — recorded, not pretended) |
| macOS laptop, direct | **Seatbelt** generated SBPL (capability-checked per OS release; CI smoke) | `declared` | `denied` (non-loopback; localhost allowed) |
| Laptop, containerized | **podman+landlock** (probe-checked; `podman` without) | `declared` (`project-rw` without in-container Landlock) | `denied` — engine container runs `--net=none`, loopback intact (in-recipe LocalCluster/torch workers keep working) |
| Perlmutter, direct | **podman-hpc recipe wrap** with the static runtime image (step 3 only — never the dask worker). Mounts: project RO, own output dir RW, realpath'd uv interpreter dir RO, declared inputs RO, `$SCRATCH` RW — the normative policy, honestly expressed; `$PWD`-RW is **never** stamped `declared`. **Landlock replaces this wrap if the SLE-15 + Lustre spike passes** (deletion path pre-declared: the wrap, its mount code, and the static image's Perlmutter role all go) | `declared` | `denied` via `--net=none` — *provisional on the `--gpu`/`--net` spike; fallback posture if incompatible: GPU rules run unwrapped and record `mechanism: none` rather than pretend* |
| Perlmutter, containerized (spike-gated) | **podman-hpc full-stack** (project image; workers under `--net=host`) + in-container Landlock per recipe | `declared` (`project-rw` without) | `allowed` — `--net=host` is what dask requires, recorded honestly |
| Hub, direct | **pod** (static image) — the pod bounds the OS layer but worker pods mount all of NFS home; declared-file discipline comes from **Landlock inside the pod** (chart item: seccomp `RuntimeDefault` + `landlock_*` allowlisted) or not at all | `os-only` (`pod+landlock` ⇒ `declared`) | `allowed` unless NetworkPolicy — workers record `allowed` unconditionally (lc cannot observe NetworkPolicy) |
| Hub, containerized | **pod** (project image, full stack) + in-pod Landlock as above | `os-only` (`pod+landlock` ⇒ `declared`) | `allowed` (same rule) |
| Native Windows | none | `open`; WSL2 users get Landlock | — |

**Network enum, normative mapping:** `denied` means non-loopback
egress is blocked (loopback always intact — this is the spec's
meaning of denied, Seatbelt and `--net=none` alike); `allowed` means
lc applied no restriction (recorded whenever the mechanism's flags
did not deny — the matrix row is documentation, the *flags actually
applied* are what the manifest records); `unenforced` means the
mechanism cannot express a useful deny (Landlock). Per-output
network declarations are **deferred to §12** — v1 remedies for a
network-needing recipe are declaring the download as an input, or a
recorded `--no-sandbox` run.

**Manifest field:**
`hermeticity: {mechanism: landlock|seatbelt|podman|podman+landlock|podman-hpc|pod|pod+landlock|none,
fs: declared|project-rw|os-only|open, network: denied|allowed|unenforced,
landlock_abi?, exec_allowlist_version?}`.

**Probe, strictness, and never-silent — worker-side, per job**: the
capability probe runs in `run_rule` (the driver's kernel is not the
worker's) and populates the manifest there. When the probe lands
below the venue's expected level (`mechanism: none`, or `project-rw`
where `declared` was expected), lc records it **and prints one
console line** — a user must never finish a materialize believing
they were sandboxed when they weren't. `--require-sandbox` is
enforced there too: bare form requires `mechanism ≠ none`;
`--require-sandbox=declared-fs` additionally requires
`fs: declared`. **Maintenance note:** the podman-hpc and GKE-seccomp
behaviors were validated by one-time spikes, but NERSC and GKE
upgrade underneath them — the venue rows are a maintained surface
like the exec allowlist; revalidate on site changes (a periodic
canary materialize per venue is the cheap form).

**The denial UX — the design's primary UI (mandatory).** The parent
re-stats on `EACCES`/`EXDEV`/sandbox-`ENOENT` and classifies by
best-guess heuristic (exec bit / bin-dir path ⇒ tool; otherwise ⇒
data file), ordering remedies accordingly — both always shown, each
with a copy-pasteable fix:

```
blocked by lc sandbox: cannot execute /Library/TeX/texbin/latex —
not part of the declared environment.

  if this is a tool the recipe needs, declare it in the system layer:
      [tool.lightcone.image]
      system-packages = ["texlive-latex-base"]
    (apt package names — unsure? try: apt-cache search <tool>)
    note: this containerizes the project — podman required (macOS:
    one-time `podman machine` VM setup, ~minutes) — and re-stages
    all materialized outputs.

  if this is a data file, declare it as an input in astra.yaml:
      outputs:
        report:
          inputs:
            - path: /Library/TeX/texbin/latex

  diagnostics: lc run --sandbox-debug (shell inside the sandbox) ·
  lc run --no-sandbox (recorded as unsandboxed) · lc status
```

`--no-sandbox`/`--sandbox-debug` live in the subdued diagnostics
trailer, never as peer remedies. The tool → apt-name hint table is
**capped and versioned** (a dozen high-frequency tools: latex, R,
julia, convert, pdftoppm, …), falling back to the generic
`apt-cache search` line — never an open-ended mapping. A wrong
package name surfaces as lc parsing apt's
`E: Unable to locate package X` inside the build into "no apt
package named `X` — search with `apt-cache search`", never a raw
build log. **And on every nonzero sandboxed exit** — including
recipes that swallow the PermissionError — lc appends a fixed
one-line trailer: "this recipe ran under the lc sandbox (landlock) —
if the failure looks like a permissions/missing-file error, try
`lc run --sandbox-debug`". Threat model stated (declared-dependency
discipline against *accidental* leakage — metadata visibility,
interpreter-reads-script, memfd-exec are named adversarial-only
gaps); realpath every policy path.

## 8. Deleted ledger

v4's deletions stand (placement tiers, markers/leases/`lc env`
verbs, `lc doctor`, fingerprint ladder, projection document). v6/v6.1
amendments:

- **The pixi substrate (v5) is deleted** — rejected; decision record
  and re-add triggers:
  [substrate-default-tradeoffs.md](substrate-default-tradeoffs.md).
  The §13 pixi empirical evidence block is the sole authoritative
  copy and is retained by recorded decision.
- **The recipe-only container wrap (v6.0) is deleted** — replaced by
  full-stack-in-image after the review's host-sync deadlock finding:
  a hybrid that syncs the lock on the bare host cannot support
  lock-level system dependencies, which are the hatch's own §2
  examples.
- **`lc build` / `container.py` / `cloudbuild.py` return, reworked**
  — the tag+digest functions, the generated code-free Containerfile,
  and a three-file Cloud Build context replace the v3-era
  build-input hashing and project-tree staging.
- **Two flags deleted before birth**: `--accept-containerfile-loss`
  (v6.0 migration constant) — `lc init` instead *refuses* on an
  authored Containerfile ("v6 generates images from the lock —
  delete or rename it, then re-run `lc init`"; the user's own file
  operation is stronger consent than a flag); and
  `--sandbox-writable-project` — the ASTRA-declared per-output key
  is the single spelling (§7).
- **Per-output `network:` declarations** — dropped from the
  normative surface and the ASTRA migration step; returned to §12
  (the motivation stands: `--no-sandbox` being all-or-nothing is why
  the design deserves finishing).
- The **BYO digest-pinned per-output container** remains the
  schema's escape for truly arbitrary cases.
- Diagnostics re-add candidates: doctor-style checks, RECORD-hash env
  audit, trace attestation, image GC.

## 9. Fabric: dask, seam explicit, Ray escape hatch

Unchanged from v4 (keep dask; seam = `cluster_for_run` + executor;
Ray `working_dir`+uv is the shared-FS-less escape; re-evaluate on the
named triggers; dask-gateway risk + hub contract carried).

## 10. Alternatives

- **Dual-substrate uv + pixi (v5)** — rejected; see
  [substrate-default-tradeoffs.md](substrate-default-tradeoffs.md).
- **pixi-only** — rejected for the default on adoption grounds.
- **Recipe-only container wrap (v6.0)** — rejected by review: the
  host-sync deadlock (§8). Full-stack-in-image costs the podman-hpc
  worker-launch machinery on Perlmutter (spike-gated) and buys
  lock-level system deps, engine coherence, in-container Landlock on
  macOS, and the disappearance of the dual host/image environment.
- **Container-canonical (v1)** / always-container — rejected;
  Landlock/Seatbelt deliver direct-mode discipline with zero
  install, and the majority path never needs an image.
- **uv-only without the hatch (v4-pure + BYO)** — rejected: leaves
  G6's denial without a first-class remedy.
- bubblewrap — future upgrade tier. Trace-based hermeticity — future
  attestation. Snakemake `--sdm`, venv+activation, Ray-now —
  rejected as before.

## 11. Migration

Stage 1 (unchanged, first): rename `lc run`→`lc materialize`; the
new `lc run` probe verb **with the rename guard** (§4); hint; docs;
tests.

Stage 2 — constant: `LC_DELEGATED=1`. Steps:

1. **Environment layer**: `env_version`; lock scan; the §3 manifest
   field list; `SCHEMA_VERSION` bump; digest build-record; generator
   ignores legacy `container:`; golden fingerprint fixtures (direct
   and containerized).
2. `lc init` (uv scaffold; authored-Containerfile refusal; AGENTS
   stanza) + launcher (discover → mode-detect → scrub with
   site-registry re-injection → converge/resolve → exec; frozen
   delegation interface; `lc status` header lines; packaged×
   containerized refusal).
3. **Static runtime image** (deployment repo): slim base + uv +
   exec-shim, by digest — the hub's direct-mode substrate and
   Perlmutter's direct-mode wrap image; direct-mode Perlmutter
   preflight with the exact login-node command.
4. Fabric: no-write job command + worker sequence (pre-gate, env
   check/digest assert, boundary exec, post-gate); offline overlay +
   scrub unsets; driver git capture; connect probe (+digest);
   login-guard scoping; error-string sweep.
5. **Sandbox layer** (direct): vendored Landlock ctypes + policy
   builder (incl. **ELF loader tier** and **HOME/XDG contract**) +
   the `lightcone._sandbox_exec` shim (FD-inheritance spike first;
   direct-interpreter-exec fallback specced); Seatbelt generator +
   macOS CI smoke; podman-hpc direct wrap **with the honest mount
   set** (spike-contingent, deletion path pre-declared);
   probe/attestation + downgrade console line; the §7 denial UX
   (heuristic ordering, capped hint table, apt-error parsing, VM
   cost notice, failure trailer); `--require-sandbox` /
   `--no-sandbox` / `--sandbox-debug`.
6. **Container hatch (laptop + hub)**: `[tool.lightcone.image]` +
   `Containerfile.extra` parsing and full rendering, with the §2
   base-contract refusals (digestless base, musl base, apt-less base
   with `system-packages`, arm64 emulation warning); the
   Containerfile generator — apt layer before `uv sync`; **offline
   ENV (`UV_OFFLINE=1` etc.) emitted only in the final stage**, so
   the build's own sync layer keeps network (pinned by golden
   tests); `--compile-bytecode`; dpkg snapshot + content hash;
   world-readable; tag + digest record; builders — podman (canonical
   local), Cloud Build (three-file context); `lc build`; the
   full-stack run wrapper (engine-container mount set, `--net=none`,
   in-container Landlock probe); macOS `podman machine` preflight
   incl. the **mount-source share check**.
7. **Perlmutter containerized** (spike-gated): podman-hpc full-stack
   launch (`--net=host` workers, login-node build/migrate preflight,
   GPU-CDI gate with refusal until passed).
8. Hub plumbing: `LC_PROJECT_ENV` (direct); project-image pods +
   the notebook-`FROM`-project-image deployment contract with its
   preflight refusal (containerized); attestation-only image
   recording.
9. ASTRA: environment declaration = manifest+lock+system layer;
   `container:` derived/optional (BYO per-output container stays);
   the `sandbox: writable-project` output key; WRROC (the RO-Crate
   provenance archive format) archives the lockfile.
10. Eval re-baseline (sandbox on; one containerized eval task).
11. Cleanup (`_unpack_result`; superseded docs).

Tests land with their steps, plus: Landlock policy units — allow,
deny, crisp-message, **dynamically-linked-exec**, and the two
denial-fallback fixtures (a recipe that swallows PermissionError; a
rewrapped error that defeats the re-stat classifier — the trailer
must fire in both); Seatbelt smoke on macOS CI;
generated-Containerfile golden tests (incl. ENV ordering) + a
`podman build` smoke; mount-set units; CliRunner coverage for both
modes.

**Spike (gates the steps that name it):** the v4 Perlmutter items;
**Landlock ruleset-FD survival through `uv run`'s spawn/exec chain**
(gates step 5's shim shape); Landlock SLE-15 + Lustre (if it passes,
replaces the direct-mode podman-hpc wrap); podman-hpc recipe-wrap
smoke (mount set, `-e` pass-through, `--net=none`, the
`--gpu`/`--net` interaction with its stated fallback); podman-hpc
full-stack smoke (worker launch, `--net=host`, GPU CDI — gates step
7); rootless-podman laptop smoke (uid mapping, mount perf,
`--net=none`, **landlock syscalls under the default seccomp
profile**); macOS `podman machine` (file-sharing perf, tmpfs, exit
codes, share coverage); hub-pod seccomp (`landlock_*` under GKE
defaults).

## 12. Open questions

1. **The project survey** — which existing projects hold
   dependencies uv cannot source, and on which venues their users
   sit. Sizes the hatch population, gates eval depth, and remains
   the standing pixi re-add datum.
2. **apt pinning** — snapshot.debian.org (or distroless/wolfi bases)
   to harden the name-pin residue.
3. **Per-output `network:` declaration** — deferred from the v6.0
   surface; `--no-sandbox` being all-or-nothing is the motivation to
   finish the design (ASTRA schema; default deny where expressible).
4. **Per-output environments** — deferred; one-env-per-project is
   what this would relax deliberately.
5. **Landlock-on-Perlmutter** — spike-gated replacement of the
   direct-mode podman-hpc wrap.
6. **Image lifecycle** — `lc`-side image GC and an accumulation
   policy for Perlmutter's migrated images (today: podman's own
   `image prune`, documented).
7. **bubblewrap upgrade tier**; **wheel variants (PEP 825)** —
   every adopted variant shrinks the hatch population; **fabric
   re-evaluation clock**; **scale re-add trigger** — as v4.

## 13. Evidence appendix

Carried from v4 (fabric ground truth; uv 0.12.3 empirical set incl.
the `UV_OFFLINE` cache-write finding, `--exact` additive-sync
finding, read-only-venv warm-run verification, **explicit flags beat
ambient `UV_*` variables**, and `uv run`'s silent PATH fallback +
`--no-sync` disabling the locked check — the facts behind the hub
shim's fail-loud contract). **`uv add` semantics pass (uv 0.12.3,
2026-08-17, grounds §2's dependency-edit rules)**: on a
resolution-phase build failure (legacy sdist without static
metadata, build tool absent from PATH, cold cache) `uv add` **rolls
back** — `pyproject.toml` and `uv.lock` left untouched — and uv's
own hint names `--frozen`; on success, the auto-sync creates the
host `.venv` as a side effect (`--no-sync` avoids it); a warm uv
cache can mask the build failure entirely (the build is skipped on
a cache hit).
[hermeticity-enforcement.md](hermeticity-enforcement.md) — Landlock
ABI/distro map, Seatbelt precedents, bubblewrap gate, tracing.
Review-derived design facts (v5.1 + the v6 review record): the
two-tier exec baseline; the exec-shim boundary; Landlock
additive-rights expressiveness; **Landlock EXECUTE applies to the
ELF loader open** (the §7 loader tier); hub pod = `os-only` without
in-pod Landlock; Landlock ABI ≤ 3 has no network control and ABI 4
cannot express a loopback carve-out. **Open item, spike-tracked:
Landlock ruleset-FD inheritance through `uv run`** (§7/§11 — no
verified entry exists yet; the fallback is specced). Prior art for
the ladder: Metaflow `--environment=uv` (ships
`pyproject.toml`+`uv.lock`, syncs frozen, execs via
`uv run --no-sync`; no pixi backend exists — verified against source
at `2fb3c91`), Flyte/Union ImageSpec, Modal
(`Image.apt_install(...).uv_sync()`), ClearML `uv sync --locked`.
**Retained decision-record evidence — pixi 0.76.2 empirical pass**
(grounds the §8 rejection and any future re-add):
`--locked`/`--frozen` mirror uv; `--no-install` cold-env silent
host-PATH fallback; `pixi lock --check` writes on drift
(`--dry-run --check` is the safe form); env self-containment +
`env -i` direct exec; URL+sha256 PyPI locking and hash-free path-dep
entries; the closed `PIXI_*`/`CONDA_OVERRIDE_*` steering list;
interpreter build + channels/indexes inside `pixi.lock`; lock header
`version: 7`. **uv→pixi migration pass** (pixi 0.76.2 · uv 0.12.3):
`[project.dependencies]` carry over verbatim; same-day migration
resolves byte-identical PyPI artifacts; pixi ignores
`.python-version`; no uv.lock import exists; bare `pixi init` on an
existing pyproject prompts interactively, injects an editable
self-dependency, and defaults to a single platform. Review record:
[execution-environment-v6-review.md](execution-environment-v6-review.md).
