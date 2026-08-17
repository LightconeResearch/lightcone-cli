# User story: the locked environment is the execution environment

- **Status:** narrative companion, v3 — non-normative. This document
  tells the [execution-environment.md](execution-environment.md) spec
  (v6.1: uv-only, sandbox-enforced, full-stack container hatch) as a
  sequence of user stories: what a researcher and their coding agent
  actually type, see, and get, from first install through laptop →
  Perlmutter → hub, in both modes (direct and containerized),
  including the failure modes the design converts into pointed
  errors. Where this document and the spec disagree, the spec wins.
- **Personas:**
  - **Riley** — a cosmology postdoc. Comfortable in a terminal, has
    never written a Containerfile, does not want to. Works on a macOS
    laptop, runs real jobs on Perlmutter (2–4 nodes), sometimes on
    the lab's JupyterHub.
  - **The agent** — a coding agent (Claude Code, Codex, …) working in
    Riley's project. Per the eval evidence, the agent is the primary
    *interface* to lc: it knows uv from training data, it follows
    crisp error messages, and its historical failure mode is
    environment-boundary confusion. `lc init` scaffolds the agent
    notes it needs (the boundary rule, the four-verb map) so it
    succeeds without reading the spec.
  - **Sam** — a collaborator who receives Riley's repo a year later
    and has to trust, verify, and extend the results.

---

## Story 1 — Day one on a laptop

Riley starts a new weak-lensing analysis on their laptop.

```
$ curl -LsSf https://astral.sh/uv/install.sh | sh   # if uv isn't there yet
$ uv tool install lightcone-cli
$ mkdir wl-analysis && cd wl-analysis
$ lc init
```

That is the entire on-ramp: **uv is the single prerequisite**, and it
is the same tool that installs `lc` (spec G1, §2). No Docker, no
conda, no activation scripts. Bare `lc <verb>` is the one spelling
Riley ever types, on every venue (§4). `lc init` scaffolds
`astra.yaml`, `pyproject.toml` (with `lightcone-cli` as a locked
dependency — the engine is *inside the experiment's lock*),
`.python-version` with an exact interpreter patch, an agent-notes
stanza, and runs `uv lock`. There is no Containerfile in the
scaffold — a direct-mode project never builds an image at all
(G5, §1).

Riley adds dependencies with the native tool — lc never wraps
dependency management (§1):

```
$ uv add numpy scipy astropy
```

The agent probes the environment through the one run verb:

```
$ lc run python -c "import scipy; print(scipy.__version__)"
$ lc run python src/fit.py --output /tmp/probe
```

`lc run` is byte-for-byte the recipe environment — same lock, same
converged `.venv`, and the **same sandbox** recipes get (G4, §4).
The boundary rule the agent is taught (and that `lc init` wrote into
the agent notes) is one sentence: *if a `ModuleNotFoundError` in
this command would mean "fix `pyproject.toml`", it belongs in
`lc run`.* Everything else (git, editors, `lc` itself) stays on the
host. And Riley's old muscle memory is guarded: typing
`lc run best_fit` (the pre-v6 grammar) doesn't exec a mystery shell
command — it errors immediately with *"outputs are materialized, not
run — did you mean: `lc materialize best_fit`?"* (§4).

When the pipeline is declared in `astra.yaml`, Riley materializes:

```
$ lc materialize
```

The driver converges the environment once (`uv sync --locked
--exact --compile-bytecode`), starts the run-scoped cluster, and
each rule's recipe runs inside the sandbox with its manifest written
next to its output:

```
results/fiducial/best_fit/data.txt
results/fiducial/best_fit/.lightcone-manifest.json
```

The manifest records `env_version` (lock + interpreter pin + install
settings + the — here empty — system layer), `git_sha`/`git_dirty`,
the platform, and a `hermeticity` block saying exactly what
enforcement the output ran under — on this macOS laptop:
`{mechanism: seatbelt, fs: declared, network: denied}` (§3, §7).
Inside the sandbox each recipe gets a fresh private `HOME` under the
tmp scope, so matplotlib and astropy work on first import without
ever reading Riley's real dotfiles (§7).

**What Riley never did:** write a Dockerfile, build an image,
activate a venv, or export an environment variable.

## Story 2 — The sandbox catches the leak

Riley's report rule shells out to `latex`. It works — because
`latex` happens to be installed on Riley's laptop via MacTeX. On any
other machine it would fail, and the manifest would have claimed a
pinned environment that silently depended on an undeclared host
tool. This is the #1 leakage channel the design exists to catch
(G6, §7).

With the sandbox on by default, the recipe fails **at
materialization time, loudly** — and the denial message is the
design's primary UI (§7): it classifies the denial (executable ⇒
probably a tool; plain file ⇒ probably data), leads with the likely
fix as copy-pasteable TOML or YAML, states the real cost up front,
and keeps the escape hatches in a subdued diagnostics trailer:

```
blocked by lc sandbox: cannot execute /Library/TeX/texbin/latex —
not part of the declared environment.

  if this is a tool the recipe needs, declare it in the system layer:
      [tool.lightcone.image]
      system-packages = ["texlive-latex-base"]
    (apt package names — unsure? try: apt-cache search latex)
    note: this containerizes the project — podman required (macOS:
    one-time `podman machine` VM setup, ~minutes) — and re-stages
    all materialized outputs.

  if this is a data file, declare it as an input in astra.yaml.

  diagnostics: lc run --sandbox-debug · lc run --no-sandbox
  (recorded as unsandboxed) · lc status
```

`--no-sandbox` is never a peer remedy — it's a diagnostic, and using
it is recorded (`hermeticity: {mechanism: none, fs: open}` — never a
silent downgrade). Even when a recipe *swallows* the permission
error and dies with an unrelated traceback, lc appends one fixed
line — *"this recipe ran under the lc sandbox (seatbelt) — try
`lc run --sandbox-debug`"* — so neither Riley nor the agent flails
against an invisible wall (§7). Taking the tool remedy is Story 3.

The same fence protects the project from the recipe: writes are
scoped to the rule's own `results/<u>/<o>/` (+ scratch/tmp), so a
misbehaving script cannot clobber sibling outputs, manifests, or
`astra.yaml`. A recipe that legitimately writes intermediates in the
tree declares `sandbox: writable-project: true` on its output in
astra.yaml — a fact in the repo, not a flag lost to shell history —
and its manifest records the honest weaker scope `fs: project-rw`
(§7).

## Story 3 — The project outgrows PyPI: the container hatch

Riley pastes the TOML from the error message — and while they're at
it, admits the project also needs R (there's a legacy likelihood
that only speaks it, reached via `rpy2`):

```toml
[tool.lightcone.image]
system-packages = ["texlive-latex-base", "r-base-core"]
```

That declaration *is* the escalation (§1: derived, not configured —
and reversible: delete the list and the project is direct again).
The message already told Riley the two costs: podman (the only extra
install containerized mode ever costs — and the tool a project
headed for HPC or k8s wants eventually anyway), and the blast
radius — `env_version` moves, so **all** materialized outputs go
stale, exactly like any environment edit (§1, §3).

The next `lc materialize` builds the image and says so:

```
building lc-env-9f2c81d44a1b03e7 (first run after an environment
change; ~minutes)
```

— `lc run` never builds; it errors with the exact `lc build`
command instead, so a two-second probe never silently absorbs a
build (§4). Inside the image, the apt layer is installed *before*
`uv sync` runs — which is why `rpy2` works: the lock's own packages
build against the declared system layer, not against Riley's bare
laptop (§2). From then on **the image is the execution world**:
driver, workers, recipes, and probes all run from its baked
`/opt/venv`; there is no host `.venv` at all (§1). The container
bounds the world to the project + declared inputs, and inside it the
same Landlock shim scopes each recipe to its own output dir — on
macOS too, since the podman VM is Linux — so the manifest says
`{mechanism: podman+landlock, fs: declared, network: denied}` (§7).

Anyone — Riley included — can check what state the project is in:

```
$ lc status
mode:    containerized (2 system packages)
image:   lc-env-9f2c81d44a1b03e7 — built (digest sha256:4c1f…)
sandbox: podman+landlock (fs: declared, network: denied)
```

Everything else in Riley's muscle memory is unchanged: `uv add`
still manages Python deps; `lc run`, `lc materialize`, `lc status`,
`lc verify` behave identically. Rebuilds happen only when the
environment changes — never on code edits (G5). The honest residue
is stated, not hidden: apt is name-pinned, so the build records a
dpkg snapshot (content-hashed into the manifest), and the macOS VM
means linux builds and no GPU — `platform` attested from inside the
boundary, which is where everything now runs (§3, §5).

## Story 4 — Same project family, Perlmutter

The weak-lensing project from Story 1 — still direct mode, pure
PyPI — needs 2–4 GPU nodes. The venue change is a clone and an
allocation (§5):

```
perlmutter$ uv tool install lightcone-cli      # once
perlmutter$ cd $CFS/myproj && git clone … wl-analysis && cd wl-analysis
perlmutter$ salloc -N 2 …
perlmutter$ lc materialize
```

The project lives on **CFS, not `$HOME`** — CFS is writable from
compute nodes, so environment convergence works mid-allocation. The
cache placement (`$SCRATCH/uv-cache`, copy link-mode) comes from
lc's site registry and is injected by the launcher after the ambient
scrub — Riley exports nothing (§4). The env is the same in-tree
`.venv` as the laptop.

The execution discipline is the spec's one sentence: **converge
once, then never write to the environment** (§6). Every job runs
`uv run --no-sync` with the offline overlay, so a hundred workers
never race installs onto Lustre — a warm env is a no-op check, drift
is a loud failure. Each recipe is wrapped in **podman-hpc** using
the venue's static runtime image, with the honest mount set —
project **read-only**, own output dir RW, declared inputs RO — so
the manifest's `fs: declared` means the same thing here as
everywhere else (§7). The one venue chore is fetching that static
image once per login node era; forget it and the compute-node
preflight prints the exact login-node command instead of a podman
error (§5).

**Mid-run edit, caught.** While rules are running, the agent
helpfully runs `uv add emcee` in another pane. The next rule's
pre-gate re-hashes the lock against the run-start `env_version` and
fails with *"lock changed mid-run — re-run lc materialize"* (§6).

(The containerized R project of Story 3 can come here too — its
image built and migrated once on a login node, its workers launched
inside it — but that path rides the podman-hpc full-stack spike, and
until the GPU question clears, lc says so plainly instead of
pretending: §5.)

## Story 5 — Same project, the hub

Riley opens the lab JupyterHub, clones the direct-mode repo into NFS
home, and runs `lc materialize` from the notebook pod. What does
*not* happen: no Cloud Build job, no per-project image, no waiting
for a build-and-push before the first run.

Worker pods run a **static, deployment-managed runtime image**
(referenced by digest; slim base + uv + the exec-shim). Riley's
environment, interpreter, and code all ride the NFS home the pods
already mount; lc passes the env location as `LC_PROJECT_ENV`
through the standard cluster options, and the shim execs from it —
failing loudly if it's unset or absent, never falling back to the
image's Python (§5).

Honesty in the manifest: the pod bounds only the OS layer, so
hermeticity is recorded as `fs: os-only` — upgraded to `declared`
only where in-pod Landlock is actually enabled, and network recorded
`allowed` (lc cannot observe NetworkPolicy) rather than pretended
(§7). This is also where G4's one documented exception lives:
notebook-pod probes share the lock but not the workers' OS layer.

(The containerized project runs here with its **own image as the
worker pod** — full stack, built by Cloud Build from three files,
never containing code — and the deployment derives the notebook
image `FROM` the project image, which closes even the G4 exception
on this venue: §5.)

**Editing code between runs costs nothing.** Riley edits
`src/fit.py`, re-runs `lc materialize`, and only the affected rules
re-execute — a code edit never triggers an image build in either
mode, because no image ever contains project code (G5).

## Story 6 — The environment changes; the blast radius is surfaced

Riley upgrades numpy:

```
$ uv lock --upgrade-package numpy
$ lc materialize
environment changed: 14 materialized outputs are now stale
```

`env_version` moved, so `code_version` moved, so Snakemake's rerun
triggers fire — the one case where re-materialization is *correct*,
announced at decision time rather than discovered later. In direct
mode there is no image rebuild and no tag bookkeeping — the identity
is the lock itself; in containerized mode this is the one moment a
rebuild happens, which is exactly when it's meaningful (G3, G5, §3).
And a rebuilt image is not trusted by name: the run pins the digest
the driver resolved, and every worker asserts it — two nodes can
never silently run different userlands under one tag (§3, §6).

The same identity machinery refuses the unauditable: a path
dependency (`uv add ../my-hack`) is rejected at lock-scan time —
except the project's own package, which is exempt (§3). Non-default
dependency groups draw an advisory ("outside lc's guarantees"), and
the sandbox — whose allowlist contains only the project environment —
is what makes that rule real rather than documentation (§7).

## Story 7 — A year later: Sam verifies, reproduces, extends

Sam clones the repo (results synced alongside) and, before trusting
anything:

```
$ lc status      # offline, local-only — mode, image state, sandbox
$ lc verify
```

`lc verify` recomputes each output's `data_version` and walks the
provenance chain; failures surface as `tampered_data`,
`broken_chain`, or `missing_manifest` — and it surfaces
**dirty-tree** and **unsandboxed** outputs distinctly (§3). Sam can
see, per output, not just *what* environment produced it but *what
enforcement it ran under*: an output with `hermeticity: {mechanism:
landlock, fs: declared}` earns different trust than one with
`{mechanism: none, fs: open}` — or the honest middle ground
`fs: project-rw`. For a paper's final runs, Sam's CI adds
`--require-sandbox=declared-fs`, refusing any output below that bar
(§7). Outputs written under an older manifest schema report
`pre_migration` under the `SCHEMA_VERSION` bump, distinct from
tampering.

Reproducing is the same on-ramp as Story 1: install uv (plus podman
if the project declares a system layer — the mode is derived from
the files, Sam doesn't have to guess), `lc materialize`. The
lockfile *is* the environment definition; the system layer is one
TOML list; even the dpkg snapshot of the image that built the
figures is content-hashed in the manifests, so the audit outlives
any image registry. The claim Sam gets is the honest one the spec
commits to: **pinned environment identity, never bit-identical
outputs** (G3) — BLAS dispatch and thread scheduling still vary by
hardware, and the manifest's attestation fields say so instead of
pretending.

## Story 8 — Migrating the existing project

Riley's older project has the v3-era scaffold: an authored
Containerfile and image tags in its manifests. `lc init` on it
converges the scaffold — and on finding the authored Containerfile
it **refuses with instructions** rather than hiding consent behind a
flag: *"found an authored Containerfile; v6 generates images from
the lock — delete or rename it, then re-run `lc init`"* (§8). Old
outputs are not invalidated wholesale: `lc verify` reports them as
`pre_migration`, distinct from tampering. New runs write current
manifests; the two coexist in one results tree.

Containers in Riley's life are now generated, never authored: the
hatch's derived image (§3) for declared system layers, and — for
truly arbitrary cases — the digest-pinned **BYO** per-output
container (§8), declared in `astra.yaml`, hashed into identity, the
one container lc does not generate.

---

## What the stories never contain

The negative space is the design (§8 deletion ledger):

- No second package manager: uv is the only substrate, on every rung
  of the ladder.
- No authored Containerfile, ever — images exist only for projects
  that declare a system layer, are generated from the lock, tagged
  by content, digest-pinned at run time, and never contain project
  code.
- No container runtime on any direct-mode laptop — Landlock and
  Seatbelt deliver the recipe fence with zero install; podman
  appears only when a project's dependencies leave PyPI.
- No dual environments: a containerized project has no host `.venv`
  — the image is the execution world.
- No environment verbs, placement tiers, markers, or leases — the
  env is in the project tree (or the image) on every venue.
- No `requirements.txt`, no activation, no hand-exported venue
  variables.
- No flags for one-time events or repo-external behavior changes —
  migration consent is a file operation; writable-project is an
  astra.yaml declaration.
- No one-way doors: the escalation is a declaration, and deleting it
  de-escalates.
- No pretending: every place enforcement or identity is weaker (hub
  pods without Landlock, Landlock's missing network control, apt's
  name-only pinning, `project-rw` scopes, sdist builds, host GPU
  drivers), the manifest records the truth — and a downgrade prints
  a console line, never just a field (§7).

## Traceability

| Story | Spec anchor |
|---|---|
| 1 — day one | G1, §2 (uv project, agent notes), §4 (launcher, `lc run`, rename guard), §3 (manifest) |
| 2 — sandbox denial | G6, §7 (policy, HOME/XDG, denial UX, failure trailer, writable-project key) |
| 3 — container hatch | §1 (full-stack ladder, blast radius), §2 (system layer), §3 (image identity, dpkg residue), §4 (build moment, `lc status`), §7 (in-container Landlock) |
| 4 — Perlmutter | §4 (site registry), §5 (venue row, static-image preflight), §6 (discipline, mid-run gate), §7 (honest mount set) |
| 5 — hub | G4 exception, §5 (static image + `LC_PROJECT_ENV` / project-image pods + notebook-FROM contract), §7 (`os-only`, network honesty) |
| 6 — env change | G3, G5, §3 (identity, digest pinning, lock scan) |
| 7 — verify | §3 (attestation, schema), §7 (`hermeticity`, `--require-sandbox`, downgrade notice) |
| 8 — migration | §8 (Containerfile refusal, BYO container), §11 (stages, `pre_migration`) |
