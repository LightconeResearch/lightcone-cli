# CLAUDE.md

## Project Overview

**lightcone-cli** is Lightcone Research's execution layer for ASTRA
(Agentic Schema for Transparent Research Analysis). It ships the `lc`
executable — an agent-agnostic CLI; it bundles no agent-specific
skills, hooks, or plugins.

- **ASTRA** = pure specification: schema, validation, helpers, minimal
  CLI. Carries analysis structure only (inputs/outputs/recipes/
  decisions/universes) — **never** environment or sandbox information.
- **lightcone-cli** = execution layer: the uv-based environment model,
  Snakemake-based execution, the OS sandbox, the podman container
  hatch, and per-output provenance manifests.

The normative design is `docs/design/execution-environment.md` (v6.1);
the implementation deviates from it only where recorded (ASTRA carries
no container/sandbox keys; `PYTHONPYCACHEPREFIX` amendment; local-only
venue scope with podman as the only container backend).

### Namespace contract

`lightcone-cli` ships the `lightcone.*` namespace via PEP 420 implicit
namespace packages. **`src/lightcone/` must not contain an
`__init__.py`** — that would break coexistence with future sibling
distributions. Any new `lightcone-*` package must use src-layout, not
create `src/lightcone/__init__.py`, and ship only its own subpackage.

## Architecture

Three owned layers over a Snakemake core (see `docs/architecture.md`
for the full picture):

1. **Environment** — a project is `pyproject.toml` + `uv.lock` +
   `.python-version`; uv is the only substrate. Mode is *derived*:
   declaring `[tool.lightcone.image]` (or `Containerfile.extra`) is the
   escalation into containerized mode. `env_version` (lock ‖ interpreter
   pin ‖ install settings ‖ system layer) sits inside every output's
   `code_version`. The launcher (`lightcone/launcher.py`) delegates
   execution verbs to the project-locked engine — frozen interface:
   argv passthrough + `LC_DELEGATED=1`.
2. **Integrity** — `.lightcone-manifest.json` per output
   (SCHEMA_VERSION 2): code/env/data versions, input chain, git state,
   runtime attestation, image identity, hermeticity. The manifest is a
   declared Snakemake output; a failing recipe writes none.
3. **Hermeticity** — every recipe/probe executes through the
   `ExecBoundary` (`engine/boundary.py` → `engine/sandbox/`): Landlock
   on Linux (incl. in-container), Seatbelt on macOS; declared-set
   policy (own-output RW, project+inputs RO, env + versioned utility
   allowlist + ELF loaders exec, fresh per-recipe HOME/XDG/
   PYTHONPYCACHEPREFIX). The shim is `lightcone/_sandbox_exec.py`
   (stdlib-only; exit 97 = setup failure). Manifests record the
   *applied* enforcement; downgrades are announced, never silent.

**Images** (`engine/image/`): one-TOML-table surface, Modal-inspired
internals — declaration → deterministic render (fixed layering, apt
before `uv sync`, offline ENV in the final stage only) → tag
`lc-env-<hash>` as a pure function of rendered text + pyproject +
uv.lock → podman build with pointed error mapping → digest-pinned
full-stack run (`--net=none`, `--userns=keep-id`, entrypoint cleared).
Project code never enters an image; code edits never move the tag.

**Execution flow**: `lc materialize` → launcher converges/delegates →
`snakefile.generate()` (recipes rendered but **never wrapped**; typed
`RuleJob` cfg) → `snakemake --executor dask` on a run-scoped
LocalCluster → `run_rule()` worker sequence: pre-gate (env_version
recomputed vs baked) → env check (`uv sync --check` / image identity
assert) → boundary exec with offline overlay → post-gate →
`write_manifest`.

## CLI surface

- `lc init` — idempotent uv-native scaffold (pyproject with the engine
  locked in, `.python-version`, `uv lock` + sync, AGENTS.md stanza);
  refuses an authored root Containerfile; `--check`/`--json`.
- `lc materialize [outputs…]` — execute; `--require-sandbox[=declared-fs]`,
  `--no-sandbox` (flags reach workers via env, never cfg).
- `lc run [cmd…]` — the probe verb: arbitrary commands in byte-for-byte
  the recipe environment (sandboxed, tmp-only writes); rename guard for
  output ids; `--sandbox-debug`; never builds images.
- `lc status` — offline; 3-line header (mode/image/sandbox) + per-output
  states incl. `pre_migration`; blast-radius line.
- `lc verify` — tamper/chain checks + notes (unsandboxed, dirty_tree,
  pre_migration).
- `lc build` — containerized mode only (direct = explanatory no-op).
- `lc export wrroc` — RO-Crate bundle.

## Development Commands

```bash
uv sync --group dev
uv run pytest                       # default: excludes slow/podman marks
uv run pytest -m podman             # real podman builds (this machine has podman)
uv run ruff check src/ tests/
uv run mypy src/                    # strict
```

A `justfile` covers common tasks (`just test`, `just lint`,
`just docs`). The macOS Seatbelt smoke runs only in
`.github/workflows/sandbox-macos.yml`.

## Key Invariants

- `astra.yaml` = analysis structure only; legacy `container:` keys are
  ignored. The environment is the uv project + `[tool.lightcone.*]`
  (a **closed** surface — unknown keys are refusals).
- `.lightcone/Snakefile` + `snakefile-config.json` are regenerated
  every run; `.lightcone/image/` is the machine-local build record
  (gitignored).
- `code_version = sha256({recipe, decisions, env_version,
  writable_project})` — computed only via `manifest.code_version()`,
  shared by generator and status.
- Per-output sandbox escalation lives in pyproject:
  `[tool.lightcone.sandbox] writable-project = ["<output_id>"]` —
  hashes into that output's `code_version`, not `env_version`.
- Manifest filename `.lightcone-manifest.json` is fixed; changing
  semantics means bumping `SCHEMA_VERSION` and the golden field-list
  test. `sha256_dir` excludes the manifest and `.snakemake_timestamp`.
- Engine constants (base/uv digests, `DEFAULT_PYTHON`) live in
  `engine/image/constants.py` and change only with an engine release.
- Golden tests pin: env_version fingerprints (`test_environment.py`),
  rendered Containerfiles (`tests/goldens/`, regen with
  `--regen-goldens`), the manifest v2 field list, and the frozen
  delegation interface.

## Extending

| To… | Read | Pattern |
|---|---|---|
| Add a CLI verb | `cli/commands.py` + `launcher.py` | decide tool-env vs delegated (TOOL_ENV_VERBS) |
| Change identity semantics | `engine/environment.py`, `engine/manifest.py` | bump goldens consciously, same commit |
| Change the sandbox policy | `engine/sandbox/policy.py` | bump `EXEC_ALLOWLIST_VERSION`; add an enforcement test |
| Change the image | `engine/image/definition.py`/`render.py` | regen Containerfile goldens; podman smoke |
| Add a container backend / venue | `engine/image/builder.py` protocol, `engine/boundary.py` | implement the protocol; never fork call sites |

## Test Patterns

- Fixture projects come from `tests/conftest.py::make_project`
  (deterministic bytes — identity goldens hash them).
- Landlock enforcement tests (`test_sandbox_enforcement.py`) run
  unprivileged but place projects under `$HOME`, NOT `tmp_path` — the
  policy grants `/tmp` blanket-RW, which would mask denials.
- `lc init` tests fake the uv seam (`commands._run_uv`) — never real
  resolution in unit tests.
- Real-subsystem tests are opt-in marks: `slow` (LocalCluster),
  `podman` (real builds), `darwin` (Seatbelt smoke on macOS CI).

## Conventions

- Ruff (E, F, I, N, W, UP), line length 100, target Python 3.11; mypy
  strict with `namespace_packages = true`.
- Errors are the interface: refusals carry the exact fix (the denial
  UX, base-contract messages, `lc build` pointers). Never a raw log
  or a silent fallback.
- The manifest records what actually ran — never what should have.
