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

## Contributing

PRs need a DCO sign-off, posted as a PR comment after approval (`.github/workflows/dco.yml`). The agentic eval (`.github/workflows/eval.yml`, seeds in `evals/`) runs on dispatch and on every non-draft PR.
