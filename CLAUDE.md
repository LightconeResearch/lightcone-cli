# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**lightcone-cli** ships `lc`, the execution layer for ASTRA (`astra.yaml`
analysis specs). The split with `astra-tools` is enforced in code: what a
spec *means* (scoping, `from:`, `when:`, universes, the recipe placeholder
grammar) is answered by `astra.resolve` / `astra.validation`; lc only adds
execution — scaffolding, environment identity, sandboxing, storage,
provenance. If a spec's meaning looks wrong, the fix belongs in
astra-tools, not here.

The CLI is agent-agnostic: it bundles no skills, hooks or plugins, and a
verb is run by an agent more often than a person — so no interactive
prompts, ever.

## Commands

```bash
uv sync --group dev                      # pytest, ruff, mypy, datalad, playwright, roc-validator
uv run pytest                            # whole suite
uv run pytest tests/test_plan.py         # one file
uv run pytest tests/test_plan.py::test_name   # one test
uv run pytest -k "foreign_write"         # by keyword
uv run ruff check src/ tests/            # --fix to apply
uv run mypy src/                         # strict
cd ui && npm ci && npm run build         # the `lc view` front end → src/lightcone/app/static/
uv build                                 # wheel + sdist; runs the npm build via hatch_build.py
```

Test, lint and type-check are the whole loop and exactly what
`.github/workflows/{tests,lint}.yml` run. There is deliberately no task
runner.

- Python work never needs Node: an editable install skips the front-end
  build. `lc view` and the UI tests do need it (plus
  `python -m playwright install chromium` for `test_view_browser.py`).
- Several suites **skip** when their mechanism is absent and CI turns the
  skip into a failure via env vars: `LC_SANDBOX_TESTS_REQUIRED`,
  `LC_CONTAINER_TESTS_REQUIRED`, `LC_CRATE_TESTS_REQUIRED`,
  `LC_UI_TESTS_REQUIRED`. A local green run may have skipped them.
- CI runs Linux (py 3.11–3.13) **and** macOS: the sandbox enforcement
  suite is one set of tests over Landlock and Seatbelt, so never assert
  one mechanism's or one platform's wording.
- The `uv-floor` CI job runs the uv-touching suites under uv 0.7.0, the
  floor in `templates/files/pyproject.toml.tmpl`. Using a newer uv flag
  means raising both together.
- PRs need a DCO sign-off (`dco.yml`).

## Rules (each was asked for in review; none is optional)

- **No backward-compatibility code.** No guards, versions or migrations
  for older CLIs, formats or habits.
- **No foreshadowing, no dead code.** Nothing — code, comment, flag,
  message — mentions a feature that does not exist yet; nothing lands
  without a caller. `lc --help` advertises only verbs that work.
- **No escape hatches around guarantees.** Enforcement ships without a
  flag to turn it off (there is no sandbox opt-out, no `--jobs`, no
  skip-rebuild flag).
- **Literal behavior over convenience.** The current directory is the
  project root; no walk-up discovery; error rather than guess.
- **Streamline.** No small helpers or rendering layers where a few inline
  lines read fine. Don't add a flag whose only user is a test — stub the
  seam instead.
- **Never reference a design spec, section number or history in code or
  comments.** Comments carry *why*, not how the code came to be.
- **Leave working files alone**; don't touch files merely adjacent to a
  change.
- **Honest provenance.** Adapted third-party material is marked "inspired
  from", with its license (the vendored `.sbpl` profiles, the app after
  JupyterLab's `examples/app`).
- **A dependency enters `pyproject.toml` with the code that needs it.**
- Every change ships tests. When adding a *denial* test, mutation-check
  it: the same command through `Unavailable()` must succeed, and the
  target must be a path the OS itself would allow (not `/etc`, not under
  `/tmp`, which the policy grants).

Conventions: ruff (E, F, I, N, W, UP; line length 100; py311), mypy
strict with namespace packages, Google-style docstrings on public
functions (click callbacks excepted — their docstring is the `--help`
text).

## Architecture

### Packaging

`lightcone.*` is a PEP 420 namespace: **`src/lightcone/` must never get
an `__init__.py`**. The engine is the host's `uv tool` — never a
dependency of the projects it runs, never inside a container image.
`[project.scripts]` re-declares the git-annex wheel's four entry points
verbatim (a test pins them against the wheel) so `uv tool install` puts
git-annex on PATH.

### CLI / engine split

`cli/` owns flags, rendering and exit codes only. `engine/` never imports
click and never prints; it raises `ProjectError`, which the CLI turns
into a clean error. Engine imports happen *inside* command callbacks so
`lc --help` stays cheap. Two modules are `python -m` entry points that
must stay import-light and must never become verbs or console scripts:
`engine/worker.py` (no click/rich) and `_sandbox_exec.py` (stdlib only,
zero lightcone imports).

Every external tool call (uv, git, git-annex, container runtimes) goes
through one seam, `project._run`, which the autouse `tools` fixture in
`tests/conftest.py` stubs. The `real_tools` fixture opts back out;
storage, worker and materialize tests use real tools on purpose.

### What a project is (`engine/project.py`, `templates/`)

A project is `pyproject.toml` + `uv.lock` + `.python-version` in a git +
git-annex repository; uv is the only environment substrate. `lc init` is
*convergence*, not scaffolding: every item goes through `_Converger`
(create if missing, append-only repair, never overwrite user files), and
`--check` is the same decision path with writes off. Templates are real
files using `string.Template`; `.gitignore`/`.gitattributes` converge
entry-wise, and new managed `.gitattributes` lines are always appended at
the *end* of the template.

Mode is derived, never configured: a `[tool.lightcone.image]` table means
containerized; `[project].license` turns on RO-Crate maintenance.

### Storage (`engine/dataset.py`)

Git carries pointers, manifests and history; git-annex carries bytes, via
`* filter=annex` so the user's plain `git add` does the right thing.
Manifests stay in git so a bytes-free clone can still classify
everything. Unfetched content *exists* as a pointer file or dangling
symlink — hashing must refuse both (`ContentNotFetchedError`). Only the
driver touches git; workers never do.

### Identity and invalidation (`identity.py`, `assets.py`)

- `definition_version` = recipe ‖ decisions ‖ format — what an output *is*.
- `env_version` = lock ‖ `.python-version` ‖ install settings ‖ image
  document — what it ran under. Deliberately **not** part of
  `definition_version`.
- `data_version` = the output file's sha256, computed in the worker.

`assets.classify` is the single rule, with three states: `stale` (the
artifact contradicts the project → remade), `behind` (only the
environment moved → reported, left alone unless `--refresh`), `current`.
`Verdict.calls_for_a_remake` is the only place a state becomes an action.
Check mode and the real run differ by one input value, never by logic.
A committed hand edit of a result is detected by git history
(`dataset.last_writer` vs. `materialize.datalad_run_subject`), not
rehashing, and classifies `stale`. Editing source code does not
invalidate outputs unless the file is a declared ASTRA input — by
decision; don't add heuristics.

### Execution (`plan.py` → `materialize.py` → `worker.py`)

`plan.build` validates with ASTRA, then maps resolved outputs onto a task
graph. One output is exactly one file,
`results/<universe>/<output_id>.<format>`, with a sidecar manifest
`.<output_id>.manifest.json`; ids carry no dots. `materialize` is the
driver: refuses a dirty tree, converges the environment, submits tasks to
a **borrowed** Dask cluster (dependency order comes from futures as
arguments), and commits each result serially with a datalad-compatible
run record whose `cmd` re-runs the engine-pinned worker. The worker never
raises — it returns `ok/current/behind/failed/blocked` — and the tree
ends as clean as it started (`save` on success, `restore` on failure).
`lc status` reports and always exits 0; `lc materialize --check` gates.
Read-only verbs must never traceback, fetch, or need a cluster.

### Sandbox (`engine/sandbox/`)

Every mechanism is a pure `Backend.wrap(policy, argv) -> argv`, so all
backends are testable on any host. `detect()` holds the only platform
branch. `Policy` (what to enforce), `Capability` (what the host can do)
and `Attestation` (what was actually enforced, recorded in the manifest
as `hermeticity`) stay distinct types. The project tree is read-only; a
recipe may write only its output's directory, a probe (`lc run`) only
`results/`. Execute rights are per-file — never grant EXECUTE on a
directory that could be a system prefix. Landlock unions rights while
SBPL is last-match-wins, so rule order matters on macOS only. Network is
uncontrolled on every mechanism and attested as such. Containerized mode
(`image.py` pure, `container.py` impure, `sandbox/oci.py`) uses the mount
table as the mechanism; the image is the system layer only, archived as a
docker-archive in the annex under `.datalad/environments/`.

### Compute (`engine/compute/`, `cli/compute.py`)

Compute is explicit: `lc compute resources|launch|status|down` manages
local and Slurm allocations through a `Provider`; `lc run` and
`lc materialize` take a cluster name/ID as first argument and never
create compute. No lc server, registry or custom Dask worker — native
jobs/processes are the authority, names are native labels. One Pydantic
model family (`compute/model.py`) is shared by catalog and providers.
Recipe resources (`execution_resources.py`) use stock Dask resource
admission. Slurm is only simulated in tests; the real Perlmutter
deployment test is still pending.

### `lc view` (`engine/serve.py`, `app/`, `ui/`)

A detached, idempotent, self-stopping Jupyter server (`LabServerApp`)
whose token can only *read*: no kernels, no writes, every route
authenticated, file reads confined to their root (`app/files.py`). The
page is `@astra-spec/ui` over the Contents API; the spec is resolved **in
the browser** by `@astra-spec/sdk` — Python never parses the spec for the
app. Server routes (`app/routes.py`) answer only what the Contents API
cannot (manifests, engine status, the paper cache). Server state lives in
`~/.lightcone/serve/<host>/`, never in the project.

## Where to change things

| To change… | Start in | Tests |
|---|---|---|
| A scaffolded file's content | `engine/templates/files/*.tmpl` | `test_templates.py` |
| What gets converged | `engine/project.py` | `test_project.py` |
| How bytes are stored | `engine/dataset.py`, `gitattributes.tmpl` | `test_dataset.py` (real annex) |
| When an output is remade | `engine/assets.py` | `test_assets.py` |
| How the spec becomes a graph | `engine/plan.py` (ask `astra.resolve`) | `test_plan.py` — lc's additions only, not spec semantics |
| How a recipe runs / what a run commits | `engine/worker.py` / `engine/materialize.py` | `test_worker.py`, `test_materialize.py` |
| What a sandboxed command may touch | `sandbox/policy.py` | `test_sandbox_policy.py`, `test_sandbox_enforcement.py` |
| A denial message | `sandbox/denial.py` — remedies must work today | `test_sandbox_denial.py` |
| Images | `engine/image.py`, `engine/container.py`, `sandbox/oci.py` | `test_image.py`, `test_container.py`, `test_container_smoke.py` |
| The RO-Crate | `engine/crate.py` (pure, clock-free, sorted) | `test_crate.py`, `test_crate_smoke.py` |
| Where a run executes | `engine/compute/` | `test_compute*.py` |
| What `lc view` shows | `ui/src/index.tsx` | `test_serve.py`, `test_view_browser.py` |
| A CLI verb | `cli/commands.py` — logic stays in the engine | `test_cli.py` (assert short fragments; rich wraps) |
