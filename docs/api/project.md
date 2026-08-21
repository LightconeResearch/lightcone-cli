# lightcone.engine.project

What a project is: the convergence engine behind `lc init`, project
discovery, mode detection, and the one subprocess seam the whole
engine shares.

Source: `src/lightcone/engine/project.py` (+
`engine/templates/` for the scaffold's file content).

## Key symbols

| Symbol | Role |
|---|---|
| `converge(dir, *, write)` | The whole scaffold operation. `write=False` is check mode — the *same* decision path with side effects off. |
| `ConvergenceReport` | `created` / `repaired` / `unchanged` / `blocked` / `warnings`, plus `.converged` and `.as_dict()`. |
| `current_project()` | The cwd as a project: requires `pyproject.toml`, `uv.lock`, `.venv`. |
| `declared_project()` | The weaker question — what the repository carries, without `.venv`. One caller: the worker entry point, which builds the venv a moment later. |
| `mode(root)` | `"direct"` or `"containerized"` — presence of `[tool.lightcone.image]`, nothing else. |
| `uv_prefix(root, *, sync)` | The one spelling of the project uv hop. Callers differ only in `sync`: a probe converges the environment, a recipe must not. |
| `project_name(dir)` | PEP 503-ish name from the directory name. |
| `_run` / `_check_call` | Every external tool invocation, and the suite's one monkeypatch point. |
| `child_env()` / `scrub_warning()` | The one composer of the environment external tools run in: drops `VIRTUAL_ENV`, ambient `UV_*` outside the `_UV_KEPT` plumbing allowlist, and `MOUNT_*` (a site container module's mount gates); supplies a known center's uv plumbing (`UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR`) from its scratch root where unset (`venue.site_env`). The warning names every non-empty variable dropped. |
| `ProjectError` | The engine's one exception; the CLI translates it once. |

## What must stay true

- **Everything routes through the converger.** Every scaffold item
  goes through `_Converger.item` / `.file` / `.blocked`; nothing
  writes or records outside that mechanism. `.file` takes a *thunk*,
  so check mode renders no template at all.
- **Derived artifacts converge by correctness, not existence.**
  `uv.lock` and `.venv` are probed with uv's own no-write checks
  (`uv lock --check`, `uv sync --locked --exact --check`); drift
  reports as `repaired`. Check mode may probe but never mutates —
  pinned by `test_check_mode_only_probes`.
- **A warning is advisory; a blocked item counts.** Convergence never
  claims a project is converged while something it owns is absent or
  unfixable — and repairs only ever append (`.gitignore` /
  `.gitattributes` are converged entry-wise, order judged against the
  template).
- **Only what git can carry is converged.** No `src/`, no empty
  directories — a clone must need nothing but `.venv` and
  `git annex init`, and
  `test_a_clone_of_a_converged_project_is_converged` pins it.
- **There is no discovery.** The invoked directory is the project or
  it is a clean error; every uv call carries an explicit `--project`.
- **Templates are files** (`templates/files/*.tmpl`, `string.Template`
  with strict substitution), and a template gets a function only when
  there is a value to decide or a merge policy to hold.

## Tests

`tests/test_project.py` (semantics, against the stubbed `_run`),
`tests/test_templates.py` (content, substitution, repair logic),
`tests/test_cli.py` (the `lc init` surface).
