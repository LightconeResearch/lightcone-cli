# AGENTS.md

This file provides guidance to coding agents working in this repository.

## What this is

**lightcone-cli** ships `lc`, the execution layer for ASTRA 0.1 (`astra.yaml` analysis specs). The split is enforced in code: `astra-tools` owns what a spec *means* — validation, universes and their selections, what each result reads, where it goes, and its command as it runs. `lc` asks for it with `astra.api.resolve` and never re-derives any of it. `lc` owns execution: scaffolding, environments, sandboxing, deciding what to remake from ASTRA's checker (`astra.api.status`), running recipes, committing results with ASTRA's record of each (`astra.api.write_records`). lc imports astra through `astra.api` alone; if something about spec meaning looks wrong, or `astra.api` lacks what lc needs, the fix is a PR to astra-tools, not a second implementation or a reach into its internals here.

User documentation lives in a separate repository (LightconeResearch/docs), not here.

## Commands

```bash
uv sync --group dev                 # pytest, ruff, mypy, datalad
uv run pytest                       # whole suite
uv run pytest tests/test_plan.py::test_name   # one test
uv run ruff check src/ tests/       # --fix to apply
uv run mypy src/                    # strict
uv run lc --help                    # the CLI from the checkout
uv build                            # wheel + sdist
```

There is deliberately no task runner. CI (`.github/workflows/{tests,lint}.yml`) runs exactly pytest, ruff and mypy — Linux on Python 3.11–3.13, plus macOS on 3.13.

Several suites skip when their prerequisite is absent, and CI turns each skip into a failure with an environment variable. Set the same variable locally to be sure a suite really ran:

| Variable | Suite | Needs |
|---|---|---|
| `LC_SANDBOX_TESTS_REQUIRED=1` | `test_sandbox_enforcement.py` | Landlock (Linux) or Seatbelt (macOS) |
| `LC_CONTAINER_TESTS_REQUIRED=1` | `test_container_smoke.py` | podman or docker |

The `uv-floor` CI job reruns the uv-touching suites against uv 0.7.0. That version must equal the `required-version` floor in `src/lightcone/engine/templates/files/pyproject.toml.tmpl`; change both together.

## Architecture

### Layout

- `src/lightcone/` is a **PEP 420 namespace package: it must never contain an `__init__.py`**. Sibling `lightcone-*` distributions share the namespace, and `python -m lightcone._sandbox_exec` relies on it to import nothing else.
- `cli/` holds flags, rendering and exit codes only. Logic lives in `engine/`, which raises `ProjectError`; the click group translates it.
- Module docstrings are the design record for each module. Read the docstring before changing a module.

### The model

A project is `pyproject.toml` + `uv.lock` + `.python-version` + `astra.yaml`, in a git repository with git-annex. uv is the only environment substrate. The current directory is the project root; nothing walks up.

- **Convergence** (`project.py`, `lc init`): every item is created if missing and repaired if wrong; rerunning is always safe. Scaffolded file content lives as real files in `engine/templates/files/*.tmpl`.
- **Graph** (`plan.py`): one task per result of the working tree, keyed by ASTRA's id for it (`fit#baseline`; `fit` for an output built once; `stage.fit#u1` for a result of an analysis the project drives; `stage:fit#v1` for one of an analysis it declares), in ASTRA's order: each after every result it reads. A result is exactly one file at the place ASTRA gives it, `<analysis>/results/<universe>/<name>.<format>`, with ASTRA's record of it at `<analysis>/.astra/records/<universe>/<name>.json`; the record is all lc writes beside a result. `build` asks `astra.api.resolve` and `astra.api.status` once each, refuses the pair where they name different commits, and keeps the checker's whole report beside the tasks (`Graph.checked`). A `Task` is ASTRA's own `Result`, its places included, with what a run needs looked up once: the folder its recipe runs in, the file each entry reads and what its recipe asks for. ASTRA renders the command and gives every path; lc composes neither.
- **Declared analyses** (`plan.py`, `commits.py`): an analysis the project declares by path is built like its own, each recipe in its own analysis's folder; one beside the project (`path: ../shared`) is in the same repository, and a run's dirty-tree check covers its folder (`Graph.beside`). A result from a commit is no task: it is read through its record at that commit. Whatever a recipe reads at another commit, a run gives it in that commit's tree: `.lightcone/commits/<commit>/` (`plan.COMMITS`) is a clone of the repository that holds the commit — the URL its analysis is declared with, or the project's own — checked out at it, and `astra.api.resolve(commits=...)` names each such file there. Refused: a recipe that would run on a commit's files (`NOT_AT_COMMIT`), and an analysis in a repository of its own.
- **Identity** (`identity.py`): `env_version` (raw `uv.lock` bytes, `.python-version`, a closed list of uv install settings, the image declaration) is the environment's identity, and a record names it as the IRI `urn:lightcone:env:<env_version>` (`identity.environment`), a function of the files `identity.files` names in the record's commit. What a result is *made from* (its declaration, the options selected, its inputs' content, the results it reads) is the specification's, compared by ASTRA's checker against the commit the record names. There is no definition hash here.
- **Classification** (`plan.classify`): one rule, shared by `lc status`, `materialize --check` and `materialize`. A result's state is ASTRA's checker's — `unchanged`; `stale`, where something it is made from changed since the commit its record names, or something it reads did; `missing`, with no valid record — plus lc's `behind`: unchanged, but made under another environment than the project's, reported and remade only with `--refresh`. `stale` and `missing` are remade, and so is a result whose bytes no clone can fetch (ASTRA's `ABSENT_RESULT`), whatever its state.
- **Execution** (`materialize.py` drives, `worker.py` makes one result): the driver refuses a dirty tree, hands the graph to Dask, and **owns git alone**, committing each result as it arrives, with its record in the same commit. Workers never raise and never write git; a failed task returns an outcome saying so. Each commit carries a DataLad run record that names `python -m lightcone.engine.worker <task>`, so `datalad rerun` reproduces it: datalad fetches the files of the dataset the record lists, and the worker gives the trees of other commits itself, from the analysis at that commit.
- **Storage** (`dataset.py`): the DataLad model through plain `git` and `git annex`. `.gitattributes` routes results and declared inputs to the annex with `filter=annex`; records and code stay in git. Whether a file holds its content or only a stand-in for it, and the hash a record names content by, are `content.py`'s.
- **Exec boundary** (`sandbox/`): `policy.py` builds mechanism-free path sets; a `Backend` is a pure `wrap(policy, argv) -> argv`; `boundary.py` picks the backend, runs it and returns an `Attestation` of what was actually enforced. The backends are Landlock (through the stdlib-only shim `_sandbox_exec.py`), Seatbelt (generated SBPL), and OCI (podman, podman-hpc, docker), where the mount table is the mechanism. The tree is read-only apart from the write scope. Network is deliberately not controlled. `lc run` puts a probe under the same boundary a recipe gets.
- **Containers** (`image.py` pure, `container.py` impure): declaring `[tool.lightcone.image]` in `pyproject.toml` switches the project to containerized mode. Nobody authors a Containerfile. The built image is saved as a docker-archive, annexed and committed in the repository; runtimes load it from there, and their local stores are only caches.
- **Compute** (`compute/`): allocation is explicit. `lc compute launch` starts a local or Slurm Dask cluster from an offer in the catalog, and `lc run` / `lc materialize` take its name or ID as their first argument. Providers own the native lifecycle; the engine borrows a standard Dask client.

### The runner's rules

The specification's table of rules has a runner layer, and `lc` is who answers for it. Each rule lc keeps is kept in one place, and `tests/test_runner_rules.py` has a test named after it.

| Rule | Where lc keeps it |
|---|---|
| `UNRECORDED_RESULT` | `worker.execute`: the recipe writes the result's default place, and `astra.api.write_records` writes a record at each of its places, committed with the bytes. |
| `FALSE_RECORD` | `worker.execute`: the result's hash is of the file the recipe wrote; each input's is taken before the recipe runs — what its upstream's task reported, the driver's digest of that very file (`RunContext.hashed`), or its own hash — and the input has to hold its content (`content.require_fetched`) and be the same file when the recipe ends (`content.stamp`); a result left alone whose bytes the run fetched is hashed before anything reads it, since a stand-in that gives no SHA-256 vouched for nothing (`materialize._not_as_recorded`); a result that reads as a stand-in is not recorded (`content.standin`); the commit is the one the run started from, and holds the analysis (`worker.require_in_commit`); the environment is checked either side of the recipe (`_gate`). The rerun entry point refuses a tree changed by more than the result's own files. |
| `WRONG_WORKING_DIRECTORY` | `worker.execute` runs the command in `Task.workdir`, its output's analysis's folder, even when the project drives it; a container is told so (`OCIBackend.workdir`), and the folder is readable to its recipe, beside the project too. |
| `REBUILT_FROM_COMMIT` | `plan.build`: a result from a commit is no task. What reads it reads it as a run gives it, its bytes hashed and compared with its record at that commit (`worker.from_commit`). |
| `NOT_AT_COMMIT` | Kept by refusal: `plan.build` refuses a project in which a recipe would run on a commit's files, the one result ASTRA gives no command once told where a run puts them. |
| `STALE_INPUT` | `worker.from_commit`: a result from a commit that ASTRA's checker does not find unchanged there, or whose bytes are not its record's, is read by nothing; what reads it is blocked, and `--check` says so. |
| `UNRECREATABLE_ENVIRONMENT` | The IRI is `urn:lightcone:env:<env_version>`, a function of files in the record's commit. `worker.require_in_commit`, asked by `materialize` and by the rerun entry point, refuses a run while one of `identity.files`, or the analysis, is not in git as a file of its own (`dataset.not_in_git`): an ignored file leaves a tree clean, a symlink is committed as the path it points to, and an annexed file as a pointer. |

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
- `image.py` is pure. Test its structure and ordering, not byte goldens; the one byte-level claim is that rendering twice gives identical output.

## Testing patterns

- Tests mirror `src/`. Pure suites (`test_identity`, `test_content`, `test_plan`, `test_image`, `test_sandbox_policy`, `test_sandbox_wrap`, `test_sandbox_denial`, `test_sandbox_oci`) spawn nothing and run on any OS.
- `test_dataset`, `test_worker` and `test_materialize` run real git, git-annex and uv on purpose. Whether bytes land in the annex and whether the tree ends clean are questions a stub cannot answer.
- `materialize.cluster_for_run` is the monkeypatch point for graph tests that should not launch a real cluster.
- `test_runner_rules.py` has one test per rule of the specification's runner layer lc keeps, named after its code, on a real project; `test_false_record` asks ASTRA's own checker (`astra.api.status`) whether what lc wrote holds.
- `test_plan.py` tests what lc adds (which results are tasks, what each task looks up, edges, the refusals), not what a spec means, and drives `classify()` with the statuses ASTRA's checker returns, built by hand: what is pinned is what lc makes of them. Every fixture spec must pass `astra validate`.
- Identity tests check sensitivity both ways: what must move the hash and what must not.
- `test_sandbox_enforcement.py` runs the real `exec_policy` against the real kernel mechanism, with one suite for both Landlock and Seatbelt. **Mutation-check every new denial test**: run the same command through `Unavailable()` and confirm it succeeds, otherwise the test proves nothing.
- `test_cli.py` covers the CLI surface only. Rich wraps at terminal width, so assert on short fragments.

## Invariants and traps

Each of these was learned from a real failure or a measurement, and most are not visible from the code at the point where you would break them. Lines marked *measured* were checked empirically; do not re-derive them from defaults.

### CLI and convergence

- `cli/commands.py` imports the engine inside command callbacks and builds the rich console lazily, so `lc --help` and completion stay cheap. `astra.api` loads the whole library, so the engine imports it inside the functions that use it, `project.converge` included.
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
- `materialize._require_storable` asks git about the very paths a run would commit, before anything runs: a clone where git-annex was never set up, a result an ignore rule covers, a result no line sends to the annex, and a record a line does send there are each refused by name — whether by `annex.largefiles` or by a `filter` other than git-annex's, such as the line `git lfs track` appends. The rules are files the researcher owns, and each of these fails silently otherwise. Both questions are asked NUL-separated, or git quotes a path holding an accent; `check-ignore` takes `-z` only with its paths on stdin (*measured*).
- An `annex.largefiles` expression can turn on the bytes (`largerthan=`), which do not exist before the run, so the preflight refuses only what the attributes settle, and `dataset.save` checks where git staged a result and its records (`git annex lookupkey` on the index) before it commits. Refused, nothing is committed and nothing is left staged.
- A `git add` through the annex filter leaves the location log in git-annex's journal; a clone made before a git-annex command commits it is told of no copy (*measured*). `dataset.save` commits it after each commit with `git annex sync --only-annex` and no remote, content or commit of its own. Never `git annex merge`: with no ref it merges a pending `synced/` branch into the branch checked out (*measured*), in the middle of a run.
- A refused save is restored like a failed task (`materialize._consume`): nothing committed, and the tree as the run found it.
- git-annex routes dotfiles to git regardless of `annex.largefiles`, so `dataset.save` passes `annex.dotfiles=true` per add. The image archive lives under a dot path and depends on it.
- Results are committed with `annex.thin=true` per add, never in repo config. Thin's hazard is an in-place write, which rewrites the annex object under its key (*measured*: the committed version becomes unrecoverable). lc never writes in place; researchers' tools on `data/` do. In tests, unlink a result before forging it, since identical results share one object.
- An unfetched annexed file still exists: as a small pointer file (unlocked) or a dangling symlink (locked), and Git LFS leaves a pointer file of its own. `content.standin` reads a pointer file exactly as ASTRA's checker does (a whole Git LFS pointer, a one-line annex path, at most 1024 bytes): read any other way, an input ASTRA calls content is refused, or a result is recorded that ASTRA then reads as a stand-in and plans again forever. `content.require_fetched` refuses all three with `ContentNotFetchedError` before a file is hashed; hashing one would give a well-formed digest of the wrong thing. ASTRA reads each as a stand-in, which gives the content's hash without the content, so read-only verbs classify a bytes-free clone correctly; content that is stood in for remakes nothing. Which file a run fetches is ASTRA's checker's answer (`Bytes`): what git-annex keeps is fetched, and content another tool keeps (Git LFS, DVC) is refused before anything runs, naming the command that fetches it.
- `dataset.save` commits with the same pathspec it stages, so work the user staged mid-run is never swept into a result's commit. Every git command waits out another's hold on `index.lock` (`dataset._run`): a plain `git add` of a large file holds the lock for as long as hashing it takes (*measured*). A lock that outlasts that wait is refused before work that ends in a commit starts, a run or `lc build` (`dataset.require_free_index`), since a killed git leaves it for good.
- `dataset.status` is scoped with `-- .` because a project may sit inside a larger repository. It counts a submodule that differs at all, whatever `.gitmodules` asks git to ignore, and untracked files whatever `status.showUntrackedFiles` says — passed as `-c`, the one form that also reaches the `git status` git runs inside a submodule. It reads git's NUL-separated form with renames off: git otherwise quotes a path holding a space or an accent, and no comparison with a real path then holds.
- `dataset.restore` is `git clean` plus `git checkout HEAD --` only for paths HEAD has. Never `git checkout HEAD -- .`.
- `require_committer` runs before a run, not at the first commit, so a missing git identity cannot waste a computed result.
- `pyproject.toml` re-declares the git-annex wheel's entry points so `uv tool install` puts `git-annex` on PATH. A test pins them against the wheel. Under `uvx` the researcher's own `git add` cannot find git-annex; that is why `required` is set.
- lc never imports datalad and never parses `.datalad/`. It writes there (dataset id, image archives, container config) and reads back only through `git config -f`. datalad is a dev dependency because the run record's format is datalad's: the suite parses it with datalad and runs a real `datalad rerun`, since a malformed record makes `rerun` skip and exit 0.

### Identity and classification

- `env_version` is length-framed (`_frame`). It hashes raw lock bytes rather than a parse, and a closed settings list (`_INSTALL_SETTINGS`) with every key hashed whether set or not. `uv.toml` replaces `[tool.uv]` rather than merging (*measured*), and settings are read the way uv reads them.
- Machine-level uv config (`~/.config/uv/uv.toml`, `/etc/uv/uv.toml`) is never hashed, only reported as an advisory: hashing it would make one commit classify differently on two hosts. No uv flag closes the gap without also dropping the project's own config (*measured*).
- The git commit is recorded and never hashed. **Editing analysis code remakes nothing unless the file is declared as an ASTRA input.** That is a decision; do not add a heuristic that scans recipes for paths.
- The engine version is attestation (the record's `runner`), never identity: an lc upgrade moves no result.
- The lock scan refuses path, directory and editable dependencies, because they record where code was, not what it was. Names compare in PEP 503 form.
- `stale` and `missing` mean the result contradicts the project; `behind` means only circumstances moved. `stale` wins when both apply. `behind` does not propagate downstream.
- `Verdict.calls_for_a_remake(refresh=)` is the only place a state becomes an action. Do not re-spell it at a call site.
- The driver and a task differ by one input value to `classify()`, never by logic: `read`, the bytes of each result this one reads, which only a task knows once its upstreams are made or kept. Without it the checker's state is taken as it is.
- ASTRA's checker says what a result breaks in itself (`Status.why`) and, separately, which entries read a result that is not unchanged or was made again with other bytes (`Status.stale_inputs`). A result stale only through those is unchanged again once they are made with the bytes its record gives: a task compares the bytes its upstreams came out with against those its record says were read, which is what lets a byte-identical rebuild stop a cascade. Only an error in `why` stales. `ABSENT_RESULT`, a warning, is bytes at the result's place that are neither the record's nor a stand-in for them: the state stays the checker's, and a run makes the result again, since no clone can fetch what the record names. Unfetched content stood in for is no diagnostic at all.
- A skip returns the recorded digest, never a recomputed one, so bytes-free clones classify correctly.
- `--refresh` widens a run by exactly one state. There is no flag that suppresses remaking a stale result.
- `lc status` always exits 0 and tolerates a dirty tree; `materialize --check` is the gate. `up_to_date` ignores `behind` and is false for a failed run.

### Execution

- ASTRA's checker, HEAD and the container runtime are read once per run by the driver and handed down. HEAD moves as the driver commits, so a per-task read would be nondeterministic. A record's `commit` is therefore the commit the run started at.
- A task asks ASTRA nothing. It applies `classify()` to the findings it was handed and never calls `astra.api.status` or git: compute nodes need neither, and a check per task would cost a full pass over the analysis each time. The one astra call in a task is `write_records`.
- A result's digest (`sha256:` hash and size) is computed in the worker before anything is staged, by hashing the file the recipe wrote. It must not come from the annex, where nothing is recorded yet. What a task read of another task is the digest that task reported, for the very file it was taken of (`TaskOutcome.stamp`, as `RunContext.hashed` does for inputs); any other file in its place is hashed as the recipe reads it, so a record never names bytes its recipe did not read.
- A declared input more than one task of a run reads is hashed once, by the driver: each task would otherwise read all of it to name it in its record, and a multiverse reads one catalog in every universe. The digest travels with the stamp of the file it was computed from (`content.stamp`: inode, size, time), and a task takes it only for that very file; for any other it hashes what it reads.
- A dependent can start while its upstream is still being annexed. That is safe (*measured*: git-annex hard-links, then renames). Do not move the save into the task.
- A result with two names is one file: the recipe writes its first place, the worker copies the bytes to the others, and `write_records` writes a record at each; a run stores and commits every place.
- The worker resets only what the result owns (`Task.owns`: its file and its records), never the directory, since results share it and run concurrently. A file a recipe writes beside its result is no output's: it is neither committed nor removed, and the run warns that it was left. A result declared again in another format is a new artifact at a new place; the earlier file is only no longer declared, and stays committed. A payload that is not a regular file — a symlink, a folder, nothing — fails the task.
- `materialize` converges the environment once something is to run, before any recipe starts. `uv run --locked --no-sync` does not notice a `.venv` that drifted from the lock (*measured*).
- The run fetches what its recipes read itself with `git annex get`, where ASTRA's checker finds the bytes absent and kept by git-annex: every file a task that may run reads, bar a result certain to be remade — a result that may run only because something it reads may is left alone when that comes out as recorded, and its readers then read its bytes. A run that makes nothing fetches nothing, and connects to no cluster. Read-only verbs never transfer anything. Nobody is ever told to run an annex command by hand. A fetched result is hashed before anything reads it; one whose bytes are not its record's is reported failed and its readers blocked, and with its bytes in the clone, the next check plans it.
- A file at another commit is given in that commit's tree, never fetched into the project's (`commits.give`). For the project's own commit the content is fetched into the project first, from wherever the project fetches its own, and the commit's clone takes it with `get --from origin`: the clone's git-annex branch was copied before the project held it, so a plain `get` finds no copy (*measured*). git-annex initializes itself in that clone at its first fetch, which commits, so the clone carries the project's committer identity (*measured*). The run record lists only the files of the dataset, which datalad can fetch; the worker gives the rest.
- An analysis in another folder keeps its results in its own `results/`, which the project's anchored `results/**` line does not reach: the storage check refuses such a result and says to copy lightcone's `.gitattributes` into that folder. Nothing annexes a folder merely named `results`.
- A run leaves the tree as clean as it found it: `made` saves, `failed` or `blocked` restores. Interrupted tasks are the exception; their files stay because a recipe may still be writing on the cluster. A task removes its record before its recipe runs, so the dirty-tree refusal counts `.astra/` as lc's, like `results/`: its remedy is to discard, never to commit.
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
- A recipe's write scope is the directory its result lands in, not the file: a path that does not exist yet is dropped from the policy, and a file grant cannot create the file. A probe gets `results/` whole.
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
- The image identity document feeds `env_version`; the tag also hashes the rendered Containerfile. A generator change therefore rebuilds the image without putting results behind.

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

## Decided against

Do not reintroduce these without a new reason.

- **A launcher, or the engine as a project dependency.** The `lc` that was invoked is the engine. Project locks carry only what the analysis imports.
- **`lc verify` and `lc export`.** A full rehash is O(bytes) and blind on unfetched content, while ASTRA's checker answers from records and stand-ins on a bytes-free clone.
- **Bare-recipe run records.** `datalad rerun` executes the recorded string literally, so a recipe would replay unconfined and write no record.
- **bubblewrap.** Its advantage is rights subtraction, which the policy never needs. Reconsider only if a policy shape needs subtraction or `bwrap` becomes universal.
- **Controlling the network.** Every mechanism attests `network: allowed`. `denied` stays in the type for a mechanism that really denies.
- **An exclusion-based read policy.** A missing allowlist entry fails loudly; an exclusion list fails silently.
- **Pinning the annex filter or hooks to the engine's executable.** It writes durable state pointing into a prunable `uvx` cache.
- **Asking ASTRA's checker from a task.** It needs git and the whole repository on every compute node, and pays a full check per task downstream of a rebuild. The driver asks once; a task applies lc's rule to what it was handed.
- **A second file beside the record.** The record is all lc writes beside a result. Its schema is closed, so what it cannot hold (what the sandbox enforced, the image id, the uv version) is not kept, by decision; a slot for runner attestation is a change to the specification.
- **A definition hash.** What a result is made from is the specification's to say and its checker's to compare.
- **Asking git who last wrote a result.** ASTRA's checker compares the bytes at a result's place with its record; all history added was a record forged together with its bytes, for a `git log` per result on every status.
- **Launching Slurm workers through `uv run --with`.** It re-resolves Dask away from the driver's and needs an index on compute nodes.
- **Catalog `connections`, `lc compute --config`, and a separate local settings block.** Offers name their provider, and local offers are ordinary offers.
- **Migrations.** ASTRA 0.0.x specs and their `universes/*.yaml` are not read, and results from older engine layouts are invisible; the remedy is `git rm -r results/` and a re-materialize.
- **Scaffolding an `AGENTS.md` into projects.** A test pins its absence.

## Contributing

PRs need a DCO sign-off, posted as a PR comment after approval (`.github/workflows/dco.yml`). The agentic eval (`.github/workflows/eval.yml`, seeds in `evals/`) runs on dispatch and on every non-draft PR.
