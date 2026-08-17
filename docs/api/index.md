# Python API

The `lightcone.*` namespace, module by module. Signatures live in the
source docstrings — this page is the map. (For the subsystem view, see
[Architecture](../architecture.md).)

## Top level

| Module | Responsibility |
|---|---|
| `lightcone.launcher` | the tool-env launcher: discover → mode-detect → scrub → converge → delegate (frozen interface: argv + `LC_DELEGATED=1`) |
| `lightcone._sandbox_exec` | the exec shim (`python -m lightcone._sandbox_exec`) — stdlib-only, applies Landlock/Seatbelt between fork and exec; exit 97 = setup failure |
| `lightcone.cli.commands` | the Click surface: `init`, `materialize`, `run`, `status`, `verify`, `build`, `export` |

## Engine — environment & identity

| Module | Responsibility |
|---|---|
| `engine.environment` | `Mode`, `EnvironmentSpec`, `load_environment()`, `compute_env_version()`, `scan_lock()` — the single parse point for the closed `[tool.lightcone]` surface |
| `engine.uv_env` | the closed ambient `UV_*` scrub list + the offline overlay |
| `engine.project` | `find_root()` — the `astra.yaml` walk-up |
| `engine.manifest` | `SCHEMA_VERSION`, `code_version()`, `sha256_dir()`, `write_manifest()`, `is_pre_migration()` |
| `engine.attestation` | `capture_runtime_attestation()` — worker-side platform/interpreter/uv/GPU capture |

## Engine — execution

| Module | Responsibility |
|---|---|
| `engine.snakefile` | `generate()` — astra.yaml → Snakefile + per-(rule, universe) `RuleJob` cfg; `render_recipe()` template substitution |
| `engine.job` | `RuleJob` — the typed generator→worker contract |
| `engine.runner` | `run_rule()` — the worker sequence (gates, env check, boundary exec, manifest) |
| `engine.boundary` | `ExecBoundary` protocol, `ExecScope`, `SandboxAttestation`, `get_boundary()` |
| `engine.dask_cluster` | `cluster_for_run()` — the run-scoped LocalCluster |
| `snakemake_executor_plugin_dask` | rules dispatched as dask tasks; SENTINEL-framed output |
| `engine.scratch` | scratch-root resolution, run dirs, the run lock |

## Engine — sandbox

| Module | Responsibility |
|---|---|
| `engine.sandbox.policy` | `build_policy()` — the §7 read/write/exec sets, HOME/XDG contract, `EXEC_ALLOWLIST_VERSION` |
| `engine.sandbox._landlock` | vendored ctypes bindings + `abi()` probe |
| `engine.sandbox.wrap` | `wrap_command()`/`wrap_argv()` — ruleset FD + shim argv assembly |
| `engine.sandbox.probe` | capability probe, hermeticity composition, `status_line()` |
| `engine.sandbox.seatbelt` | the generated SBPL profile (macOS) |
| `engine.sandbox.denial` / `hints` | the denial UX: re-stat, classify, two-remedy render, trailer |
| `engine.sandbox.exec_boundary` | `SandboxExecBoundary` — the enforced `ExecBoundary` |

## Engine — images

| Module | Responsibility |
|---|---|
| `engine.image.declaration` | `[tool.lightcone.image]` parsing + static refusals; `ImageDeclaration` |
| `engine.image.definition` / `render` | `ImageDefinition` → deterministic Containerfile text (fixed layering) |
| `engine.image.identity` | `compute_tag()` — `lc-env-<hash>` |
| `engine.image.builder` / `builder_podman` | `Builder` protocol; the three-file `BuildContext`; podman with pointed error mapping |
| `engine.image.record` | the build record + dpkg snapshot attestation |
| `engine.image.runtime_podman` / `mounts` | the digest-pinned full-stack run wrapper + the mount set |
| `engine.image.machine` | macOS `podman machine` preflight |
| `engine.image` (package) | `ensure_image()`, `resolve_pinned()`, `image_status()` |

## Engine — readers & export

| Module | Responsibility |
|---|---|
| `engine.status` | `get_output_status()`, `env_blast_radius()` — offline by invariant |
| `engine.verify` | `verify_outputs()` — tamper/chain checks + provenance notes |
| `engine.tree` | analysis-tree traversal over the resolved ASTRA spec |
| `engine.validation` | post-materialization output shape checks |
| `engine.wrroc` | Workflow Run RO-Crate export |
