# Decision analysis: uv vs pixi as the default substrate — and the cost of carrying both

- **Status:** decision analysis, v1. Weighs the substrate *posture*
  question the v5.1 spec settled implicitly: uv-only, pixi-only, or
  dual-substrate — and if dual, which is the default. Prompted by the
  observation that the dual design's biggest cost is **maintaining two
  environment types**. Draws on
  [uv-vs-pixi-adoption.md](uv-vs-pixi-adoption.md),
  [environment-substrate-evaluation.md](environment-substrate-evaluation.md),
  the spec's pixi 0.76.2 empirical pass, this week's uv→pixi migration
  experiment (pixi 0.76.2 · uv 0.12.3), and a source read of
  Metaflow's environment backends (commit `2fb3c91`, 2026-08-15).
- **Date:** 2026-08-16

## The actual question

"uv or pixi as default" is really three postures, because pixi's
embedded uv means pixi *alone* can cover every project, while uv
alone cannot cover polyglot ones:

- **A. uv-only** (the v4 spec): one substrate; polyglot needs are met
  by the digest-pinned BYO container escape hatch.
- **A′. uv + container hatch on demand**: one substrate; no container
  at all by default; when a project needs a dependency uv cannot
  source, the sandbox denial nudges it into a *declared* system layer
  (`system-packages` → generated, content-addressed container run via
  podman) — the Modal/Flyte ImageSpec model. See the Decision section.
- **B. pixi-only**: one substrate; pure-Python projects ride pixi's
  embedded uv for the PyPI side.
- **C. dual, uv default** (the v5.1 spec): uv unless `lc init --pixi`.
- (C′. dual, pixi default — strictly dominated: it pays C's full
  maintenance bill while putting the weaker-adoption tool in front of
  every user. Not considered further.)

## The dual-substrate maintenance bill, itemized

The con deserves to be priced, not hand-waved. What "two environment
types" actually costs, per the v5.1 spec's own structure:

| Cost | Size | Recurring? |
|---|---|---|
| Two `Substrate` implementations (9 ops each) | ~300–600 LOC + init templates | no — written once |
| Two golden fingerprint suites, two CliRunner test matrices | moderate | every engine change touching identity |
| **Empirical re-verification per tool release** | the 0.76.2 pass pinned ~10 load-bearing behaviors (`--no-install` cold-env fallback, `lock --check` writes on drift, flag-vs-env precedence, …); uv 0.12.3 has its own verified set | **yes — every supported-version bump, ×2 tools** |
| Two ambient-scrub lists (`UV_*`, `PIXI_*`/`CONDA_OVERRIDE_*`) | closed lists | audit on every tool release |
| Two hub exec-shim modes; two podman-hpc mount sets | small | venue changes ×2 |
| Detection edge cases (both manifests, both lockfiles, `[tool.pixi]`-in-pyproject) | small code, real support surface | user-facing forever |
| Per-mode asymmetries needing compensation (pixi's missing worker env-vs-lock check → mandatory env-prefix gate) | design complexity | forever |
| Docs, eval prompt, agent guidance ×2 modes | moderate | forever |
| CI needs both tools installed and warm | infra | forever |

The one-time cost is genuinely small (Metaflow's uv backend is ~230
lines against its conda backend's ~1,800 — backends that delegate to
a lock-owning tool are thin). The **recurring verification burden is
the real bill**: two fast-moving pre-1.0/0.x tools whose flag
semantics are load-bearing for integrity guarantees, each needing an
empirical re-pass on version bumps. That bill is capped by the
protocol design (no substrate conditional outside the 9 ops), but it
never goes to zero.

Two honest discounts on the bill:

- Several "pixi compensations" are good hygiene uv-mode already wants
  (env-prefix existence check, explicit project pins, ambient scrub) —
  they'd survive a pixi deletion.
- The fabric, manifest chain, sandbox, and CLI surface are
  substrate-blind by construction; the 2× is confined to the edge.

## A. uv-only

**Pros**

- **One environment type** — the entire bill above halves; one tool
  to empirically track, one scrub list, one test matrix.
- The default path keeps every adoption advantage: agents know uv
  from training data (the eval failures were agent
  environment-boundary confusion — "the agent already knows the tool"
  is a reliability property, not a popularity contest); ~196M
  downloads/month; native IDE/Dependabot/CI support; zero extra
  install; `lc` itself arrives via `uv tool install`.
- Battle-tested prior art for exactly this architecture
  (Flyte/Union, Metaflow, Modal, ClearML, Ray's uv hook).
- Current workloads don't exercise the conda layer: dask-over-TCP (no
  MPI), PyPI CUDA wheels, vendored BLAS — structural for MPI,
  verified for the fabric.

**Cons**

- Polyglot projects (R, Julia, TeX, compilers, HDF5) get a materially
  worse story: system tools become README prose + host attestation,
  or a BYO container — reintroducing exactly the container-authoring
  burden the design deleted, for the projects least equipped to
  carry it.
- The sandbox's sharpest guarantee (G6: undeclared tool ⇒ loud
  failure) loses its remedy in uv mode: lc can *catch* the host
  `latex` leak but can't offer a declared home for `latex`. Enforcement
  without a fix path invites `--no-sandbox` habituation.
- Interpreter identity stays attestation-grade (version pinned, build
  as residue) rather than content-addressed.
- The unsurveyed presumption: "no current project needs conda-forge"
  has not been checked against actual projects. If wrong, uv-only
  fails the very users the sandbox was built to protect.

## B. pixi-only

**Pros**

- **One environment type** — same halving of the bill, and no
  detection logic, no migration story, no one-way door, no
  mode-asymmetry table in the docs.
- Strictly stronger identity everywhere: interpreter build
  content-addressed in the lock; system layer (conda-forge, URL +
  sha256) in the lock; channels and indexes inside the lock bytes.
- The sandbox story is uniformly clean: every denial has the same
  one-line fix (`pixi add <tool>`); the exec-tier allowlist debate
  shrinks.
- PyPI resolution quality is uv's own (pixi embeds uv) — verified to
  resolve byte-identical PyPI artifacts in the migration experiment.
- Self-contained envs (`env -i <env>/bin/python` verified) simplify
  the hub shim and the podman-hpc mount set to one mode.

**Cons**

- **Every project pays pixi's costs, including the pure-Python
  majority**: the extra binary bootstrap on every venue (not
  pip-installable), conda-forge as a mandatory channel (bigger envs,
  channel churn in the lock), and pixi's verified traps
  (`--no-install` silent host-PATH fallback on cold envs,
  `lock --check` writing on drift) on the *default* path rather than
  the opt-in path.
- Adoption is an order of magnitude behind and not closing: absent
  from usage surveys, ~12 Stack Overflow questions, a 5-person
  pre-1.0 vendor. For an agent-first tool this is the decisive
  reliability gap — agents reach for `uv add`/`uv run` unprompted and
  must be *retrained by prompt* into pixi verbs on every project.
- No battle-tested workflow-system precedent: Metaflow, Flyte, Modal,
  ClearML, Ray — all uv, none pixi (verified against Metaflow source;
  no pixi backend exists anywhere in that tier). lightcone would be
  first, alone, on the vendor's schedule.
- Sustainability risk concentrates in one small vendor; uv-only risk
  concentrates in Astral, which is VC-funded but an order of magnitude
  more entrenched.
- Weaker worker-side verify (no true env-vs-lock no-write check)
  becomes the *only* posture, not the exceptional one.

## C. Dual, uv default (v5.1)

**Pros**

- Each project gets the right tool: the majority path keeps uv's
  adoption/agent advantages at zero extra cost; polyglot projects get
  first-class locked system layers instead of containers or prose.
- The sandbox guarantee keeps a remedy in both modes (declare in
  pixi; convert or containerize in uv).
- Preserves optionality: if pixi's ecosystem position improves (or
  collapses), the default can move (or the mode can be deleted)
  without an architecture fork — the protocol is the hedge.

**Cons**

- **The maintenance bill above** — dominated by the recurring
  two-tool empirical verification, forever.
- A user-facing mode split: two init paths, two dependency verbs, an
  asymmetry table users eventually meet ("why is worker verify weaker
  here?"), detection edge cases, and a one-way migration door.
- Intersection semantics: lc's guarantees cover only the default env
  in both modes — the contract must be stated twice and enforced at
  the sandbox layer.
- Risk of the untested half: if few projects actually choose pixi
  mode, it becomes the rarely-exercised branch — precisely where
  integrity bugs hide. (Mitigation: CI parity, pixi-mode eval
  coverage — which is itself more of the bill.)

## Where the evidence points

The decision hinges on one unmeasured quantity: **what fraction of
real Lightcone projects need the conda-forge layer.** The rationale
doc already flags this survey as open; it is the deciding datum:

- **≈0%** → A (uv-only) wins: the bill halves, the BYO-container
  escape hatch covers the tail, and nothing of value is lost. The
  cost of being wrong later is bounded — the substrate protocol can be
  re-added (it was designed once; the pixi empirical pass is
  documented).
- **A small-but-real minority (the presumed case)** → C stands, and
  the bill is the price of first-class polyglot without making the
  majority pay pixi's costs. Keep the bill capped: support exactly one
  pinned version range per tool, gate bumps on the empirical
  checklist, and require pixi-mode CI/eval parity from day one.
- **A majority** → B becomes thinkable — but only if the agent
  problem is solved by prompting (the eval must demonstrate agents
  operating pixi verbs as reliably as uv verbs), since the eval
  evidence is the design's origin. Adoption trend would need to have
  turned as well.

A useful asymmetry when weighing A vs C: **deleting pixi mode later
is cheap; adding it later is also cheap** (the protocol and the
empirical pass are the expensive artifacts, and both now exist on
paper). The genuinely expensive commitment would be B — betting the
default on the smaller ecosystem — because walking *back* from
pixi-default means migrating every project across a one-way door in
reverse (conda deps have no uv home).

## Recommendation (superseded by the Decision below)

Run the project survey before paying another increment of the dual
bill. Until it lands: keep C (dual, uv default) as specified, but
treat pixi mode as **frozen at one pinned version** (no
version-range chasing) and make the survey a migration-step-7
prerequisite — if the survey comes back empty, ship stage 2 as A
(uv-only) and leave the pixi implementation as a documented,
evidence-backed re-add rather than shipped code.

## Decision (2026-08-16): A′ — uv + container hatch on demand

Adopted as **spec v6**
([execution-environment.md](execution-environment.md)). A′ refines A
with an escalation path that keeps G6's denial actionable: the
default is pure uv with zero extra installs; declaring
the `[tool.lightcone.image]` table (or `Containerfile.extra`) flips
the project into containerized mode — lc renders the lock + declared
system layer into a content-addressed image (never a user-authored
root Containerfile), run via podman / podman-hpc / pods.

Why A′ over C (dual substrate), beyond the bill above:

- **Every posture asks the user to install exactly one extra tool**
  — pixi in C, podman in A′. Podman is the install that generalizes:
  a project that grows complex and deploys to HPC or k8s wants a
  container eventually anyway, so the escalation converges with where
  such projects were headed; pixi is a dead-end install by
  comparison. (And a container runtime is arguably baseline developer
  tooling in a way pixi is not.)
- **Shipped precedent instead of none**: uv-lockfile-first + derived
  container with a declared apt layer is exactly Modal
  (`Image.apt_install(...).uv_sync()`), Flyte/Union ImageSpec, and
  the pattern Metaflow ships — no workflow system ships pixi.
- **Reversible escalation**: deleting the declaration returns the
  project to direct mode; the uv→pixi migration was a verified
  one-way door.
- **Agent familiarity on both rungs**: agents know uv *and*
  apt/containers from training data; pixi verbs they do not.
- Honest costs, accepted: apt is name-pinned only (dpkg-snapshot
  attestation, snapshot-pinning as future hardening) — weaker
  system-layer identity than `pixi.lock`'s content-addressing; macOS
  escalation means a `podman machine` VM (linux builds, no GPU); G4
  requires probes to run in-container once escalated.

Standing pixi re-add triggers (unchanged in substance): the project
survey reveals a meaningful laptop-centric conda-layer population
for whom a container VM is unacceptable; or pixi reaches 1.0 with an
adoption inflection. The pixi 0.76.2 empirical pass is retained in
the spec's evidence appendix so a re-add starts from documented
ground, not from scratch.

## Amendment (2026-08-17): full-stack containerized mode (spec v6.1)

The v6.0 form of A′ wrapped only the *recipe* in the container while
the engine and workers synced the full lock on the bare host. The
multi-agent review
([execution-environment-v6-review.md](execution-environment-v6-review.md))
confirmed this deadlocks on lock-level system dependencies (rpy2
needing R, sdist builds needing headers) — the hatch's own examples.
v6.1 adopts the scope-preserving resolution: **in containerized mode
the entire execution stack (driver, workers, recipes, probes) runs
inside the image**; no host environment exists for the project. This
closes the deadlock (uv sync runs inside the image build, after the
apt layer), preserves engine-version coherence, deletes the dual
host/image environment, and — because the container is Linux on
every host OS — enables per-recipe Landlock scoping even on macOS.
Costs, accepted: Perlmutter containerized requires the podman-hpc
full-stack worker launch (`--net=host`, GPU-via-CDI spike-gated),
and the hub deployment contract gains a
notebook-image-`FROM`-project-image item. This strengthens the
"podman generalizes" argument above: the same install now carries a
project from laptop escalation to HPC and k8s without an
architecture change.
