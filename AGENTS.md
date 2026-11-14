# AGENTS.md

This file provides guidance to coding agents working in this repository.

## What this is

**lightcone-cli** ships `lc`, the execution layer for ASTRA (`astra.yaml` analysis specs). The split is enforced in code: `astra-tools` (pinned exactly in `pyproject.toml`) owns what a spec *means* — validation, scoping, `from:` references, `when:`, universe resolution, the recipe placeholder grammar. `lc` owns execution: scaffolding, environments, sandboxing, running recipes, committing results with provenance. If something about spec meaning looks wrong, the fix is a PR to astra-tools, not a second implementation here.

User documentation lives in a separate repository (LightconeResearch/docs), not here.

## Commands

```bash
uv sync --group dev                 # pytest, ruff, mypy, datalad, playwright, roc-validator
uv run pytest                       # whole suite
uv run pytest tests/test_plan.py::test_name   # one test
uv run ruff check src/ tests/       # --fix to apply
uv run mypy src/                    # strict
uv run lc --help                    # the CLI from the checkout
cd ui && npm ci && npm run build    # the `lc view` front end → src/lightcone/app/static/ (gitignored)
uv build                            # wheel + sdist; hatch_build.py runs the npm build
```

There is deliberately no task runner. CI (`.github/workflows/{tests,lint}.yml`) runs exactly pytest, ruff and mypy — Linux on Python 3.11–3.13, plus macOS on 3.13.

Python work never needs Node: editable installs skip the front-end build. Only `lc view` needs the bundle, and it refuses to start without one.

Several suites skip when their prerequisite is absent, and CI turns each skip into a failure with an environment variable. Set the same variable locally to be sure a suite really ran:

| Variable | Suite | Needs |
|---|---|---|
| `LC_SANDBOX_TESTS_REQUIRED=1` | `test_sandbox_enforcement.py` | Landlock (Linux) or Seatbelt (macOS) |
| `LC_CONTAINER_TESTS_REQUIRED=1` | `test_container_smoke.py` | podman or docker |
| `LC_CRATE_TESTS_REQUIRED=1` | `test_crate_smoke.py` | `rocrate-validator` (in the dev group) |
| `LC_UI_TESTS_REQUIRED=1` | `test_frontend_build.py`, `test_view_browser.py`, part of `test_serve.py` | built bundle; `python -m playwright install chromium` |

The `uv-floor` CI job reruns the uv-touching suites against uv 0.7.0. That version must equal the `required-version` floor in `src/lightcone/engine/templates/files/pyproject.toml.tmpl`; change both together.

## Architecture

### Layout

- `src/lightcone/` is a **PEP 420 namespace package: it must never contain an `__init__.py`**. Sibling `lightcone-*` distributions share the namespace, and `python -m lightcone._sandbox_exec` relies on it to import nothing else.
- `cli/` holds flags, rendering and exit codes only. Logic lives in `engine/`, which raises `ProjectError`; the click group translates it.
- `engine/` is the engine; `app/` is the Jupyter server behind `lc view`, run as `python -m lightcone.app` and never imported by the CLI; `ui/` is its TypeScript page.
- Module docstrings are the design record for each module. Read the docstring before changing a module.

### The model

A project is `pyproject.toml` + `uv.lock` + `.python-version` + `astra.yaml` + `universes/*.yaml`, in a git repository with git-annex. uv is the only environment substrate. The current directory is the project root; nothing walks up.

- **Convergence** (`project.py`, `lc init`): every item is created if missing and repaired if wrong; rerunning is always safe. Scaffolded file content lives as real files in `engine/templates/files/*.tmpl`.
- **Graph** (`plan.py`): one task per `(universe, output)` with a recipe. An output is exactly one file, `results/<universe>/<output_id>.<format>`, with the manifest sidecar `.<output_id>.manifest.json` beside it. The path is composed from the spec, never chosen by the recipe. `build` validates through astra before resolving anything.
- **Identity** (`identity.py`): two hashes, kept separate on purpose. `definition_version` (rendered recipe, decisions, format) is the rebuild trigger. `env_version` (raw `uv.lock` bytes, `.python-version`, a closed list of uv install settings, the image declaration) is not part of it.
- **Classification** (`assets.py`): one `classify()` yields `current`, `behind` or `stale`, shared by `lc status`, `materialize --check`, `materialize` and the viewer. A changed definition or changed input content is `stale` and is remade. A changed environment is only `behind`: reported, and remade only with `--refresh`. The rule compares content hashes, never mtimes.
- **Execution** (`materialize.py` drives, `worker.py` makes one output): the driver refuses a dirty tree, hands the graph to Dask, and **owns git alone**, committing each output as its result arrives. Workers never raise and never write git; a failed task returns a result saying so. Each commit carries a DataLad run record that names `python -m lightcone.engine.worker <universe>/<output>`, so `datalad rerun` reproduces it.
- **Storage** (`dataset.py`): the DataLad model through plain `git` and `git annex`. `.gitattributes` routes results and declared inputs to the annex with `filter=annex`; manifests and code stay in git. A write to an output that lc did not make is detected from git history, not by rehashing.
- **Exec boundary** (`sandbox/`): `policy.py` builds mechanism-free path sets; a `Backend` is a pure `wrap(policy, argv) -> argv`; `boundary.py` picks the backend, runs it and returns an `Attestation` of what was actually enforced. The backends are Landlock (through the stdlib-only shim `_sandbox_exec.py`), Seatbelt (generated SBPL), and OCI (podman, podman-hpc, docker), where the mount table is the mechanism. The tree is read-only apart from the write scope. Network is deliberately not controlled. `lc run` puts a probe under the same boundary a recipe gets.
- **Containers** (`image.py` pure, `container.py` impure): declaring `[tool.lightcone.image]` in `pyproject.toml` switches the project to containerized mode. Nobody authors a Containerfile. The built image is saved as a docker-archive, annexed and committed in the repository; runtimes load it from there, and their local stores are only caches.
- **Compute** (`compute/`): allocation is explicit. `lc compute launch` starts a local or Slurm Dask cluster from an offer in the catalog, and `lc run` / `lc materialize` take its name or ID as their first argument. Providers own the native lifecycle; the engine borrows a standard Dask client.
- **Publication** (`crate.py`): `ro-crate-metadata.json` is a pure function of repository state (sorted iteration, no clock), converged by `materialize` like `uv.lock`. There is no export verb.
- **Viewer** (`serve.py`, `app/`, `ui/`): one detached, read-only Jupyter server per project. The page resolves the spec in the browser with the released `@astra-spec/sdk` and `@astra-spec/ui`; output state comes from the engine's `classify()` through `app/routes.py`.

### The subprocess seam

Every external tool (uv, git, git-annex, container runtimes) is invoked through `engine.project._run`. Route new commands through it. `tests/conftest.py` stubs it with the autouse `tools` fixture, which emulates each tool's observable effect and records every argv. The `real_tools` fixture puts the real one back, and the `analysis` fixture builds a real project on top of it.

## Project rules

These came out of review and apply to every change.

- **No backward-compatibility code.** No migrations, version guards or aliases for older behavior or formats.
- **No foreshadowing and no dead code.** Code, comments, flags and messages describe only what exists now. `lc --help` lists only verbs that work.
- **No escape hatches around guarantees.** Enforcement ships without a flag that turns it off.
- **Nothing waits on a human.** Agents run these verbs: no interactive prompts, no interactive shells.
- **Literal behavior over invented convenience.** Erroring beats guessing; anything ambiguous is a `ProjectError`.
- **Streamline.** No small helpers or rendering layers where a few inline lines read fine.
- **Never reference design documents or section numbers in code or comments.** Comments and docstrings carry *why*, not the history of how the code came to be.
- **Attribute adapted third-party material** as "inspired from" or "adapted from", with its license. The Seatbelt profiles in `sandbox/profiles/` are kept near-verbatim from upstream so they can be diffed.
- **Leave working files alone** when a nearby change merely makes them look touchable.
- **A dependency lands with the code that needs it.**

## Conventions

- Ruff (E, F, I, N, W, UP), line length 100, target py311. mypy strict with `namespace_packages` and `explicit_package_bases`.
- Google-style docstrings on public functions. Click command callbacks are the exception: their docstring is the `--help` text and stays prose.
- `worker.py` and `_sandbox_exec.py` are on the `python -m` path of every task. Keep `worker.py` free of click and rich; keep `_sandbox_exec.py` stdlib-only with no lightcone imports.
- `image.py` and `crate.py` are pure. Test their structure and ordering, not byte goldens; the one byte-level claim is that rendering twice gives identical output.

## Testing patterns

- Tests mirror `src/`. Pure suites (`test_identity`, `test_assets`, `test_plan`, `test_image`, `test_crate`, `test_sandbox_policy`, `test_sandbox_wrap`, `test_sandbox_denial`, `test_sandbox_oci`) spawn nothing and run on any OS.
- `test_dataset`, `test_worker` and `test_materialize` run real git, git-annex and uv on purpose. Whether bytes land in the annex and whether the tree ends clean are questions a stub cannot answer.
- `materialize.cluster_for_run` is the monkeypatch point for graph tests that should not launch a real cluster.
- `test_plan.py` tests what lc adds (paths, edges, `definition_version`, the validation gate), not what a spec means. Every fixture spec must pass `astra validate`.
- Identity tests check sensitivity both ways: what must move the hash and what must not.
- `test_sandbox_enforcement.py` runs the real `exec_policy` against the real kernel mechanism, with one suite for both Landlock and Seatbelt. **Mutation-check every new denial test**: run the same command through `Unavailable()` and confirm it succeeds, otherwise the test proves nothing.
- `test_cli.py` covers the CLI surface only. Rich wraps at terminal width, so assert on short fragments.

## Invariants and traps

Each of these was learned from a real failure or a measurement, and most are not visible from the code at the point where you would break them. Lines marked *measured* were checked empirically; do not re-derive them from defaults.

### CLI and convergence

- `cli/commands.py` imports the engine inside command callbacks and builds the rich console lazily, so `lc --help` and completion stay cheap. The one module-scope astra import is `astra.scaffold` in `project.py`, which is stdlib-only; `astra.validation` and `astra.resolve` must stay lazy.
- `converge(write=False)` is check mode: the same decision path with side effects off. Everything routes through `_Converger.item` / `.file` / `.blocked`. A *warning* never affects `converged`; a *blocked* item does.
- Convergence only appends to files the user owns. `.gitignore` and `.gitattributes` converge entry by entry, never by a marker. **A new managed `.gitattributes` line goes at the end of the template**, because the file is last-match-wins and the disorder check judges against template order.
- Only what git can carry is converged (no empty directories), so a fresh clone needs nothing but `.venv` and `git annex init`.
- `uv.lock` and `.venv` converge by correctness, using uv's own `--check` probes. Those probes are why the scaffold sets the uv floor: an older uv rejects the flag and convergence would silently re-lock forever.
- Every uv call carries an explicit `--project`. `--system-site-packages` must never be used. `project.child_env` scrubs ambient `UV_*` outside the `_UV_KEPT` allowlist, because those variables change what a lock installs without moving `env_version`.
- There is one project uv hop, `project.uv_prefix`, always `--no-sync`: the driver converges before submitting, and a per-task sync would have workers racing on `.venv`.
- Don't add a flag whose only user is a test; stub `project._run` instead.

### Storage

- `filter.annex.required=true` is set by convergence. Without it, on Linux a `git add` whose annex filter cannot start exits 0 and stages the raw bytes into git history (*measured*). lc writes no absolute paths, filter drivers or hooks into a repository.
- `* annex.largefiles=nothing` must come first in `.gitattributes`; without it `filter=annex` annexes code too. Manifests are exempted back into git so a clone with no content can still classify.
- git-annex routes dotfiles to git regardless of `annex.largefiles`, so `dataset.save` passes `annex.dotfiles=true` per add. The image archive lives under a dot path and depends on it.
- Results are committed with `annex.thin=true` per add, never in repo config. Thin's hazard is an in-place write, which rewrites the annex object under its key (*measured*: the committed version becomes unrecoverable). lc never writes in place; researchers' tools on `data/` do. In tests, unlink a result before forging it, since identical outputs share one object.
- An unfetched annexed file still exists: as a small pointer file (unlocked) or a dangling symlink (locked). `assets.data_version` refuses both with `ContentNotFetchedError`; hashing either would give a well-formed digest of the wrong thing. Read-only verbs report "not in this clone", which is not "changed".
- `dataset.save` commits with the same pathspec it stages, so work the user staged mid-run is never swept into an output commit. `dataset.status` is scoped with `-- .` because a project may sit inside a larger repository.
- `dataset.restore` is `git clean` plus `git checkout HEAD --` only for paths HEAD has. Never `git checkout HEAD -- .`.
- `require_committer` runs before a run, not at the first commit, so a missing git identity cannot waste a computed result.
- `pyproject.toml` re-declares the git-annex wheel's entry points so `uv tool install` puts `git-annex` on PATH. A test pins them against the wheel. Under `uvx` the researcher's own `git add` cannot find git-annex; that is why `required` is set.
- lc never imports datalad and never parses `.datalad/`. It writes there (dataset id, image archives, container config) and reads back only through `git config -f`. datalad is a dev dependency because the run record's format is datalad's: the suite parses it with datalad and runs a real `datalad rerun`, since a malformed record makes `rerun` skip and exit 0.

### Identity and classification

- Both hashes are length-framed (`_frame`). `env_version` hashes raw lock bytes rather than a parse, and a closed settings list (`_INSTALL_SETTINGS`) with every key hashed whether set or not. `uv.toml` replaces `[tool.uv]` rather than merging (*measured*), and settings are read the way uv reads them.
- Machine-level uv config (`~/.config/uv/uv.toml`, `/etc/uv/uv.toml`) is never hashed, only reported as an advisory: hashing it would make one commit classify differently on two hosts. No uv flag closes the gap without also dropping the project's own config (*measured*).
- The git commit is recorded and never hashed. **Editing analysis code remakes nothing unless the file is declared as an ASTRA input.** That is a decision; do not add a heuristic that scans recipes for paths.
- The engine version is attestation (`lc_version`, `uv_version` in the manifest), never identity: an lc upgrade moves no output.
- The lock scan refuses path, directory and editable dependencies, because they record where code was, not what it was. Names compare in PEP 503 form.
- `stale` means the artifact contradicts the project; `behind` means only circumstances moved. `stale` wins when both apply. `behind` does not propagate downstream.
- `Verdict.calls_for_a_remake(refresh=)` is the only place a state becomes an action. Do not re-spell it at a call site.
- Check mode and execution differ by one input value to `classify()` (`None` for "will change"), never by logic. A foreign write enters the same way, as a fact computed by whoever has git.
- A skip returns the recorded digest, never a recomputed one, so bytes-free clones classify correctly.
- `--refresh` widens a run by exactly one state. There is no flag that suppresses remaking a stale output.
- `lc status` always exits 0 and tolerates a dirty tree; `materialize --check` is the gate. `up_to_date` ignores `behind` and is false for a failed run.
- Output and universe ids must be single path components and output ids carry no dot: the sidecar is recovered by splitting the filename on the first dot. Two universe files with the same id are refused.

### Execution

- HEAD, the uv version, the container runtime and each output's last writer are read once per run by the driver and handed down. HEAD moves as the driver commits, so a per-task read would be nondeterministic. A manifest's `git_sha` is therefore the commit the run started at.
- `data_version` is computed in the worker before anything is staged, by hashing files. It must not come from the annex, where nothing is recorded yet.
- A dependent can start while its upstream is still being annexed. That is safe (*measured*: git-annex hard-links, then renames). Do not move the save into the task.
- The worker resets only what the output's id names (`<id>.*` and the sidecar), never the directory, since outputs share it and run concurrently. A payload that is not a regular file fails the task.
- `materialize` converges the environment right after the dirty check and before the graph. `uv run --locked --no-sync` does not notice a `.venv` that drifted from the lock (*measured*).
- The run fetches declared in-tree inputs itself with `git annex get`. Read-only verbs never transfer anything. Nobody is ever told to run an annex command by hand.
- A run leaves the tree as clean as it found it: `ok` saves, `failed` or `blocked` restores. Interrupted tasks are the exception; their files stay because a recipe may still be writing on the cluster.
- The run record pins the engine with `uv run --no-project --with <requirement>`: a release by version, a dev build by source commit (`materialize._engine_requirement`). The worker entry point syncs the project environment itself. Recorded paths are declared, never resolved, or they would name annex objects.
- Concurrency is the allocation's. There is no `--jobs`.
- Recipes run through `bash -c`.

### Sandbox

- **Never grant EXECUTE on a directory that could be a system prefix.** Landlock unions rights over ancestors, so one grant on `/usr` silently defeats the per-file allowlist while every test passes. The interpreter's file is always executable; its install root only when it is the interpreter's own tree.
- Three non-obvious grants, each found by a failure: the realpath'd ELF loader in the exec set, read on the interpreter's install root (the stdlib), and read on `/dev/urandom`.
- A derived read root may never be `$HOME` or above (`_stdlib_root`).
- Tool paths resolve through `policy.utility()` only. A hardcoded `/usr/bin/env` is a denial on the first exec of every run on hosts that keep it elsewhere.
- The env overlay is applied inside the wrap by the seam (`boundary.env_argv`), never merged into the subprocess environment: the `uv run` hop outside the boundary must keep the real `XDG_*`, or uv loses its cache.
- `Unavailable` is a real backend that attests `fs: open`. There are no sandbox flags.
- `denial.trailer()` fires on every nonzero sandboxed exit, because `denial.explain()` may find nothing. Remedies must be things that work today.
- The shim exits 97 on any setup failure and never runs the command unsandboxed. Its policy arrives as JSON on argv, which keeps `wrap` pure.
- SBPL is last-match-wins while Landlock unions. `generate_profile` restates the write tier after the read-only guard; in the wrong order, recipes work on Linux and fail on macOS.
- The mechanisms disagree about removing the write root itself: Landlock denies it, Seatbelt allows it. A test asserting either answer fails on the other OS.
- A recipe's write scope is the directory its output lands in, not the file: a path that does not exist yet is dropped from the policy, and a file grant cannot create the file. A probe gets `results/` whole.
- The write scope is a directory nested in a read-only tree because that is the only shape Landlock, SBPL and bind mounts all express. A writable tree with a read-only hole needs subtraction, which Landlock lacks.
- `/dev`, `/proc`, `/sys` and `/run` are granted generously on purpose. The test for any grant is whether undeclared *inputs* could arrive through it; a grant whose absence produces an unattributable error should be made freely.
- On macOS, not controlling the network needs the mach services in `network.sbpl`, not only the socket families. The vendored profiles carry one marked local delta (no blanket `process-exec`), pinned by a test.
- Enforcement fixtures must not live under `/tmp`, which is in the write baseline. A write-denial test must target a path the OS itself would allow, such as a declared input.

### Containers

- The engine never enters the image. Only the environment converge (`container.sync`, into `.lightcone/venv`) and each recipe or probe exec run in it; the driver, git, annex and Dask stay on the host. The host `.venv` is unused in containerized mode.
- The image is the system layer only: base, apt packages, a pinned uv and interpreter. No project file enters the build context, so a code edit can never trigger a build.
- Execution pins the image id (sha256 of the archive's config blob), never a tag. A dropped archive is never substituted; a rebuild is a new archive under a new id.
- `runtime_for_run(root, *, build)` has two strictnesses. `lc build` and the materialize preflight may build, save and commit. The probe and the worker only find, fetch and load.
- Builds and the archive commit need a clean tree, and happen after the graph resolves, so a spec typo never costs a build.
- The OCI wrap uses `--read-only` (otherwise a stray write succeeds into the ephemeral layer and vanishes), `--security-opt label=disable` (SELinux), and mounts with a resolved source and the declared destination. The container environment is an allowlist.
- `contains_prefix`: a host mechanism keeps the `uv run` hop outside the wrap, while the container backend takes it inside and applies env with `--env`.
- podman-hpc, podman and docker are spellings inside one `OCIBackend`, detected in that order. `_PODMAN_FAMILY` is asked positively so a new runtime does not inherit podman behavior. `podman-hpc migrate` runs outside the load branch.
- `_require_arch` refuses a wrong-architecture archive before loading: the load would succeed and fail later as `exec format error`.
- The archive format is docker-archive because every runtime consumes it, including a future apptainer backend.
- The image identity document feeds `env_version`; the tag also hashes the rendered Containerfile. A generator change therefore rebuilds the image without putting outputs behind.

### Compute

- `run` and `materialize` never create compute. `materialize --check` and `status` never connect to any.
- Workers run the driver's own interpreter (`sys.executable`), so client, scheduler and workers are one installation on a shared filesystem. Workers need neither git nor git-annex.
- Cluster names are native labels found by fresh discovery, not a registry. Slurm jobs are `JobName=lc-<name>` with a token in `Comment`; owner, name and token are all verified before attaching or cancelling. A user's own job named `lc-…` blocks the name loudly and is never adopted.
- Compute formats (IDs, records, catalog, JSON) are unversioned because allocations are ephemeral. Manifests are committed and keep `schema_version`.
- The catalog is selected only through `LC_COMPUTE_CONFIG`, so every verb sees the same one.
- One local allocation per user per machine, enforced by scanning the process table. `flock` is not used because NERSC home filesystems do not support it. Local compute is blocked on recognized NERSC login nodes.
- Local clusters end when idle through Dask's own `idle_timeout`. `SCHEDULER_CONFIG` pins scheduler settings so ambient Dask config never applies.
- Ended local allocations are retired (`ended.json`), never deleted: a missing record cannot be told from a changed `connection_root`.
- Slurm workers are stock Dask Nannies, one per rank, with `memory_limit=0` because Dask does not account for subprocess memory. Thread-count variables default to 1.
- Recipe resources are cooperative Dask reservations, not OS limits. A recipe `time_limit` is refused. A GPU recipe takes the worker's whole GPU mask. GPU visibility comes from the allocation's `CUDA_VISIBLE_DEVICES`; nothing probes hardware.
- At NERSC, `UV_CACHE_DIR` must be off `$HOME`, since compute nodes cannot lock files there. The user exports it before `lc compute launch`.

### Crate and viewer

- A `[project].license` turns crate maintenance on. Nothing invents a license.
- `datePublished` is the newest manifest `finished_at`, overriding rocrate's default of construction time. It keeps the clock out of the render and is what lets `lc status` see a lagging crate.
- A published `sha256` is always a raw digest that `sha256sum` can verify. Out-of-tree inputs publish none. `dataset.annex_keys` uses `git annex find --include=*`, because a bare `find` lists only files present locally.
- The crate's `Person` is the author of the output's saving commit, not of the manifest's `git_sha`.
- A foreign write is detected when the commit that last touched an output does not carry its run-record subject. `materialize.datalad_run_subject` is the one spelling of that subject.
- The `lc view` token can only read: the server keeps the Contents API and `/files/`, the authorizer refuses every write, and three writes Jupyter makes outside its authorizer are switched off in `app/__init__.py`. Every route requires the token (`allow_unauthenticated_access=False` plus `@web.authenticated`).
- Each server run has a fresh token and signing secret, so an old page cannot authenticate to a later server.
- Serve state lives in `~/.lightcone/serve/<host>/`, mode 0700, never in the project: it holds the token, and login nodes share `$HOME`.
- `lc view` returns immediately, is idempotent per project, never opens a browser, and the server stops itself after 30 idle minutes.
- Python never resolves the spec for the page. The browser does, with the released astra packages only.
- File reads are confined beneath their root (`app/files.py`) with `O_NOFOLLOW` at each step. Links into `.git/annex/objects` are allowed; external symlinks are refused.
- The bundle URL is versioned by a hash of the bundle's bytes, because Jupyter serves static files as immutable. `jupyter-live-content` is pinned below 0.2 because the page speaks its unpublished protocol.

## Decided against

Do not reintroduce these without a new reason.

- **A launcher, or the engine as a project dependency.** The `lc` that was invoked is the engine. Project locks carry only what the analysis imports.
- **`lc verify` and `lc export`.** A full rehash is O(bytes) and blind on unfetched outputs, while history answers on a bytes-free clone. The repository already is the crate.
- **Bare-recipe run records.** `datalad rerun` executes the recorded string literally, so a recipe would replay unconfined and write no manifest.
- **bubblewrap.** Its advantage is rights subtraction, which the policy never needs. Reconsider only if a policy shape needs subtraction or `bwrap` becomes universal.
- **Controlling the network.** Every mechanism attests `network: allowed`. `denied` stays in the type for a mechanism that really denies.
- **An exclusion-based read policy.** A missing allowlist entry fails loudly; an exclusion list fails silently.
- **Pinning the annex filter or hooks to the engine's executable.** It writes durable state pointing into a prunable `uvx` cache.
- **Launching Slurm workers through `uv run --with`.** It re-resolves Dask away from the driver's and needs an index on compute nodes.
- **Catalog `connections`, `lc compute --config`, and a separate local settings block.** Offers name their provider, and local offers are ordinary offers.
- **Migrations.** Outputs from older engine layouts are invisible; the remedy is `git rm -r results/` and a re-materialize.
- **Scaffolding an `AGENTS.md` into projects.** A test pins its absence.
- **Acting on ASTRA's `container:` key.** It is ignored entirely.
- **Records as dock tabs or a JupyterLab shell in the viewer.** Both were built and dropped to stay on released astra-ui.

## Contributing

PRs need a DCO sign-off, posted as a PR comment after approval (`.github/workflows/dco.yml`). The agentic eval (`.github/workflows/eval.yml`, seeds in `evals/`) runs on dispatch and on every non-draft PR.
