# Architecture

lightcone-cli is a thin shim over Snakemake plus three layers it owns
substantively: the **environment model** (uv as the only substrate,
identity, the container hatch), the **integrity layer** (per-output
content-addressed manifests), and the **hermeticity layer** (the
sandbox). Everything else — DAG resolution, staleness, parallelism,
retry, locking — is Snakemake's.

```text
        lc <verb>  (uv tool shim — the launcher)
             │  discover → mode-detect → UV_* scrub → converge → delegate
             ▼
   <project>/.venv/bin/lc          direct mode: the engine from the
             │                     project's own lock (LC_DELEGATED=1)
             │      — or —
   podman run … /opt/venv/bin/lc   containerized: the whole stack inside
             │                     the digest-pinned image
             ▼
astra.yaml ── snakefile.generate() ──► .lightcone/Snakefile + snakefile-config.json
             │
      snakemake --executor dask   (run-scoped LocalCluster)
             │
        run_rule()  — the worker sequence:
          1. pre-gate      env_version(tree) == job's baked env_version
          2. env check     uv sync --check   /  image identity assert
          3. boundary exec sandbox (Landlock/Seatbelt) + offline overlay
          4. post-gate     then write_manifest()
             │
   results/<u>/<o>/…  +  .lightcone-manifest.json
```

## The layers

### Environment (`engine/environment.py`, `engine/uv_env.py`, `launcher.py`)

A project is `pyproject.toml` + `uv.lock` + `.python-version`. Mode is
derived: the presence of `[tool.lightcone.image]` (or
`Containerfile.extra`) *is* the escalation into containerized mode.
`env_version` — one length-framed hash over the lock, the interpreter
pin, the closed install-settings list, and the system-layer declaration
— is the environment identity; it sits inside every output's
`code_version`, so environment edits stale exactly what they can
affect. The launcher (`lightcone/launcher.py`) owns the two-hop
delegation: tool env → project-locked engine, with the frozen interface
(argv passthrough + `LC_DELEGATED=1`).

### Integrity (`engine/manifest.py`, `engine/status.py`, `engine/verify.py`)

`code_version = sha256({recipe, decisions, env_version,
writable_project})`; `data_version = sha256_dir(output)`. The manifest
(SCHEMA_VERSION 2) is a declared Snakemake output of every rule — a
missing manifest forces a re-run, closing the agent-faked-file
scenario. `status` and `verify` read only manifests and the repo:
offline by invariant. The one shared `code_version()` function is
called by both the generator (write path) and status (read path), so
they can never disagree.

### Hermeticity (`engine/sandbox/`, `lightcone/_sandbox_exec.py`, `engine/boundary.py`)

Every recipe and probe executes through the `ExecBoundary`: a
per-job capability probe picks the mechanism (Landlock on Linux —
including inside containers; Seatbelt on macOS), `policy.py` realizes
the declared sets (own-output write, project+inputs read, env +
versioned allowlist + ELF loaders exec, fresh per-recipe HOME/XDG),
and the stdlib-only shim applies the restriction between fork and
exec. The manifest's `hermeticity` field records the *applied* flags —
downgrades are announced, never silent; `--no-sandbox` is recorded as
`{none, open}`.

### Images (`engine/image/`)

Modal-inspired internals behind a one-TOML-table user surface:
`declaration.py` parses and statically refuses; `definition.py` +
`render.py` produce a deterministic Containerfile (fixed layering, apt
before sync, offline ENV only in the final stage); `identity.py`
computes `lc-env-<hash>` as a pure function of rendered text +
`pyproject.toml` + `uv.lock` (code edits move nothing);
`builder_podman.py` builds with pointed error mapping;
`runtime_podman.py` runs the full stack digest-pinned under
`--net=none`/`--userns=keep-id`; `record.py` keeps the build record
and the dpkg snapshot attestation. `Builder` and the runtime are
protocols — remote builders and other venues return behind them.

## Repository structure

```text
src/lightcone/              # PEP 420 namespace — NO __init__.py
├── _sandbox_exec.py        # the exec shim (stdlib-only)
├── launcher.py             # tool-env launcher / delegation
├── cli/                    # Click surface (init, materialize, run, status, verify, build, export)
├── engine/
│   ├── environment.py      # Mode, EnvironmentSpec, env_version, lock scan
│   ├── manifest.py         # the integrity layer (SCHEMA_VERSION 2)
│   ├── job.py              # RuleJob — the generator→worker contract
│   ├── snakefile.py        # Snakefile generator
│   ├── runner.py           # run_rule: the worker sequence
│   ├── boundary.py         # ExecBoundary seam
│   ├── sandbox/            # Landlock/Seatbelt policy, wrap, probe, denial UX
│   ├── image/              # declaration → render → identity → build → run
│   ├── attestation.py      # worker-side runtime capture
│   ├── status.py verify.py # offline readers
│   ├── dask_cluster.py     # run-scoped LocalCluster
│   ├── scratch.py tree.py validation.py wrroc.py
│   └── project.py uv_env.py
└── snakemake_executor_plugin_dask/   # rules as dask tasks
```

## Key invariants

- `astra.yaml` carries analysis structure only; the environment lives
  in the uv project files. Legacy `container:` keys are ignored.
- The Snakefile and `snakefile-config.json` are regenerated on every
  `lc materialize` — never edit them.
- A failing recipe writes no manifest; the `os.replace` in
  `write_manifest` is the atomic commit point.
- Recipes are never wrapped at generation time: enforcement happens at
  exec time (the boundary), containerization at delegation time (the
  launcher).
- Run flags (`--no-sandbox`, `--require-sandbox`) travel to workers via
  environment variables, never via cfg — they must not perturb the
  content-addressed job identity.
- The manifest records what *actually* ran — mechanism, image digest,
  platform — never what documentation says should have run.
