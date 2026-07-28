# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] — 2026-07-25

Run a lightcone project on a JupyterHub deployment with nothing to
configure, and scaffold a report alongside the analysis.

### Added

- **`kubernetes` container runtime.** On a Dask Gateway deployment the
  worker pod *is* the container, so recipe wrapping is a passthrough and
  Containerfile specs resolve to registry refs
  (`<registry>/lc-<name>:<hash>`) instead of local-store tags. Never
  auto-detected from `PATH` — selected by site detection or pinned in
  `~/.lightcone/config.yaml`.
- **GCP Cloud Build backend** (`lightcone/engine/cloudbuild.py`) for
  hosts with no local OCI runtime. The staged build context is uploaded
  to a deployment bucket and the image is pushed to the deployment's
  Artifact Registry. Auth is the pod's Workload Identity via the GCE
  metadata server — no stored credentials, no SDK dependency.
  Environment contract: `LIGHTCONE_REGISTRY`, `LIGHTCONE_BUILD_BUCKET`,
  and the optional `LIGHTCONE_BUILD_SERVICE_ACCOUNT`. Freshness is a
  single registry `HEAD` on the content-addressed ref, so unchanged
  files never rebuild and never even upload.
- **Run-scoped Dask Gateway clusters.** A new `cluster_for_run` branch,
  activated by `DASK_GATEWAY__ADDRESS`, creates a cluster with the
  project's image, scales it adaptively from 1 to `--jobs`, waits for
  the first worker (bounded by `LIGHTCONE_GATEWAY_WORKER_TIMEOUT`,
  default 600s), asserts that workers advertise the `cpus`+`memory`
  resource contract, and culls the cluster on exit. Create-per-run is
  what makes image updates seamless: a Gateway cluster's image is fixed
  at creation.
- **`jupyterhub` site** in the site registry, detected from the
  `DASK_GATEWAY__ADDRESS` env marker; declares the `kubernetes`
  container runtime and `$HOME` as the scratch root.
- **Pre-flight image build in `lc run`.** The image pass that `lc build`
  performs now also runs at the start of `lc run`, so the first
  invocation after editing a Containerfile no longer fails mid-DAG on a
  missing image.
- **`lc build --runtime kubernetes`** to select the Cloud Build path
  explicitly.
- **`worker_image` manifest field** — the image the executing worker pod
  reported, recorded alongside the `container_image` the spec declared.
  Optional and additive; `null` on every other backend.
- **MyST report scaffold in `lc init`** — `myst.yml` (MySTRA plugin,
  book theme) and a TODO-driven `index.md` using the `{astra}` role and
  directive plus an `outputs` embed, referencing the boilerplate
  `example_method` / `main_result` ids. `_build/` was added to the
  scaffolded `.gitignore`, the project `CLAUDE.md` template gained a
  `## Report` section, and `lc init`'s next-steps now suggest
  `myst start` (requires the MyST CLI, `npm i -g mystmd`).

### Changed

- **Image digests now cover every `COPY` / `ADD` source** referenced
  from the Containerfile — files hashed directly, directories walked
  recursively (with `.git`, `.venv`, `results`, `.lightcone`,
  `node_modules`, and the usual caches excluded). *Behavior change:*
  projects whose Containerfile copies source code will rebuild once on
  upgrade, and will subsequently rerun downstream outputs when that
  copied code changes — which previously went undetected.
- `lc run` **rejects a spec resolving to more than one container image**
  on the Gateway backend, where a single worker-pod image serves the
  whole run. Other backends wrap per rule and are unaffected.
- `cluster_for_run` now yields an **env overlay dict** rather than a
  scheduler address string (the Gateway branch passes a cluster name,
  not a dialable address) and takes new `worker_image` and `max_workers`
  keyword arguments.
- `dask-gateway` is now a **regular dependency**, not an extra, so
  `lc run` works out of the box on a hub and scaffolded project images
  inherit it through their `lightcone-cli` pin.
- The `snakemake` invocation passes `--shared-fs-usage persistence
  input-output sources storage-local-copies source-cache`
  unconditionally — omitting `software-deployment` keeps the driver's
  `sys.executable` out of spawned job commands, which would not exist
  inside a worker image.

## [0.3.7] — 2026-06-30

### Changed

- Removed the ASTRA `narrative` field from the shipped skills; bumped
  the `astra-tools` floor to 0.2.10.
- Refreshed the README badges.

## [0.3.6] — 2026-05-14

### Added

- PyPI trove classifiers.

## [0.3.5] — 2026-05-14

### Added

- The **paper-reproduction skill bundle** (`/lc-from-paper` and its
  supporting skills).
- Documentation site: landing page, lightcone styling, GitHub Pages
  deployment (automatic and manual).

### Changed

- `lc init` uses `uv` for venv creation, falling back to `python -m venv`.
- Upgraded init terminal output; broad cleanup of the CLI and user guides.
- Dropped the credentials requirement now that ASTRA is public.

### Fixed

- `curl` missing from the python-slim base image.
- CI: dedicated lint workflow, mypy errors, no expensive runs on draft PRs.

## [0.3.4] — 2026-04-30

### Changed

- Streamlined the Claude Code project hooks.

## [0.3.3] — 2026-04-30

### Fixed

- Install `lightcone-cli` (not just `astra-tools`) into the project venv.

## [0.3.2] — 2026-04-30

### Fixed

- Install `astra-tools` into the project venv on `lc init`.

## [0.3.1] — 2026-04-30

### Added

- `lc run` refuses to execute on a Perlmutter login node.

### Changed

- Trimmed and realigned the Claude Code skills, hooks, and reference docs.

## [0.3.0] — 2026-04-30

Version bump; content shipped in 0.2.1.

## [0.2.1] — 2026-04-30

### Added

- **Snakemake-based execution layer** with content-addressed manifests
  on a Dask substrate — the architecture the CLI still rests on.
- Post-materialization result-file validation.
- Intent-based targets with dynamic SLURM discovery.

### Fixed

- Gracefully retire Dask workers so `srun` exits silently; drive worker
  log level by env var and silence INFO logs unless `--verbose`.
- Thread analysis-level inputs into manifest `input_versions`.
- Prefer `podman-hpc` over `podman` in runtime auto-detection.
- Insert `--` before snakemake targets so `--rerun-triggers` stops
  swallowing them.

## [0.2.0] — 2026-04-21

### Changed

- **Renamed Prism to lightcone-cli**: the executable is now `lc` and the
  code lives in the `lightcone.*` namespace.
- Initial contributor documentation.

### Fixed

- Crash with dagster 1.13 (removed `get_all_asset_specs`).

## [0.1.3] — 2026-04-14

### Changed

- Published to PyPI as `lightcone-prism`.
- Simplified the container spec to a single string instead of a build dict.
- Relicensed from Apache 2.0 to BSD 3-Clause.
- Consolidated the guides into `astra-reference` and `prism-reference`.

## [0.1.2] — 2026-03-28

### Added

- Sub-analysis redesign.
- Eval harness for quantitative build-loop evaluation.
- Podman support and a venv fallback backend.

### Changed

- Recipe output is streamed in real time.

## [0.1.1] — 2026-03-19

### Added

- `prism update` with project sync and a startup update check.
- `--existing-project` init mode.

### Changed

- Recommended permissions flipped from an allowlist to a denylist.
- Internals moved into a `.prism/` directory.
- `prism-build` rewritten on the ralph stop hook, without plan mode.

## [0.1.0] — 2026-03-03

Initial release.

[Unreleased]: https://github.com/LightconeResearch/lightcone-cli/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/LightconeResearch/lightcone-cli/compare/v0.3.7...v0.4.0
[0.3.7]: https://github.com/LightconeResearch/lightcone-cli/compare/v0.3.6...v0.3.7
[0.3.6]: https://github.com/LightconeResearch/lightcone-cli/compare/v0.3.5...v0.3.6
[0.3.5]: https://github.com/LightconeResearch/lightcone-cli/compare/v0.3.4...v0.3.5
[0.3.4]: https://github.com/LightconeResearch/lightcone-cli/compare/v0.3.3...v0.3.4
[0.3.3]: https://github.com/LightconeResearch/lightcone-cli/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/LightconeResearch/lightcone-cli/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/LightconeResearch/lightcone-cli/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/LightconeResearch/lightcone-cli/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/LightconeResearch/lightcone-cli/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/LightconeResearch/lightcone-cli/compare/v0.1.3...v0.2.0
[0.1.3]: https://github.com/LightconeResearch/lightcone-cli/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/LightconeResearch/lightcone-cli/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/LightconeResearch/lightcone-cli/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/LightconeResearch/lightcone-cli/releases/tag/v0.1.0
