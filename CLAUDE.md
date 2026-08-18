# CLAUDE.md

## Project Overview

**lightcone-cli** is Lightcone Research's execution layer for ASTRA
(Agentic Schema for Transparent Research Analysis). It ships the `lc`
executable — an agent-agnostic CLI; it bundles no agent-specific skills,
hooks, or plugins.

- **ASTRA** = pure specification: schema, validation, prior insights &
  findings, evidence verification, helpers, minimal CLI
- **lightcone-cli** = execution layer: project scaffolding, execution,
  environment identity, hermeticity enforcement

lightcone-cli depends on ASTRA. The `astra` CLI handles spec operations;
the `lc` CLI handles execution.

## ⚠️ This repository is a clean rebuild in progress

The codebase is being **re-added layer by layer** on top of the normative
design spec:

> **`../lightcone-cli/docs/design/execution-environment.md`** — *"the
> locked environment is the execution environment"*, v6.1. Normative.
> Read it before adding anything. It lives in the sibling checkout for
> now (branch `redesign_prototype`), alongside its decision records
> (rationale, substrate tradeoffs, hermeticity enforcement, the v6
> review); it moves into this repo's `docs/design/` when the docs are
> rewritten at the end of the rebuild.

The pre-rebuild codebase (Snakemake shim, authored Containerfiles,
`container:` in `astra.yaml`, vendored dask executor plugin, WRROC export)
was stripped deliberately. Functionality comes back **one layer at a
time**, each layer landing with its own tests and dependencies — never
speculatively.

### Layers

| # | Layer | State |
|---|-------|-------|
| 1 | **Project scaffolding** — `lc init` | ✅ **done** |
| 2 | Environment layer — `env_version`, lock scan, manifest schema | ⬜ |
| 3 | The `lc` entrypoint — launcher: discover → mode-detect → scrub `UV_*` → converge → delegate | ⬜ |
| 4 | Fabric — `lc materialize`, worker sequence, mid-run relock gate | ⬜ |
| 5 | Sandbox layer — Landlock / Seatbelt, exec-shim, denial UX | ⬜ |
| 6 | Container hatch — `[tool.lightcone.image]`, generated Containerfile, `lc build` | ⬜ |
| 7 | Venues — Perlmutter, hub/GKE, Cloud Build | ⬜ |
| 8 | `lc status`, `lc verify`, WRROC export | ⬜ |

The spec's §11 (Migration) is the authoritative ordering; the table above
is the working map. **Layer boundaries are also dependency boundaries** —
a dependency enters `pyproject.toml` with the layer that needs it, not
before.

### Rules while rebuilding

- **No dead code.** If nothing in the current layer calls it, it doesn't
  land yet. `lc --help` advertises only verbs that work.
- **`docs/` is frozen at its pre-rebuild state, by decision.** It still
  describes the old Snakemake architecture and the full command set.
  Don't patch it layer by layer — it gets rewritten in one pass once the
  rebuild is complete. Same for `README.md` and `zensical.toml`.
- **Port with intent.** Prior implementations (this repo's git history,
  and the `redesign_prototype` branch of the sibling `lightcone-cli`
  checkout) are references, not sources of truth. The spec is.
- **Every layer ships tests.** See the per-layer test list in spec §11.

## Architecture (target)

A project is `pyproject.toml` + `uv.lock` + `.python-version` — **uv is
the only environment substrate**. Mode is *derived, not configured*:

```
direct mode (default)              containerized mode
─────────────────────              ──────────────────
.venv in the project tree          [tool.lightcone.image] declared
no image, ever                     ⇒ content-addressed image is the
recipes run under Landlock/          execution world: driver, workers,
  Seatbelt                           recipes, probes all run in-image
                                     from the baked /opt/venv
```

Identity: `env_version = sha256(uv.lock ‖ .python-version ‖ canonical
install-settings ‖ canonical [tool.lightcone.image] ‖ Containerfile.extra
hash)`, sitting inside `code_version`. Every output records what
enforcement it actually ran under (`hermeticity`). See spec §1–§3, §7.

### Namespace contract

`lightcone-cli` ships the `lightcone.*` namespace via PEP 420 implicit
namespace packages. **`src/lightcone/` must not contain an
`__init__.py`** — that would turn the namespace into a regular package
and break coexistence with future sibling distributions
(`lightcone-ui`, etc.).

Any new `lightcone-*` package must:

1. Use src-layout (`src/lightcone/<name>/…`).
2. Not create `src/lightcone/__init__.py`.
3. Ship only its own subpackage under `src/lightcone/<name>/`.

## Repository Structure (current)

```
src/lightcone/              # namespace — NO __init__.py
├── cli/                    # the CLI only: flags, rendering, exit codes
│   ├── __init__.py         # exposes main(); the launcher hooks in here (layer 3)
│   └── commands.py         # lc init — the only verb so far
└── engine/
    ├── __init__.py         # docstring only
    ├── project.py          # how a directory converges into a project
    └── templates/          # the scaffold's file content
        ├── __init__.py     # loader + one renderer per scaffolded file
        └── files/*.tmpl    # the templates themselves, as real files

evals/                      # agentic eval seed: prompt.md + tasks/<id>/
tests/                      # pytest — mirrors src/
```

## Development Commands

```bash
uv sync --group dev              # installs pytest, ruff, mypy
uv run pytest
uv run ruff check src/ tests/    # --fix to apply
uv run mypy src/
uv build                         # wheel + sdist
```

These four are the whole loop, and they are exactly what CI runs
(`.github/workflows/{tests,lint}.yml`). There is deliberately no task
runner in between — the pre-rebuild `justfile` was 90 lines of wrappers
around them plus recipes for the frozen docs and the dormant eval.

## Key Invariants (layer 1)

**The CLI/engine split.** `cli/commands.py` owns flags, console rendering,
and exit codes — nothing else. Everything about *what a project is* lives
in `engine/project.py`:

| Symbol | Role |
|---|---|
| `converge(dir, *, write)` | The whole scaffold operation |
| `ConvergenceReport` | `created` / `repaired` / `unchanged` / `blocked` / `warnings`, plus `.converged` and `.as_dict()` |
| `project_name(dir)` | PEP 503-ish name from the directory name |
| `ProjectError` | The one engine exception; `_EngineErrorGroup` in the CLI turns it into a clean `ClickException` |

The engine never imports click and never prints. `converge(write=False)`
is check mode — the *same* decision path with side effects switched off,
which is what keeps `--check` honest rather than a second implementation.
Every item therefore routes through `_Converger.item`, `.file`, or
`.blocked`; **nothing writes or records outside that mechanism.** `.file`
takes a *thunk*, so check mode renders no template at all.

Two report distinctions that matter: a **warning** is something
convergence can see but must not fix (advisory — never affects
`converged`); a **blocked** item is one convergence cannot complete, and it
does count, so a report can never claim a project is converged while
something it owns is absent.

**Project discovery** — the `astra.yaml` walk-up — arrives with the
launcher (layer 3), the first thing that needs it. `lc init` is handed its
directory.

**CLI startup stays cheap.** `commands.py` imports the engine *inside* the
command callbacks and builds the rich console lazily, so `lc --help` and
shell completion pay for neither. Keep this up as verbs land: a module-scope
engine import would make every invocation pay for the heaviest layer.

**Templates are files, not string literals.** `engine/templates/files/*.tmpl`
are package data, loaded through `importlib.resources`; `templates/__init__.py`
exposes one function per scaffolded file. Placeholders are
`string.Template` (`${name}`) — **not** `str.format` — because several
templates legitimately contain braces (TOML tables, MyST `{astra}` roles).
Substitution is strict, so a missing key raises.

**No engine constants for the environment.** The scaffolded
`.python-version` is the interpreter `lc` itself is running on, and
`requires-python` is lightcone-cli's own `Requires-Python`, verbatim. These
can't conflict — lc only runs on an interpreter satisfying its own bound —
and neither is a number to maintain. Identity follows the project's files
from there: `env_version` hashes `.python-version`'s bytes, not anything in
the engine (spec §3).

**`.gitignore` converges entry-wise, not by marker.**
`templates.gitignore_entries()` is the template minus comments and blanks;
`missing_gitignore_entries(text)` is what a repair appends, in template
order. Idempotency is therefore structural — a pattern already in the file
is never re-added, whoever wrote it — and a pattern introduced by a later
lc release still reaches projects that already have a `.gitignore`, which
a "marker present ⇒ done" check would have skipped. `GITIGNORE_HEADER` is
cosmetic only; never make correctness depend on it.

**What `lc init` converges** — idempotently, never overwriting a file the
user owns:

| Path | Role |
|---|---|
| `astra.yaml` + `universes/baseline.yaml` | astra's boilerplate spec, verbatim, as **one item keyed on `astra.yaml`** — the baseline references the boilerplate's example decision, so it must never land beside a user-authored spec. Its `container:` key is ignored outright — see Recorded decisions |
| `pyproject.toml` | The uv project: **virtual** (no `[build-system]`), `lightcone-cli` pinned in its own dependencies — the engine lives inside the experiment's lock |
| `.python-version` | The exact patch of the interpreter `lc` is running on |
| `uv.lock`, `.venv` | **Derived** — converged by correctness, not existence: `uv lock --check` / `uv sync --check` decide, then `uv lock` / `uv sync --locked --exact --compile-bytecode` repair |
| `.gitignore` | One managed block of patterns; convergence ensures each is present |
| `results/` + `README.md` | Where outputs land; the README states the materialize-don't-hand-write contract |
| `myst.yml`, `index.md` | Template MyST report referencing `astra.yaml` *by path* |

- **Only what git can carry is converged.** No `src/`, and no empty
  `universes/`: git does not track empty directories, so converging one
  reports drift on every fresh clone, forever. astra dropped `src/` for the
  same reason (astra-tools#100) — where analysis code lives is the user's
  layout, and the boilerplate's `python src/main.py` is a placeholder.
  Universes are discovered by `glob("*.yaml")`, which is empty-not-error on
  a missing directory. `tests/test_project.py::test_a_clone_of_a_converged_project_is_converged`
  pins this: a clone must need nothing but `.venv`.
- **Convergence, not scaffolding.** Each item is created if missing,
  offered to a conservative `repair(text) -> str | None` hook otherwise,
  and left alone when the hook returns `None`. `--check` computes the
  same report without writing (exit 1 when not converged); `--json`
  emits `{converged, created, repaired, unchanged, blocked, warnings}`.
- **Derived artifacts converge by correctness, not existence.** `uv.lock`
  and `.venv` go through `_Converger.item`'s optional `is_current=`
  predicate, which is uv's own no-write verification (`uv lock --check`,
  `uv sync --locked --exact --check` — both confirmed read-only against uv
  0.12.3). A lock that no
  longer matches `pyproject.toml`, or an environment that no longer matches
  the lock, is exactly as unconverged as a missing one and reports as
  `repaired`. Existence alone made `converge()` a no-op on drift, which
  layer 3's launcher — which converges on every invocation precisely to
  guarantee the environment matches the lock — would have silently
  inherited.
  - The probe is skipped when the artifact is absent (nothing to ask), so a
    fresh project costs none and the created/repaired split falls out of the
    same check.
  - Check mode may *probe* but never mutates; `test_check_mode_only_probes`
    pins that every uv call it makes carries `--check`.
  - **Honest limitation** (measured, uv 0.12.3): `uv sync --check` catches
    packages the lock requires and the environment lacks, but not *extras* —
    a hand-installed package leaves it reporting "would make no changes".
    Set-level, not byte-level, as spec §3 already accepts; what bounds what
    a recipe can import is the sandbox, not this probe.
- **An authored `Containerfile` is left alone.** Spec §8 has `lc init`
  refuse one; we don't. Images are generated from the lock, so a
  hand-written file is simply not an input to anything — treating it as a
  blocking half-state was more ceremony than the situation earns. Revisit
  if the container hatch (layer 6) turns out to need the disambiguation.
- **Nothing about uv's caching or linking is overridden**, because uv's
  defaults are already right: it clones (copy-on-write) or hard links
  package content out of a global cache, so projects share it. Measured
  here: two environments cost 284 MB together rather than 432 MB. The docs
  discourage forcing `symlink` mode (it couples every environment to the
  cache's survival), and **`--system-site-packages` must never be used** —
  it would make packages outside the lock importable, which is exactly what
  the environment model exists to prevent (spec §7, G6).
  - The sharing silently stops working when the cache and the project are
    on **different filesystems** (uv falls back to full copies). uv warns;
    `tool_warnings()` lifts that warning out of uv's progress output into
    the report, so it reaches both the console and `--json`. This is why
    the site registry supplies `UV_CACHE_DIR` on Perlmutter (spec §4).
  - `--compile-bytecode` is the one genuinely per-project cost: bytecode is
    generated into the venv, never linked (~55 MB of 216 MB here). It is a
    deliberate trade — the environment is read-only at execution time, so
    without it every recipe run re-compiles.
- **Every external tool goes through one seam**, `project._run`, which
  tests monkeypatch — so the suite never shells out, and every call is
  inspectable. `_check_call` turns a nonzero exit into a `ProjectError`:
  nothing convergence invokes is allowed to fail silently. Every uv
  invocation carries an explicit `--project` — uv's own walk-up discovery
  is never trusted (spec §4).
- **uv is required; git is optional.** An absent uv is a refusal (it is
  the environment substrate); an absent git is simply nothing to converge,
  since a project without version control is still a valid project.
- **`git init` checks for an *enclosing* work tree**, not just a `.git` in
  the directory, so `lc init subdir/` inside a repository can't create a
  nested one. (`.git` may be a file — linked worktree or submodule — so the
  test is `exists`, not `is_dir`.)
- **`lc init` has exactly two flags**, `--check` and `--json`. `--no-git`
  and `--no-sync` were deleted: neither had a caller outside the test suite,
  `--no-git` was a workaround for the missing enclosing-repo check, and
  `--no-sync`'s real home is containerized mode (layer 6), where the host
  `.venv` is inert. Don't add a flag whose only user is a test — stub
  `project._run` instead.
### Recorded decisions

- **The ASTRA `container:` directive is ignored, entirely.** astra's
  boilerplate writes `container: python:3.12-slim` into `astra.yaml`, and
  lightcone-cli does nothing with it: not read, not stripped, not
  validated, not migrated. The environment is `pyproject.toml` + `uv.lock`
  (spec §2), so a scaffolded project simply carries a key no code path
  consults. Don't "fix" this by reconciling the two — a later layer will
  decide whether the key is dropped upstream, refused, or migrated.

### Recorded deviations from the spec

- **No `AGENTS.md` scaffolding** (spec §2 calls for an agent notes
  stanza). Dropped by decision: it documented four verbs, three of which
  don't exist yet, and the scaffold shouldn't assert behavior the CLI
  can't deliver. Revisit at layer 4, when the verbs it describes are
  real. `tests/test_project.py::test_converge_writes_no_agent_notes`
  pins the absence, so re-adding it is a deliberate act.
- Scaffolded file bodies stay descriptive of what works today — see the
  trimmed `results/README.md` for the same reason.

## Extending the Codebase

| To... | Read | Key patterns |
|---|---|---|
| Add the next layer | the spec (§11 = the layer ordering) | Land code + tests + deps together; update the layer table above. Docs are deliberately deferred |
| Change what a scaffolded file contains | `src/lightcone/engine/templates/files/` | Edit the `.tmpl`; add new ones to `TEMPLATE_NAMES` + a renderer |
| Add a value to the scaffold | `src/lightcone/engine/templates/__init__.py` | Derive it from the environment or our own metadata before introducing a constant |
| Change what gets converged | `src/lightcone/engine/project.py` + `tests/test_project.py` | `_Converger.item` / `.file`; repairs only ever append |
| Add a CLI verb | `src/lightcone/cli/commands.py` | `@main.command()`; keep logic in the engine, raise `ProjectError`, render here |

## Test Patterns

- `tests/conftest.py` — the `tools` autouse fixture stubs
  `engine.project._run`, emulating each tool's observable effect (`uv lock`
  writes `uv.lock`, `uv sync` makes `.venv`, `git init` makes `.git`) and
  recording every argv. `uv_calls(tools)` narrows it to uv. Tests are
  hermetic: no network, no resolution, no subprocesses.
- `tests/test_project.py` — discovery and convergence semantics, called
  directly. This is where scaffold behavior is tested.
- `tests/test_cli.py` — the CLI surface only: flags reaching the engine,
  rendering, exit codes, error translation. Rich wraps output at terminal
  width, so assert on short unwrappable fragments.
- `tests/test_templates.py` — template loading (guards the packaging of
  package data), strict substitution, and the `.gitignore` entry/repair
  logic. Content assertions live here; `test_project.py` asserts only that
  the file written *is* the template, so a template edit touches one file.

## Conventions

- Ruff for linting (E, F, I, N, W, UP), line length 100, target Python 3.11
- mypy strict mode with `namespace_packages = true`,
  `explicit_package_bases = true`
- Docstrings and comments carry *why*, and cite the spec section
  (`spec §7`) when they encode a design decision
