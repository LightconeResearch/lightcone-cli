# AGENTS.md

This file provides guidance to coding agents working in this repository.

## What this is

**lightcone-cli** ships `lc`, the execution layer for ASTRA 0.1 (`astra.yaml` analysis specs). The split is enforced in code: `astra-tools` owns what a spec *means* — validation, universes and their selections, what each result reads, where it goes, and its command as it runs. `lc` asks for it with `astra.api.resolve` and never re-derives any of it. `lc` owns execution: scaffolding, environments, sandboxing, deciding what to remake from ASTRA's checker (`astra.api.status`), running recipes, committing results with ASTRA's record of each (`astra.api.write_records`). lc imports astra through `astra.api` alone; if something about spec meaning looks wrong, or `astra.api` lacks what lc needs, the fix is a PR to astra-tools, not a second implementation or a reach into its internals here.

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

A project is `pyproject.toml` + `uv.lock` + `.python-version` + `astra.yaml`, in a git repository with git-annex. uv is the only environment substrate. The current directory is the project root; nothing walks up.

- **Convergence** (`project.py`, `lc init`): every item is created if missing and repaired if wrong; rerunning is always safe. Scaffolded file content lives as real files in `engine/templates/files/*.tmpl`.
- **Graph** (`plan.py`): one task per result the analysis plans, keyed by ASTRA's id for it (`fit#baseline`; `fit` for an output built once), in ASTRA's order: each after every result it reads. A result is exactly one file at the place ASTRA gives it, `results/<universe>/<name>.<format>`, with ASTRA's record of it at `.astra/records/<universe>/<name>.json`; the record is all lc writes beside a result. `build` asks `astra.api.resolve` and `astra.api.status` once each, refuses the pair where they name different commits, and keeps what the checker found of each task beside it (`Graph.found`). A `Task` is ASTRA's own `Result` with what a run needs looked up once: the file each entry reads, the options it is made with, what its recipe asks for. ASTRA renders the command and gives every path; lc composes neither. lc builds a single analysis: one that declares another is refused.
- **Identity** (`identity.py`): `env_version` (raw `uv.lock` bytes, `.python-version`, a closed list of uv install settings, the image declaration) is the environment's identity, and a record names it as the IRI `urn:lightcone:env:<env_version>` (`identity.environment`), a function of the files `identity.files` names in the record's commit. What a result is *made from* (its declaration, the options selected, its inputs' content, the results it reads) is the specification's, compared by ASTRA's checker against the commit the record names. There is no definition hash here.
- **Classification** (`assets.py`): one `classify()` yields `current`, `behind` or `stale`, shared by `lc status`, `materialize --check`, `materialize` and the viewer. `stale` is ASTRA's finding (no valid record, something the result is made from changed, or bytes at its place that are not the record's) or a foreign write, and is remade. A changed environment is only `behind`: reported, and remade only with `--refresh`. A result made from a stale one is reported stale until that one is made again.
- **Execution** (`materialize.py` drives, `worker.py` makes one output): the driver refuses a dirty tree, hands the graph to Dask, and **owns git alone**, committing each output as its result arrives, with its record in the same commit. Workers never raise and never write git; a failed task returns a result saying so. Each commit carries a DataLad run record that names `python -m lightcone.engine.worker <task>`, so `datalad rerun` reproduces it.
- **Storage** (`dataset.py`): the DataLad model through plain `git` and `git annex`. `.gitattributes` routes results and declared inputs to the annex with `filter=annex`; records and code stay in git. A write to a result that lc did not make is detected from git history, not by rehashing.
- **Exec boundary** (`sandbox/`): `policy.py` builds mechanism-free path sets; a `Backend` is a pure `wrap(policy, argv) -> argv`; `boundary.py` picks the backend, runs it and returns an `Attestation` of what was actually enforced. The backends are Landlock (through the stdlib-only shim `_sandbox_exec.py`), Seatbelt (generated SBPL), and OCI (podman, podman-hpc, docker), where the mount table is the mechanism. The tree is read-only apart from the write scope. Network is deliberately not controlled. `lc run` puts a probe under the same boundary a recipe gets.
- **Containers** (`image.py` pure, `container.py` impure): declaring `[tool.lightcone.image]` in `pyproject.toml` switches the project to containerized mode. Nobody authors a Containerfile. The built image is saved as a docker-archive, annexed and committed in the repository; runtimes load it from there, and their local stores are only caches.
- **Compute** (`compute/`): allocation is explicit. `lc compute launch` starts a local or Slurm Dask cluster from an offer in the catalog, and `lc run` / `lc materialize` take its name or ID as their first argument. Providers own the native lifecycle; the engine borrows a standard Dask client.
- **Publication** (`crate.py`): `ro-crate-metadata.json` is a pure function of repository state (sorted iteration, no clock), converged by `materialize` like `uv.lock`. There is no export verb.
- **Viewer** (`serve.py`, `app/`, `ui/`): one detached, read-only Jupyter server per project. The page resolves the spec in the browser with the released `@astra-spec/sdk` and `@astra-spec/ui`; output state comes from the engine's `classify()` and each run from its record, through `app/routes.py`.

### The runner's rules

The specification's table of rules has a runner layer, and `lc` is who answers for it. Each rule lc keeps is kept in one place, and `tests/test_runner_rules.py` has a test named after it.

| Rule | Where lc keeps it |
|---|---|
| `UNRECORDED_RESULT` | `worker.execute`: the recipe writes the result's default place, and `astra.api.write_records` writes a record at each of its places, committed with the bytes. |
| `FALSE_RECORD` | `worker.execute`: the result's hash is of the file the recipe wrote; each input's is taken before the recipe runs — what its upstream's task reported, the driver's digest of that very file (`RunContext.hashed`), or its own hash — and the input has to hold its content (`assets.require_fetched`) and be the same file when the recipe ends (`assets.stamp`); the commit is the one the run started from; the environment is checked either side of the recipe (`_gate`). The rerun entry point refuses a tree changed by more than the result's own files. |
| `UNRECREATABLE_ENVIRONMENT` | The IRI is `urn:lightcone:env:<env_version>`, a function of files in the record's commit. `worker.require_recreatable`, asked by `materialize` and by the rerun entry point, refuses a run while one of `identity.files` is not tracked: an ignored file leaves a tree clean. |

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
- `test_runner_rules.py` has one test per rule of the specification's runner layer lc keeps, named after its code, on a real project; `test_false_record` asks ASTRA's own checker (`astra.api.status`) whether what lc wrote holds.
- `test_assets.py` drives `classify()` with the statuses ASTRA's checker returns, built by hand; what is pinned is what lc makes of them.
- `test_plan.py` tests what lc adds (which results are tasks, what each task looks up, edges, the refusals), not what a spec means. Every fixture spec must pass `astra validate`.
- Identity tests check sensitivity both ways: what must move the hash and what must not.
- `test_sandbox_enforcement.py` runs the real `exec_policy` against the real kernel mechanism, with one suite for both Landlock and Seatbelt. **Mutation-check every new denial test**: run the same command through `Unavailable()` and confirm it succeeds, otherwise the test proves nothing.
- `test_cli.py` covers the CLI surface only. Rich wraps at terminal width, so assert on short fragments.

## Invariants and traps

Each of these was learned from a real failure or a measurement, and most are not visible from the code at the point where you would break them. Lines marked *measured* were checked empirically; do not re-derive them from defaults.

### CLI and convergence

- `cli/commands.py` imports the engine inside command callbacks and builds the rich console lazily, so `lc --help` and completion stay cheap. `astra.api` loads the whole library, so the engine imports it inside the functions that use it, `project.converge` included; only the viewer's server, which the CLI never imports, has it at module scope.
- `converge(write=False)` is check mode: the same decision path with side effects off. Everything routes through `_Converger.item` / `.file` / `.blocked`. A *warning* never affects `converged`; a *blocked* item does.
- Convergence only appends to files the user owns. `.gitignore` and `.gitattributes` converge entry by entry, never by a marker. **A new managed `.gitattributes` line goes at the end of the template**, because the file is last-match-wins and the disorder check judges against template order.
- Only what git can carry is converged (no empty directories), so a fresh clone needs nothing but `.venv` and `git annex init`.
- `uv.lock` and `.venv` converge by correctness, using uv's own `--check` probes. Those probes are why the scaffold sets the uv floor: an older uv rejects the flag and convergence would silently re-lock forever.
- Every uv call carries an explicit `--project`. `--system-site-packages` must never be used. `project.child_env` scrubs ambient `UV_*` outside the `_UV_KEPT` allowlist, because those variables change what a lock installs without moving `env_version`.
- There is one project uv hop, `project.uv_prefix`, always `--no-sync`: the driver converges before submitting, and a per-task sync would have workers racing on `.venv`.
- Don't add a flag whose only user is a test; stub `project._run` instead.

### Storage

- `filter.annex.required=true` is set by convergence. Without it, on Linux a `git add` whose annex filter cannot start exits 0 and stages the raw bytes into git history (*measured*). lc writes no absolute paths, filter drivers or hooks into a repository.
- `* annex.largefiles=nothing` must come first in `.gitattributes`; without it `filter=annex` annexes code too. Records are exempted back into git so a clone with no content can still classify, and the `.astra/**` line comes last, so no line above it reaches a record.
- `materialize._require_storable` asks git about the very paths a run would commit, before anything runs: a result an ignore rule covers, a result no line sends to the annex, and a record a line does send there are each refused by name — whether by `annex.largefiles` or by a `filter` other than git-annex's, such as the line `git lfs track` appends. The rules are files the researcher owns, and each of these fails silently otherwise. Both questions are asked NUL-separated, or git quotes a path holding an accent; `check-ignore` takes `-z` only with its paths on stdin (*measured*).
- git-annex routes dotfiles to git regardless of `annex.largefiles`, so `dataset.save` passes `annex.dotfiles=true` per add. The image archive lives under a dot path and depends on it.
- Results are committed with `annex.thin=true` per add, never in repo config. Thin's hazard is an in-place write, which rewrites the annex object under its key (*measured*: the committed version becomes unrecoverable). lc never writes in place; researchers' tools on `data/` do. In tests, unlink a result before forging it, since identical outputs share one object.
- An unfetched annexed file still exists: as a small pointer file (unlocked) or a dangling symlink (locked), and Git LFS leaves a pointer file of its own. `assets.require_fetched` refuses all three with `ContentNotFetchedError` before a file is hashed or read by a recipe; hashing one would give a well-formed digest of the wrong thing. ASTRA reads each as a stand-in, which gives the content's hash without the content, so read-only verbs classify a bytes-free clone correctly; content that is stood in for remakes nothing. Git LFS content is the one thing lc does not fetch, and its refusal names `git lfs pull`.
- `dataset.save` commits with the same pathspec it stages, so work the user staged mid-run is never swept into an output commit. Every git command waits out another's hold on `index.lock` (`dataset._run`): a plain `git add` of a large file holds the lock for as long as hashing it takes (*measured*). A lock that outlasts that wait is refused before work that ends in a commit starts, a run or `lc build` (`dataset.require_free_index`), since a killed git leaves it for good.
- `dataset.status` is scoped with `-- .` because a project may sit inside a larger repository. It counts a submodule that differs at all, whatever `.gitmodules` asks git to ignore, and untracked files whatever `status.showUntrackedFiles` says — passed as `-c`, the one form that also reaches the `git status` git runs inside a submodule. It reads git's NUL-separated form with renames off: git otherwise quotes a path holding a space or an accent, and no comparison with a real path then holds.
- `dataset.restore` is `git clean` plus `git checkout HEAD --` only for paths HEAD has. Never `git checkout HEAD -- .`.
- `require_committer` runs before a run, not at the first commit, so a missing git identity cannot waste a computed result.
- `pyproject.toml` re-declares the git-annex wheel's entry points so `uv tool install` puts `git-annex` on PATH. A test pins them against the wheel. Under `uvx` the researcher's own `git add` cannot find git-annex; that is why `required` is set.
- lc never imports datalad and never parses `.datalad/`. It writes there (dataset id, image archives, container config) and reads back only through `git config -f`. datalad is a dev dependency because the run record's format is datalad's: the suite parses it with datalad and runs a real `datalad rerun`, since a malformed record makes `rerun` skip and exit 0.

### Identity and classification

- `env_version` is length-framed (`_frame`). It hashes raw lock bytes rather than a parse, and a closed settings list (`_INSTALL_SETTINGS`) with every key hashed whether set or not. `uv.toml` replaces `[tool.uv]` rather than merging (*measured*), and settings are read the way uv reads them.
- Machine-level uv config (`~/.config/uv/uv.toml`, `/etc/uv/uv.toml`) is never hashed, only reported as an advisory: hashing it would make one commit classify differently on two hosts. No uv flag closes the gap without also dropping the project's own config (*measured*).
- The git commit is recorded and never hashed. **Editing analysis code remakes nothing unless the file is declared as an ASTRA input.** That is a decision; do not add a heuristic that scans recipes for paths.
- The engine version is attestation (the record's `runner`), never identity: an lc upgrade moves no output.
- The lock scan refuses path, directory and editable dependencies, because they record where code was, not what it was. Names compare in PEP 503 form.
- `stale` means the artifact contradicts the project; `behind` means only circumstances moved. `stale` wins when both apply. `behind` does not propagate downstream.
- `Verdict.calls_for_a_remake(refresh=)` is the only place a state becomes an action. Do not re-spell it at a call site.
- The driver and a task differ by one input value to `classify()`, never by logic: `read`, the bytes of each result this one reads. A task gives what its upstreams actually produced; the driver gives what their records say, or `None` while one of them may still be remade, in which case only the task can decide. A foreign write enters the same way, as a fact computed by whoever has git.
- ASTRA's checker says what a result breaks in itself (`Status.why`) and, separately, which results it reads were not unchanged when it looked (`Status.stale_inputs`). lc reads the first and not the second: by the time `classify()` is asked, a run may have remade those results, so it compares the bytes they have now with the bytes the record says were read. This is what lets a byte-identical rebuild stop a cascade. What stales from `why` is an error, or the warning `ABSENT_RESULT`: bytes at the result's place that are neither the record's nor a stand-in for them, so what is there is not what the record describes. Unfetched content stood in for is no diagnostic at all.
- A skip returns the recorded digest, never a recomputed one, so bytes-free clones classify correctly.
- `--refresh` widens a run by exactly one state. There is no flag that suppresses remaking a stale output.
- `lc status` always exits 0 and tolerates a dirty tree; `materialize --check` is the gate. `up_to_date` ignores `behind` and is false for a failed run.
- A result's name is its output's id, which ASTRA's grammar keeps free of dots, while a format may hold one (`tar.gz`): `assets.record_path` finds a result file's record by splitting its name on the first dot.

### Execution

- ASTRA's checker, HEAD, the container runtime and each result's last writer are read once per run by the driver and handed down. HEAD moves as the driver commits, so a per-task read would be nondeterministic. A record's `commit` is therefore the commit the run started at.
- A task asks ASTRA nothing. It applies `classify()` to the findings it was handed and never calls `astra.api.status` or git: compute nodes need neither, and a check per task would cost a full pass over the analysis each time. The one astra call in a task is `write_records`.
- A result's digest (`sha256:` hash and size) is computed in the worker before anything is staged, by hashing the file the recipe wrote. It must not come from the annex, where nothing is recorded yet. What a task read of another task is the digest that task reported, never a rehash.
- A declared input more than one task of a run reads is hashed once, by the driver: each task would otherwise read all of it to name it in its record, and a multiverse reads one catalog in every universe. The digest travels with the stamp of the file it was computed from (`assets.stamp`: inode, size, time), and a task takes it only for that very file; for any other it hashes what it reads.
- A dependent can start while its upstream is still being annexed. That is safe (*measured*: git-annex hard-links, then renames). Do not move the save into the task.
- The worker resets only what the result owns (`Task.owns`: `<name>.*` and the record), never the directory, since results share it and run concurrently. A payload that is not a regular file — a symlink, a folder, nothing — fails the task.
- `materialize` converges the environment once something is to run, before any recipe starts. `uv run --locked --no-sync` does not notice a `.venv` that drifted from the lock (*measured*).
- The run fetches what its recipes read itself with `git annex get`: every file a task that may run reads, bar a result certain to be remade — a result that may run only because something it reads may is left alone when that comes out as recorded, and its readers then read its bytes. A run that makes nothing fetches nothing, and connects to no cluster. Read-only verbs never transfer anything. Nobody is ever told to run an annex command by hand.
- A run leaves the tree as clean as it found it: `ok` saves, `failed` or `blocked` restores. Interrupted tasks are the exception; their files stay because a recipe may still be writing on the cluster. A task removes its record before its recipe runs, so the dirty-tree refusal counts `.astra/` as lc's, like `results/`: its remedy is to discard, never to commit.
- The run record pins the engine with `uv run --no-project --with <requirement>`: a release by version, a dev build by source commit (`materialize._engine_requirement`). The worker entry point syncs the project environment itself. Recorded paths are declared, never resolved, or they would name annex objects.
- Concurrency is the allocation's. There is no `--jobs`.
- Recipes run through `bash -c`.

### Sandbox

- **Never grant EXECUTE on a directory that could be a system prefix.** Landlock unions rights over ancestors, so one grant on `/usr` silently defeats the per-file allowlist while every test passes. The interpreter's file is always executable; its install root only when it is the interpreter's own tree.
- Three non-obvious grants, each found by a failure: the realpath'd ELF loader in the exec set, read on the interpreter's install root (the stdlib), and read on `/dev/urandom`.
- A derived read root may never be `$HOME` or above (`_stdlib_root`).
- Tool paths resolve through `policy.utility()` only. A hardcoded `/usr/bin/env` is a denial on the first exec of every run on hosts that keep it elsewhere.
- The env overlay is applied inside the wrap by the seam (`boundary.env_argv`), never merged into the subprocess environment: the `uv run` hop outside the boundary must keep the real `XDG_*`, or uv loses its cache.
- The overlay sets `PYTHONDONTWRITEBYTECODE`, never `PYTHONPYCACHEPREFIX`: with a cache prefix Python reads bytecode only from there, so the environment's own, compiled when it was synced, is ignored and every run compiles everything it imports (*measured*: three times the import time of a scientific stack).
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
- An image's identity is its id, the sha256 of the archive's config blob. Execution pins the id the runtime's own store gives it, never a tag: the image id under podman and Docker's classic store, a manifest's digest under Docker's containerd store, a fresh Docker 29's default (*measured*). That digest is the one the archive's OCI index names when `docker save` wrote it; podman saves no index, and Docker then makes the manifest as it loads, so its digest is read right after the load off the tag the archive carries — podman's spelling, `localhost/<tag>:latest`, which the bare tag does not reach (*measured*) — and such an archive is loaded again by every run. A dropped archive is never substituted; a rebuild is a new archive under a new id.
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
- Compute formats (IDs, records, catalog, JSON) are unversioned because allocations are ephemeral.
- The catalog is selected only through `LC_COMPUTE_CONFIG`, so every verb sees the same one.
- One local allocation per user per machine, enforced by scanning the process table. `flock` is not used because NERSC home filesystems do not support it. Local compute is blocked on recognized NERSC login nodes.
- Local clusters end when idle through Dask's own `idle_timeout`. `SCHEDULER_CONFIG` pins scheduler settings so ambient Dask config never applies.
- Ended local allocations are retired (`ended.json`), never deleted: a missing record cannot be told from a changed `connection_root`.
- Slurm workers are stock Dask Nannies, one per rank, with `memory_limit=0` because Dask does not account for subprocess memory. Thread-count variables default to 1.
- Recipe resources are cooperative Dask reservations, not OS limits. A recipe `time_limit` is refused. A GPU recipe takes the worker's whole GPU mask. GPU visibility comes from the allocation's `CUDA_VISIBLE_DEVICES`; nothing probes hardware.
- At NERSC, `UV_CACHE_DIR` must be off `$HOME`, since compute nodes cannot lock files there. The user exports it before `lc compute launch`.

### Crate and viewer

- A `[project].license` turns crate maintenance on. Nothing invents a license.
- `datePublished` is the newest record's `finished`, overriding rocrate's default of construction time. It keeps the clock out of the render and is what lets `lc status` see a lagging crate.
- A published `sha256` is always a raw digest that `sha256sum` can verify. Out-of-tree inputs publish none. `dataset.annex_keys` uses `git annex find --include=*`, because a bare `find` lists only files present locally.
- The crate's `Person` is the author of the result's saving commit, not of the commit its record names.
- The crate is rendered from the records ASTRA's checker read, so the graph is read again after a run that made something. It describes as made exactly the results a run would leave alone (`materialize._published`), and `lc status` dates the crate by the same ones; any other is a step of the workflow only. It is rendered only from a tree that is its commit, since it is read off the working tree. Nothing is read from the clone's own configuration, such as its `origin` URL: two clones of one commit render one document.
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
- **Bare-recipe run records.** `datalad rerun` executes the recorded string literally, so a recipe would replay unconfined and write no record.
- **bubblewrap.** Its advantage is rights subtraction, which the policy never needs. Reconsider only if a policy shape needs subtraction or `bwrap` becomes universal.
- **Controlling the network.** Every mechanism attests `network: allowed`. `denied` stays in the type for a mechanism that really denies.
- **An exclusion-based read policy.** A missing allowlist entry fails loudly; an exclusion list fails silently.
- **Pinning the annex filter or hooks to the engine's executable.** It writes durable state pointing into a prunable `uvx` cache.
- **Asking ASTRA's checker from a task.** It needs git and the whole repository on every compute node, and pays a full check per task downstream of a rebuild. The driver asks once; a task applies lc's rule to what it was handed.
- **A second file beside the record.** The record is all lc writes beside a result. Its schema is closed, so what it cannot hold (what the sandbox enforced, the image id, the uv version) is not kept, by decision; a slot for runner attestation is a change to the specification.
- **A definition hash.** What a result is made from is the specification's to say and its checker's to compare.
- **Launching Slurm workers through `uv run --with`.** It re-resolves Dask away from the driver's and needs an index on compute nodes.
- **Catalog `connections`, `lc compute --config`, and a separate local settings block.** Offers name their provider, and local offers are ordinary offers.
- **Migrations.** ASTRA 0.0.x specs and their `universes/*.yaml` are not read, and outputs from older engine layouts are invisible; the remedy is `git rm -r results/` and a re-materialize.
- **Scaffolding an `AGENTS.md` into projects.** A test pins its absence.
- **Records as dock tabs or a JupyterLab shell in the viewer.** Both were built and dropped to stay on released astra-ui.

## Contributing

PRs need a DCO sign-off, posted as a PR comment after approval (`.github/workflows/dco.yml`). The agentic eval (`.github/workflows/eval.yml`, seeds in `evals/`) runs on dispatch and on every non-draft PR.
