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
    ├── __init__.py         # lc_version()
    ├── constants.py        # engine constants (interpreter pin, uv floor)
    ├── project.py          # what a project IS: discovery + convergence
    └── templates/          # the scaffold's file templates
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
| `find_root(start)` | Discovery: nearest ancestor holding an `astra.yaml`, else `None` |
| `project_name(dir)` | PEP 503-ish name from the directory name |
| `converge(dir, *, write, git, sync)` | The whole scaffold operation |
| `ConvergenceReport` | `created` / `repaired` / `unchanged` / `warnings`, `.converged`, `.as_dict()` |
| `ProjectError` | The one engine exception; `_EngineErrorGroup` in the CLI turns it into a clean `ClickException` |

The engine never imports click and never prints. `converge(write=False)`
is check mode — the *same* decision path with side effects switched off
(via `_Converger.item` / `.file`), which is what keeps `--check` honest
rather than a second implementation.

**Templates are files, not string literals.** `engine/templates/files/*.tmpl`
are package data, loaded through `importlib.resources`; `templates/__init__.py`
exposes one function per scaffolded file. Placeholders are
`string.Template` (`${name}`) — **not** `str.format` — because several
templates legitimately contain braces (TOML tables, MyST `{astra}` roles).
Substitution is strict, so a missing key raises.

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
| `astra.yaml`, `universes/baseline.yaml`, `src/` | ASTRA spec scaffold (from astra's boilerplate; the `container:` key is stripped — ASTRA carries no environment) |
| `pyproject.toml` | The uv project: **virtual** (no `[build-system]`), `lightcone-cli` pinned in its own dependencies — the engine lives inside the experiment's lock |
| `.python-version` | Exact interpreter patch (`engine.constants.DEFAULT_PYTHON`) |
| `uv.lock`, `.venv` | `uv lock`, then `uv sync --locked --exact --compile-bytecode` |
| `.gitignore` | One managed block of patterns; convergence ensures each is present |
| `results/` + `README.md` | Where outputs land; the README states the materialize-don't-hand-write contract |
| `myst.yml`, `index.md` | Template MyST report referencing `astra.yaml` *by path* |

- **Convergence, not scaffolding.** Each item is created if missing,
  offered to a conservative `repair(text) -> str | None` hook otherwise,
  and left alone when the hook returns `None`. `--check` computes the
  same report without writing (exit 1 when not converged); `--json`
  emits `{converged, created, repaired, unchanged, warnings}`.
- **An authored `Containerfile` is refused**, before any write: images
  are generated from the lock, so a hand-written one would be silently
  ignored. The user's own file operation is the consent to migrate — no
  flag substitutes for it (spec §8).
- **uv is required** and is invoked through a single seam
  (`commands._run_uv`) that tests monkeypatch. Every uv invocation
  carries an explicit `--project` — uv's own walk-up discovery is never
  trusted (spec §4).
- **Project discovery** is one rule (`engine.project.find_root`): the
  nearest ancestor, including the start directory, holding an
  `astra.yaml`.

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
| Change what gets converged | `src/lightcone/engine/project.py` + `tests/test_project.py` | `_Converger.item` / `.file`; repairs only ever append |
| Add a CLI verb | `src/lightcone/cli/commands.py` | `@main.command()`; keep logic in the engine, raise `ProjectError`, render here |
| Add an engine constant | `src/lightcone/engine/constants.py` | Constants ship inside the locked engine — never resolved at run time |

## Test Patterns

- `tests/conftest.py` — the `fake_uv` autouse fixture stubs the uv seam
  (`engine.project._run_uv`), emulating `uv lock` writing `uv.lock` and
  `uv sync` making `.venv`, plus an isolated `$HOME`. Tests are hermetic:
  no network, no real resolution.
- `tests/test_project.py` — discovery and convergence semantics, called
  directly. This is where scaffold behavior is tested.
- `tests/test_cli.py` — the CLI surface only: flags reaching the engine,
  rendering, exit codes, error translation. Rich wraps output at terminal
  width, so assert on short unwrappable fragments.
- `tests/test_templates.py` — template loading (guards the packaging of
  package data), strict substitution, marker presence.

## Conventions

- Ruff for linting (E, F, I, N, W, UP), line length 100, target Python 3.11
- mypy strict mode with `namespace_packages = true`,
  `explicit_package_bases = true`
- Docstrings and comments carry *why*, and cite the spec section
  (`spec §7`) when they encode a design decision
