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

That split is enforced in code, not just described: everything about what
a spec *means* — scoping, `from:` references, conditional outputs,
universe resolution, the recipe placeholder grammar — is answered by
`astra.resolve` and validated by `astra.validation` before lc acts on it.
lc's whole ASTRA surface is ten functions; see Key Invariants (layer 4).

## ⚠️ This repository is a clean rebuild in progress

The codebase is being **re-added layer by layer** on top of the design
spec:

> **`../lightcone-cli/docs/design/execution-environment.md`** — *"the
> locked environment is the execution environment"*, v6.1. Read it before
> adding anything — but read it as a **reference, not gospel**: the
> rebuild deliberately drifts from it as implementation teaches better
> answers, and where this file's Recorded decisions disagree with the
> spec, the decisions win. It lives in the sibling checkout for now
> (branch `redesign_prototype`), alongside its decision records
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
| 2 | **Environment layer** — `env_version`, lock scan, manifest schema | ✅ **done** |
| 3 | ~~The `lc` entrypoint — launcher~~ | ❌ **removed by decision** (2026-08) — see Recorded decisions: the host `lc` is the engine, so there is nothing to delegate to |
| 4 | **Fabric** — `lc materialize`, worker sequence, mid-run relock gate | ✅ **done** |
| 5 | **Sandbox layer** — Landlock / Seatbelt, exec-shim, denial UX, `lc run` | ✅ **done** |
| 6 | **Container hatch** — `[tool.lightcone.image]`, `lc build`, OCI runtimes as the exec boundary, the image archived in the dataset | ✅ **done** |
| 7 | **Venues** — SLURM in-allocation execution, login guard, podman-hpc | 🔶 **landed; Perlmutter spike pending** — hub/GKE and Cloud Build deferred to their own layer |
| 8 | **Publication view** — the RO-Crate converged by materialize, foreign writes stale by history; **no `lc verify`, no `lc export`, by decision** | ✅ **done** |

`lc status` landed with the invalidation model rather than at layer 8:
once an output can be *behind*, something has to say which ones are, and
the verb is the same classification walk `--check` already does.

Layer 5 landed **out of order**, ahead of 2–4: `lc run` is the spec's
*probe* verb, and a probe has no output, so it needs neither manifests
(layer 2) nor the fabric (layer 4) — only project discovery, which came
with it. That makes it the smallest honest consumer of the exec boundary,
and the boundary is what layer 4 will then plug recipes into.

The spec's §11 (Migration) is the reference ordering; the table above is
the working map, and the spec is a reference the rebuild deliberately
drifts from — reversals land as Recorded decisions here rather than
waiting on a spec rewrite. **Layer boundaries are also dependency
boundaries** — a dependency enters `pyproject.toml` with the layer that
needs it, not before.

### Rules while rebuilding

Each of these has been asked for in review at least once; none is optional.

- **Never reference the design spec in code or comments.** No `spec §7`,
  no section numbers, no "the spec says". Code and its comments must
  stand on their own; design rationale lives in the design documents.
  (This file is the exception — it is *about* the design.)
- **No backward-compatibility code.** Nothing exists to honor the
  behavior of an older CLI, an older wire format, or trained fingers.
  If old behavior isn't promised, don't guard, version, or migrate it.
- **No foreshadowing.** No code, comment, flag, or user-facing message
  may mention a verb, layer, or feature that does not exist yet. The
  codebase is consistent with the project *at this point in time*.
- **No escape hatches around guarantees.** A feature that enforces
  something ships without a flag to turn the enforcement off.
- **Prefer literal behavior over invented convenience.** The current
  directory is the project root; erroring beats walking up or guessing.
- **Nothing waits on a human.** A verb is run by an agent more often
  than by a person, so no interactive prompt and no interactive shell —
  either is a hang, not a UX choice.
- **Streamline before shipping.** No small helper functions or
  rendering layers where a few inline lines read fine; consolidate.
- **Be honest about provenance.** Third-party material we adapt is
  "inspired from" upstream, clearly marked, with its license named —
  never passed off as verbatim, never left unattributed.
- **Leave working files alone.** Don't edit files that are fine just
  because a change nearby made them look touchable.

- **No dead code.** If nothing in the current layer calls it, it doesn't
  land yet. `lc --help` advertises only verbs that work.
- **`docs/` is frozen at its pre-rebuild state, by decision.** It still
  describes the old Snakemake architecture and the full command set.
  Don't patch it layer by layer — it gets rewritten in one pass once the
  rebuild is complete. Same for `README.md` and `zensical.toml`.
- **Port with intent.** Prior implementations (this repo's git history,
  and the `redesign_prototype` branch of the sibling `lightcone-cli`
  checkout) are references, not sources of truth. Neither is the spec by
  itself: the spec plus this file's Recorded decisions is the current
  design, and the decisions override the spec where they disagree.
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
hash)`, recorded beside `definition_version` rather than folded into it.
Every output records what
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
├── _sandbox_exec.py        # the Landlock shim — stdlib only, zero lightcone imports
├── cli/                    # the CLI only: flags, rendering, exit codes
│   ├── __init__.py         # exposes main(), lazily
│   └── commands.py         # lc init, lc run, lc materialize, lc status
└── engine/
    ├── __init__.py         # docstring only
    ├── project.py          # what a project is: convergence, discovery, mode
    ├── dataset.py          # the git + git-annex seam: how a project stores
    ├── identity.py         # env_version, definition_version, the lock scan
    ├── image.py            # the system layer: declaration, Containerfile, tag — pure
    ├── container.py        # runtimes, the build, the archived image — impure
    ├── crate.py            # the publication view: the repo as an RO-Crate — pure
    ├── assets.py           # an output: its directory, its manifest, its state
    ├── plan.py             # the spec, read as a graph of tasks
    ├── worker.py           # making one output; also the `python -m` entry point
    ├── materialize.py      # the driver: dirty gate, Dask, the save/restore loop
    ├── run.py              # what `lc run` is: the probe + the uv hop
    ├── sandbox/            # the exec boundary
    │   ├── __init__.py     # the public surface (detect, run, scope, the types)
    │   ├── model.py        # Policy · Capability · Attestation · Backend protocol
    │   ├── policy.py       # what a probe and a recipe may touch
    │   ├── boundary.py     # detect() + run(): the mechanism-blind half
    │   ├── landlock.py     # Linux backend
    │   ├── seatbelt.py     # macOS backend
    │   ├── oci.py          # containerized backend: the mount table as mechanism
    │   └── denial.py       # the denial UX
    └── templates/          # the scaffold's file content
        ├── __init__.py     # loader; a renderer only where there is a value to decide
        └── files/*.tmpl    # the templates themselves, as real files

evals/                      # agentic eval seed: prompt.md + tasks/<id>/
tests/                      # pytest — mirrors src/
```

## Development Commands

```bash
uv sync --group dev              # pytest (+ pytest-cov), ruff, mypy, datalad
uv run pytest
uv run ruff check src/ tests/    # --fix to apply
uv run mypy src/
uv build                         # wheel + sdist (CI runs this only to publish)
```

Test, lint and type-check are the whole loop, and they are what
`.github/workflows/{tests,lint}.yml` run. There is deliberately no task
runner in between — the pre-rebuild `justfile` was 90 lines of wrappers
around them plus recipes for the frozen docs and the dormant eval. The
other workflows are `eval.yml` (the agentic eval, on dispatch or PR
label), `pypi-publish.yaml`, and `docs-deploy.yml` for the frozen docs.

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

**There is no project discovery, by decision.** `lc init` is handed its
directory (defaulting to `.`); `lc run` assumes the current directory is
the project root — `project.current_project()` checks only that the
environment is there (`pyproject.toml`, `uv.lock`, `.venv`) and does not
require an `astra.yaml`, so any uv project can be probed. No walk-up:
the directory you invoke from is the project, or it is a clean error.

**Two questions about a project root, not one** — the same shape as
`_in_repository` / `_can_ask_git` below. `declared_project()` wants only
what the repository carries (`pyproject.toml`, `uv.lock`);
`current_project()` adds `.venv`. The split is named rather than a
`synced=` flag on one function, because "what makes a directory a
project" should not be negotiable per call site, and because a slice of
a constant would make the answer depend on the order its entries happen
to be in. The weaker question has exactly one caller — the worker entry
point, which builds the `.venv` a moment later — and that is the whole
reason it exists.

**CLI startup stays cheap.** `commands.py` imports the engine *inside* the
command callbacks and builds the rich console lazily, so `lc --help` and
shell completion pay for neither. Keep this up as verbs land: a module-scope
engine import would make every invocation pay for the heaviest layer.

**The scaffold comes from `astra.scaffold`, not `astra.cli`.** Both export
`create_boilerplate`, and the second drags Click, Rich and the validation
stack — measured, 37 ms against 4 ms. `astra.scaffold` is stdlib-only
(checked: it pulls no linkml_runtime, click, rich, pydantic or
jsonschema), which is why this one astra import sits at module scope in
`project.py` where `astra.validation` and `astra.resolve` must not.

**Templates are files, not string literals.** `engine/templates/files/*.tmpl`
are package data, loaded through `importlib.resources`. Placeholders are
`string.Template` (`${name}`) — **not** `str.format` — because several
templates legitimately contain braces (TOML tables, MyST `{astra}` roles).
Substitution is strict, so a missing key raises.

**A template gets a function only when there is something to decide** — a
value the caller supplies (`pyproject`, `datalad_config`, `index_md`) or a
merge policy for a file the user already owns (`gitignore_repair`,
`gitattributes_repair`). Everything else is read by name:
`templates.read("myst.yml.tmpl")`, or `partial(templates.read, …)` where
convergence wants a thunk. This reverses the earlier "one function per
scaffolded file" rule, which had five of the module's twenty functions
doing nothing but rename a file — a second place for the name to be
wrong, and one the type checker cannot catch. The two `*_repair`
functions stay named because `_Converger.file` hands them the text alone,
so the template name has to be bound before the call site.

**No engine constants for the environment — in direct mode.** The
scaffolded `.python-version` is the interpreter `lc` itself is running
on, and `requires-python` is that interpreter's minor as a floor. Both
come from one place, so they can't conflict, and neither is a number to
maintain. Identity follows the project's files from there: a direct
project's `env_version` hashes `.python-version`'s bytes, not anything
in the engine. The scoping is deliberate: containerized mode's default
base and uv digests *are* engine constants, by spec-§2 design — see the
layer-6 invariants for what moves when they do.

**`.gitignore` and `.gitattributes` converge entry-wise, not by marker.**
`templates.entries(name)` is a template minus comments and blanks;
`templates.missing(name, text)` is what a repair appends, in template
order. Idempotency is therefore structural — a pattern already in the file
is never re-added, whoever wrote it — and a pattern introduced by a later
lc release still reaches projects that already have a `.gitignore`, which
a "marker present ⇒ done" check would have skipped. The header comment
(`templates.header`) is cosmetic only; never make correctness depend on
it.

**What `lc init` converges** — idempotently, never overwriting a file the
user owns:

| Path | Role |
|---|---|
| `astra.yaml` + `universes/baseline.yaml` | astra's boilerplate spec, verbatim, as **one item keyed on `astra.yaml`** — the baseline references the boilerplate's example decision, so it must never land beside a user-authored spec. Its `container:` key is ignored outright — see Recorded decisions |
| `pyproject.toml` | The uv project: **virtual** (no `[build-system]`), no dependencies — the engine is the host's uv tool, never a project dependency (see Recorded decisions), so the lock carries only what the analysis imports |
| `.python-version` | The exact patch of the interpreter `lc` is running on |
| `uv.lock`, `.venv` | **Derived** — converged by correctness, not existence: `uv lock --check` / `uv sync --locked --exact --check` decide, then `uv lock` / `uv sync --locked --exact --compile-bytecode` repair |
| `.gitignore` | One managed block of patterns; convergence ensures each is present |
| `.git` + the annex | `git init` then `git annex init` — results are versioned in the project's own repository |
| `.gitattributes` | The storage policy: what git-annex holds and what git carries. Line-managed, like `.gitignore` |
| `.datalad/config` | A `datalad.dataset.id` UUID, generated once. Read back only by `dataset.dataset_id`, through `git config -f`, for the run record's `dsid` |
| `data/` + `README.md` | Where declared inputs live; annexed, and committed before anything computes on them |
| `results/` + `README.md` | Where outputs land; the README states the materialize-don't-hand-write contract |
| `myst.yml`, `index.md` | Template MyST report referencing `astra.yaml` *by path* |

- **Only what git can carry is converged.** No `src/`, and no empty
  `universes/`: git does not track empty directories, so converging one
  reports drift on every fresh clone, forever. astra dropped `src/` for the
  same reason (astra-tools#100) — where analysis code lives is the user's
  layout, and the boilerplate's `python src/main.py` is a placeholder.
  Universes are discovered by `glob("*.yaml")`, which is empty-not-error on
  a missing directory. `tests/test_project.py::test_a_clone_of_a_converged_project_is_converged`
  pins this: a clone must need nothing but `.venv` and `git annex init`.
  Those two are the exemptions, and for one reason — they are local state
  git does not clone.
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
  `repaired`. Existence alone made `converge()` a no-op on drift, and
  everything that converges before acting — `lc materialize`'s sync, the
  worker entry point's — would have silently inherited it.
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
    deliberate trade: paying compilation once here beats paying it on the
    first import of every run.
- **Every external tool goes through one seam**, `project._run`, which
  tests monkeypatch — so the suite never shells out, and every call is
  inspectable. `_check_call` turns a nonzero exit into a `ProjectError`:
  nothing convergence invokes is allowed to fail silently. Every uv
  invocation carries an explicit `--project` — uv's own walk-up discovery
  is never trusted (spec §4).
- **uv, git and git-annex are all required.** Each is a refusal, not a
  warning: uv is the environment substrate and git + git-annex are the
  storage substrate, and results are versioned in the repository, so there
  is no useful project without any of them. git is the one tool uv cannot
  install and the single admitted exception to a uv-installable stack;
  git-annex ships as a wheel and is therefore a dependency of the lc tool
  itself, which makes its wheel platforms the CLI's install floor.
  (This reverses layer 1's original "git is optional" — a project without
  version control had nowhere to put a result.)
- **Two questions about git, not one.** `_in_repository` is a pure
  filesystem walk-up and answers for a directory that does not exist yet,
  which is what check mode needs. `_can_ask_git` adds `is_dir()`, because
  every git *invocation* needs an existing working directory — inside an
  enclosing repository the walk-up says "in a repository" for a
  directory that is not there, and running git in it raises
  `FileNotFoundError` out of `Popen` rather than answering anything.
- **`git init` checks for an *enclosing* work tree**, not just a `.git` in
  the directory, so `lc init subdir/` inside a repository can't create a
  nested one. (`.git` may be a file — linked worktree or submodule — so the
  test is `exists`, not `is_dir`.) The annex asks git-annex's own question,
  `git config --get annex.uuid`, so an enclosing repository that already
  has one is adopted rather than re-initialized.
- **`lc init` has exactly two flags**, `--check` and `--json`. `--no-git`
  and `--no-sync` were deleted: neither had a caller outside the test suite,
  `--no-git` was a workaround for the missing enclosing-repo check, and
  `--no-sync`'s real home is containerized mode (layer 6), where the host
  `.venv` is inert. Don't add a flag whose only user is a test — stub
  `project._run` instead.
## Key Invariants (storage)

**Results are versioned in the project's own repository**, on the DataLad
model: **git carries the pointers and the history, git-annex carries the
bytes.** `engine/dataset.py` is the whole seam, and every command in it
goes through `project._run` — the same one convergence uses, so there is
one monkeypatch point and the `tools` fixture already covers git.

**git-annex is a wheel, and that sets the CLI's install floor.**
`manylinux_2_34` on x86_64/aarch64, macOS 14+ arm64 or 15+ x86_64,
win_amd64 — and **no sdist**, so a host below the floor fails to install
rather than building from source. Because git-annex is a *hard* runtime
dependency, that floor gates installing the lc tool at all, including
`lc run`, which never touches the annex — but only the tool: projects no
longer depend on lightcone-cli, so the floor never constrains a project's
own resolution. If it ever bites a real user, the hard dependency is the
thing to revisit — an extra, or a probed requirement like git — not the
floor.

**Perlmutter clears it** (checked 2026-08-19, login node): the wheel
installs and `git annex version` runs. That was the open question this
stack was most likely to fail on, so it is written down rather than
re-derived. Two things it does *not* settle, both layer 7's:

- **Lustre/GPFS behaviour is unmeasured.** arXiv:2505.06558 documents
  symlink, many-small-files and inode pressure on parallel filesystems.
  Correctness is not the worry; cost is, and one output is one file plus
  one hard link to its object. Results are already thin (above); adjusted
  (unlocked) branches remain the untried lever, as does whether Lustre
  makes the hard link behave differently. Time `git annex add` and
  `git annex get` on `$SCRATCH` and `$CFS` before designing around
  either.
- **`git-annex-shell` is not on the default remote PATH.** `git annex get`
  from a laptop dispatches to it over a non-login ssh session, and the
  wheel installs it beside the tool's interpreter. Configuration, not
  design: `git config remote.<name>.annex-shell <abs-path>`.

**`filter=annex` is what makes an ordinary `git add` do the right thing.**
`git annex init` configures the smudge/clean filter itself; the template
adds `* filter=annex`, and `annex.largefiles` then decides what counts as
content. So a researcher types `git add -A . && git commit` — the same
git they already know — and a 200 KB input lands in the annex as a
101-byte pointer while `src/main.py` and the manifests stay real. Nothing
lc scaffolds, prints or documents asks anyone to run a git-annex command,
and `dataset.save` does not run one either.

**The `annex.largefiles=nothing` default is load-bearing.** It has to come
first, with outputs and inputs opting out; last matching line wins.
Without it `filter=annex` routes *everything* into the annex, analysis
code included. `tests/test_dataset.py::test_analysis_code_stays_in_git_and_stays_writable`
pins it against a real annex.

**Manifests stay in git, deliberately.** `**/.lightcone-manifest.json` is
exempted back out of the annex so it is readable on a clone that has
fetched no annex content at all — which is what lets `lc materialize
--check` classify a whole project on a laptop that holds none of the
bytes.

**An unfetched file exists, and that is the trap — in two shapes.**
`assets.data_version` refuses both with `ContentNotFetchedError`, naming
`git annex get`, and it checks for both regardless of which one lc's own
writes produce: `annex.thin` and `git annex lock` are the researcher's to
set on their clone, so the shape a file arrives in is not ours to assume.

- **Unlocked** — what `filter=annex` writes, hard-linked or copied alike.
  A clone without content holds a ~100-byte *pointer file* where the data
  would be: it exists, it is readable, and hashing it yields a perfectly
  well-formed digest of the wrong thing. The test follows git-annex's own
  `isPointerFile`: a file no larger than 32 KiB whose bytes begin
  `/annex/objects/`. Measured before it was fixed: the same input hashed
  differently on a clone, silently.
- **Locked** — a symlink into the object store, which without the content
  dangles. This one is quieter and worse: `is_file()` answers False for a
  dangling symlink, so a directory walk filtered on that alone drops the
  absent file from the digest *without a word*, reporting a hash of
  whatever subset happens to be present. Only dangling symlinks are added
  back to the walk — one that resolves to a file already is one, and one
  that resolves to a directory is not content.

`--check` catches the error and reports "not in this clone" rather than
"changed", because the two are different facts and only one of them means
a rebuild.

**git-annex is on `PATH` by construction, not by runtime repair.**
`git annex` is not a builtin — git dispatches it by searching `PATH` for
a `git-annex` executable — and an installer links only the requested
package's own executables, so a plain `uv tool install lightcone-cli`
would have left the one git command we tell people to type unable to run.
The fix is metadata: lightcone-cli re-declares the git-annex wheel's four
entry points verbatim in its own `[project.scripts]` (`git-annex`,
`git-annex-shell`, `git-remote-annex`, `git-remote-tor-annex`, all
`git_annex:cli`). The wheel ships no raw binaries in `bin/` — its
executables *are* that dispatcher, which `execv`s the real binary out of
package data with `argv[0]` preserved, and git-annex dispatches on
`argv[0]` busybox-style. So every install channel carries the
executables wherever it carries `lc`: the tool install links all five
names (verified), `uv run` and `uvx` front the venv's bin, and the
rerun's ephemeral engine environment gets them the same way. That covers
lc's own subprocesses *and* the researcher's bare `git add` with one
mechanism, which is why the old `put_our_bin_first()` PATH-prepend was
deleted rather than kept as a belt: a second answer to "which annex runs"
that could disagree with the shell's.

The declaration is mirrored, never invented:
`test_the_annex_executables_are_ours_to_install` asserts ours match the
wheel's exactly, so an executable upstream adds, drops or renames fails
the suite rather than every user's install. (Considered and rejected:
there is no pyproject metadata to link a *dependency's* executables;
`--with-executables-from` is an install-time flag users won't type; a
post-install link repair contradicts "the install command is enough";
vendoring the binaries into platform-specific lightcone-cli wheels is
five 14–36 MB wheels and a repack pipeline for what four lines of
metadata provide.)

The accepted residue of dropping the prepend: `PATH` order is the
user's, so a system git-annex fronting the install's wins the dispatch —
for lc's subprocesses exactly as for their own git, and no different
from which `git` itself runs. Nothing records the annex version anyway
(see the Recorded decision on the engine's dependency closure).

**A project can sit inside a larger repository, so `dataset.status` is
scoped and relativised.** `lc init subdir/` adopts an enclosing work
tree rather than nesting a new one — that is a supported layout — and
`git status --porcelain` otherwise covers the *whole* work tree and names
paths from *its* root. Unscoped, an edit anywhere else in the repository
refuses every run in the project, and lc's own writes arrive as
`subdir/results/…`, which the refusal's path-class split cannot
recognise. So the call carries `-- .` and the `rev-parse --show-prefix`
is stripped off each path. A wholly untracked project collapses to `.`,
which is git's own summary of it and exactly what the refusal should tell
someone to add.

**git needs an identity, and that is asked before a run, not at the first
commit.** A fresh container or CI image has none — the case an agent-run
CLI meets most — and discovering it at `dataset.save` would throw away
whatever the recipe had already computed. `require_committer` asks
`git var GIT_COMMITTER_IDENT`, which is the question a commit itself
asks: an identity resolves from `user.email`, `EMAIL`, the author and
committer variables, or three levels of config, and a probe that
reimplements that lookup is one that can disagree with the thing it
stands in for. Measured: `git annex init` does *not* need one (it
tolerates the missing identity and exits 0), so `lc init` is not gated on
it — only the verb that commits is.

**The ignore probe asks about the directory as a directory.**
`check-ignore` is run with a trailing slash (`results/`), because the rule
that matters most — the `results/*` an older lc scaffold wrote — ignores
the directory's *contents* and does not match the bare name at all. And
with `--no-index`, which asks about the *rules* rather than the index:
without it git answers "not ignored" for anything already tracked, which
is exactly the project where someone committed one result by hand and left
the rule for the next. The item records **`blocked`**, not a warning —
`git add` skips ignored paths in silence, so a materialize would report
success and commit nothing — and it names `<file>:<line>:<pattern>`,
because `.gitignore` convergence only ever appends and cannot fix this.

**A new managed `.gitattributes` line is always appended at the *end* of
the template.** Repair is append-only, so a project scaffolded under an
older template receives new lines at the bottom of its file — and
`gitattributes_disorder` judges against template order. A line added
anywhere but last makes lc write an order it then reports as blocked, on
every project scaffolded before the line existed. The layer-6 image line
sits after the manifest exemption for exactly this reason (the patterns
are disjoint, so the order is free), and every future line takes the
same slot.

**`.gitattributes` can be *unrepairable*, and that is a second blocked
item.** The file is last-match-wins, and a repair only appends — so a
project that already carries `results/** annex.largefiles=anything`
without the `*` defaults gets `* annex.largefiles=nothing` appended
*below* it, and every result then lands in git as a plain blob while the
report says repaired and converged. `templates.gitattributes_disorder`
judges **the text a repair would produce**, not the text as it stands (a
file missing the defaults is in order until they are appended), and only
compares lines setting the **same attribute** — `* filter=annex` landing
under an `annex.largefiles` line is not disorder, and treating it as one
blocks a perfectly good file. Blocked rather than fixed, for the reason
the ignore rule is: reordering a file the user wrote is not something
append-only convergence gets to do, so it names the line and prints the
order the managed lines belong in.

**An lc project is a DataLad dataset from birth.** `.datalad/config`
carries a `datalad.dataset.id` UUID, generated once by `lc init` and never
regenerated — it identifies the dataset across clones and siblings.
Verified, not assumed: `datalad status` recognises a freshly scaffolded
project with no `--force` adoption step, and `Dataset('.').id` is the UUID
we wrote. The reciprocal is a standing non-goal: **lc never requires
datalad, never imports it, and never parses `.datalad/`.** lc *writes*
under it — the dataset id, and since layer 6 the image archives and the
`datalad.containers.*` config keys — but reads nothing back except
through `git config -f`, and the archive is read as an image, never as
datalad state. A researcher who wants `datalad get`, siblings or RIA
stores runs `uv add datalad` in their own project.

**Annexed files are ordinary writable files.** `filter=annex` keeps them
unlocked, so an output can be overwritten in place and nothing needs to
remove it first. (This reverses the earlier symlink model, where a
rebuild hit `PermissionError` on a path that looked perfectly ordinary.)

**Results are committed thin; declared inputs are not.** `dataset.save`
passes `-c annex.thin=true` to its `git add`, so a result is hard-linked
to its annex object instead of copied — measured, 39 MB to 20 MB for one
20 MB file, since an unlocked file otherwise exists both in the tree and
in the object store.

It is safe *there* and nowhere else, and the reason is specific:
**thin's hazard is an in-place write.** With the file hard-linked to the
object, one `open('r+b')` rewrites the object under the key that names
it — measured, the committed version becomes unrecoverable (`git annex
get` reports no known copies) and `fsck` only notices later, moving it to
`.git/annex/bad/`. lc never writes in place: a worker *removes* an output
directory before rebuilding it, and an unlink merely drops the link
count, leaving the object intact (measured — an old version still checks
out clean afterwards). So the flag is passed **per-add and never written
to the repository's config**, because repo-wide it would reach `data/`,
which researchers add with their own `git add` and whose tools —
`h5py.File(p, 'r+')`, astropy `mode='update'` — very much do open files
for update.

Thin-ness is *not* recorded anywhere: it is local working-tree state, so
a clone re-decides at `git annex get` time. That is why detection has to
handle every shape rather than the one we write.

**`restore` is scoped, and asymmetric.** `git clean -qfdx` always, plus
`git checkout HEAD --` **only when HEAD has the path** — a first
materialization has nothing to go back to, and the naive form exits
nonzero on the pathspec. Never `git checkout HEAD -- .`: a failed task
must not discard edits made elsewhere while the graph was running.

## Key Invariants (layer 2)

**Two hashes, and they answer different questions** (`identity.py`).
`definition_version = sha256(recipe ‖ canonical decisions)` is what the
spec says an output *is*. `env_version = sha256(uv.lock bytes ‖
.python-version bytes ‖ canonical install-settings JSON)` is what it ran
under.

**`env_version` is not part of `definition_version`, and that is the whole
shape of the model.** An environment moves for reasons that have nothing
to do with any particular output — one `uv add` for one plotting script
rewrites the lock for the entire project — while a research artifact costs
hours to remake and has usually already been looked at. So an environment
edit stales nothing; it makes an output **behind**, which is reported and
left alone. (This reverses the original design, where `env_version` sat
inside `code_version` and therefore staled every output in the project on
any dependency change, and every output in every project on an lc upgrade.
That was recorded as an accepted cost; it was the bug.)

Nothing is lost by not rebuilding: the manifest records the environment
*and* the commit, and that commit's `uv.lock` reconstructs the project
environment exactly. (The engine is not in that lock — its reconstruction
is the run record's job, and its version the manifest's `lc_version`.)
Over-sensitivity in `env_version` is affordable precisely because it no
longer spends compute.

**Both are length-framed.** Concatenating fields raw lets a boundary shift
between them produce one digest from two different inputs.  `_frame`
writes label, length, then bytes. The test lives on `env_version`, where a
shift is actually constructible — two raw file bodies, adjacent — rather
than on `definition_version`, whose second field is canonical JSON and
cannot be shifted into. Mutation-checked: breaking `_frame` fails it.

**The lock's raw bytes, not a parse.** A comment reflow moves
`env_version`, deliberately: the alternative is a parse of our own that
can silently disagree with uv about what the lock means, and
over-invalidation is the failure that costs time rather than correctness.

**The install-settings list is closed** (`_INSTALL_SETTINGS`), and every
key is hashed whether or not the project sets it. A setting outside the
list must not move the hash, or every uv config nicety stales the world;
a setting whose value merely *matches* today's default must, because that
default can change under a project that never said anything.

**The settings are read where uv reads them, and `uv.toml` *replaces*
`[tool.uv]`** rather than merging with it (measured, uv 0.12.5 — uv warns
about the pair itself, and `tool_warnings()` already lifts that into the
report). Reading both would hash settings uv is ignoring, reporting two
environments where uv installs one; reading only `pyproject.toml` left a
`uv.toml` free to change what gets installed without moving `env_version`
at all. Only the *values* are hashed, never which file supplied them —
two projects that install the same artifacts are one environment however
they spell it. `scan_lock` reads `default-groups` through the same
function, because it is asking uv's question too.

What this deliberately cannot reach is **user-level configuration**
(`~/.config/uv/uv.toml`), which uv merges in underneath the project's own
(measured). That is machine state, not project state: hashing it would
make one commit answer differently on two hosts, so a colleague's clone
would report every output as behind. The residue is real and unguarded —
a user-level `no-build` changes what a sync installs and nothing records
it. **There is no flag that closes it**: `--config-file` refuses a
`pyproject.toml` outright, and both `--config-file <empty>` and
`--no-config` drop the project's own `[tool.uv]` along with everything
else (measured; `--no-config` also drops `[tool.uv.index]`, which is the
GPU mechanism, so adopting it would break working projects). Tracked as
issue #176 with the options and the measurements; don't re-derive them.

**The git commit is recorded, never hashed, and never a signal.** It goes
in the manifest so the code that produced a result stays recoverable. It
is out of `definition_version` because git has one sha for the whole
tree — hashing it would stale every output in the repository on a README
edit — and it is not a `behind` trigger for the same reason: it moves on
every commit, and a signal that is always on is not one.

The honest consequence, which is a **decision and not an oversight**:
editing `src/fit.py` remakes nothing, because the recipe *string* is
unchanged. Per-output code invalidation is available by declaring the
source files as ASTRA inputs, and that declaration is deliberately the
researcher's to make rather than something lc infers — for an expensive
output, "the code moved and the result still stands" is a legitimate and
common position. Do not add a heuristic that scans a recipe's command line
for repo paths.

**Names are compared in PEP 503 form.** uv writes the normalized name
into `uv.lock`; `pyproject.toml` carries whatever the author wrote, and
`project_name()` keeps `_` and `.`. Raw comparison makes a packaged
project called `my_project` fail to recognise *itself*, and the lock scan
then refuses the whole run over the project's own code.

**The lock scan refuses only what cannot be audited.** A path, directory,
or editable dependency records *where* it was rather than *what was in
it*, so two syncs of one lock can install different code while every hash
agrees they are identical — that is a refusal. A registry package with no
wheel is a *report* (identity covers the sdist, not the build of it), and
a non-default dependency group is *advisory*. The project's own package
is exempt: it is the project, and the repository already records its
bytes.

## Key Invariants (layer 4)

**What the spec *means* is ASTRA's to say, and `plan.py` asks rather than
re-derives it.** `astra.resolve` settles each universe's decisions,
resolves every output's inputs to what supplies them, drops the outputs
whose `when:` does not hold, and renders the recipe grammar. Scoping,
`from:` aliases, sub-analysis nesting and the placeholder grammar are all
*read* here, never re-implemented — a second implementation of one
specification is how the two start disagreeing, and ours had:

- it could not build `examples/iris_pipeline` at all, ASTRA's own
  canonical nested example;
- it ignored `when:` on an output, so a universe ran a recipe the spec
  excludes and committed a manifest for it;
- it invented a dotted input id (`inputs: [hod.mass_function]`) and an
  implicit "same universe id" fallback for sub-analyses, neither of which
  `astra validate` accepts — `UniverseNode.universe` names it explicitly.

`lightcone.engine.plan` therefore holds only what execution adds:
`Task`, `Graph`, and the mapping of resolved outputs onto directories,
edges and `definition_version`. When something about the spec's meaning looks
wrong, the fix is in astra-tools, not here.

**A spec ASTRA rejects never reaches a recipe.** `build` runs
`validate_analysis_schema`, `validate_analysis_file` and
`validate_universe_file` before resolving anything, and refuses with
ASTRA's own errors. This is a contract requirement, not a courtesy:
resolution answers what a *valid* spec means and does not re-check that
it is one, so without the gate an invalid spec surfaces later as a
missing decision or an unresolvable input — blaming the run for a fault
in the file, far from the line at fault. It caught three of lc's own
test fixtures the first time it ran.

**The layout is flat and path-addressed.** `results/<universe>/<output_id>/`,
`data/` for declared inputs, and the path in a rendered recipe *is* the
path on disk — no staging, no scratch, no relocation. The `output_id` is
ASTRA's **qualified** id, so a sub-analysis output lands at
`results/<universe>/<analysis>.<output>/` and one addressing scheme spans
however deep the spec nests. Nesting is not capped: the dot separator is
unambiguous because ASTRA ids match `^[a-z][a-z0-9_]*$`.

**One rule names a path, and both the recipe and the run record use it**
(`plan.declared_path`). Project-relative inside the tree, absolute
outside it, never resolved. A declared input may name an absolute
`source:` — ASTRA allows it and an HPC project pointing at a shared
catalog is the obvious case — and there is no project-relative spelling
for one, so a bare `relative_to` was a `ValueError` traceback out of
`lc status`, `--check` and `materialize` alike. Two copies of this rule
existed; the second is what made the first easy to miss.

An input outside the project is **reported, not refused**: its bytes are
hashed into the manifest like any other, so a change to it still
cascades, but it is not in the repository and the commit recording the
output cannot bring it back. That is a weaker promise than the rest of
the layer makes, and saying so is the whole obligation — the same
treatment `sdist_built` gets.

**Two universes cannot share an id.** The id names a directory under
`results/`, and the graph is keyed on `(universe_id, output_id)` — so the
second file simply replaced the first and one universe's outputs went
missing with nothing said. The way in is the natural one: copy
`baseline.yaml`, edit the decisions, forget the id inside. `build`
refuses, naming both files.

**Because the path is composed, `output_dir` refuses an id that is not one
path component.** An empty universe or output id collapses
`results/<u>/<o>` onto a *parent* — `results/` itself, for two — and the
worker empties that directory before running a recipe in it, so the
consequence of an unchecked id is deleting every other universe's
outputs. A `/`, `\`, `.` or `..` is refused for the same reason. This is
the guard that lets the reset stay a whole-directory operation.

**The reset takes the whole directory, and cannot take a named list.** A
recipe declares an output *id*, never filenames, so there is no set of
"expected files" to remove — and a previous run that crashed can have left
anything in there, which would otherwise survive into this run's
`data_version` as though the recipe had written it. What bounds the blast
radius is the guard above, not a narrower delete.

**Dask owns the ordering.** Every task is submitted with its upstream
futures as arguments, so the dependency order, the parallelism, and the
scheduling all fall out of the argument graph. There is no ready-set loop
and no hand-rolled topological sort in the execution path.
`Graph.order()` exists for the read-only walk, which has to classify a
task after everything upstream of it — and for submitting in an order
where a task's upstream handles already exist.

**The driver owns git, alone.** Workers execute and return a
`TaskResult`; the driver commits, in one thread, as results arrive.
Concurrent git operations on one repository race on the index lock —
this is the `datalad-slurm` schedule/finish split, and it is not a
preference.

**A dependent does run while its upstream is being annexed, and that is
safe.** Dask releases a task the moment its upstream's *worker* returns,
milliseconds before the driver finishes committing that upstream's
directory — so a recipe reads an input directory while git's clean filter
is moving its content into the annex. Measured before relying on it: git-annex
hard-links the content into the object store and then renames the symlink
over the file, so the path never stops existing and never holds partial
bytes (448 concurrent full-content reads across 24 MB: no missing paths,
no short reads, no wrong bytes). Don't "fix" this by moving the save into
the task — that is what puts git back in the workers.

**A declared input is hashed once per run** (`assets.Versions`). Without
it a multiverse spec re-reads the same bytes once per `(universe,
output)` that names it — eight universes times four outputs sharing one
catalog is thirty-two full hashes of one file, paid again on the
"nothing to do" path because classification needs the digest before it can
skip. Memoizing is sound for exactly as long as a run lasts: a run
refuses to start on a dirty tree, and the only in-tree path a recipe may
write is its own output directory. A class rather than a closure, so it
keeps one dict alive and not whatever scope built it; and deliberately
unlocked, because a lock would serialise every hash and would not survive
being handed to a worker in another process.

**HEAD is read once per run, by the driver, and handed down.** The driver
commits each output as it lands, so HEAD *moves* during a run: a
per-task `dataset.head` would stamp later manifests with a commit this
same run created, and whether it did would depend on whether a recipe
finished before or after the previous save. Nondeterminism in a
provenance field is worse than either answer.

**The worker never raises, and that is enforced at the unit boundary.**
It returns `ok`, `current`, `behind`, `failed`, or `blocked`. A task whose upstream
did not report — failed, or never finished at all — returns `blocked`
without running. Raising would make Dask re-raise in the driver and abort
every task in flight, and reporting all independent failures in one run
is most of what owning the loop buys. `worker.materialize` wraps the
whole unit, so the contract holds for failure modes nobody enumerated;
the one inner guard that remains exists because "your recipe failed" and
"your recipe worked and we could not record it" deserve different words.

**`data_version` is computed in the worker, before anything is staged.**
The dependent's argument *is* the upstream worker's return value, so the
digest has to exist at return time — when the files are still untracked
and unannexed. Deriving it from `git annex find` instead was the first
instinct and is wrong twice: nothing is annexed yet, so every output would
record `sha256([])` — one constant, silently disabling the whole chain
with green tests — and it would make the digest a function of
`annex.backend`. The annex backend is therefore not load-bearing and is
not pinned.

**A skip returns the *recorded* digest, never a recomputed one.** On a
clone that has fetched no annex content the files are dangling symlinks,
and rehashing them would quietly report a different output. `--check`
obeys the same rule for the same reason — it reads each unchanged
upstream's manifest rather than hashing the directory, or it would report
a rebuild for a project that is entirely up to date. The honest
consequence: a hand-edited-and-committed output does not cascade in this
layer. Catching that is `lc verify`'s job.

**Three states, and the line between them is the layer's whole shape**
(`assets.classify`).

| state | means | what happens |
|---|---|---|
| `stale` | the artifact **contradicts** the project: the spec defines it differently than it was made, or it records deriving from bytes the project no longer holds | remade |
| `behind` | it is still exactly what the spec asks for; only the **environment** moved | reported, left alone |
| `current` | neither | nothing |

The distinction is *contradiction* versus *circumstance*. A stale artifact
is mislabelled — what is on disk is not an instance of what the spec
declares — so keeping it would be a lie. A behind artifact is not wrong in
any way; its environment is recorded and its commit reconstructs that
environment, so remaking it buys nothing and can cost a week of
allocation.

**`Verdict.calls_for_a_remake(refresh=)` is the one place that turns a
state into an action**, and it has three callers — the worker, `check`,
and the walk that feeds the cascade. `stale` always; `behind` only when
asked. Do not re-spell it inline; the third copy is where they start to
disagree.

**One classification rule, and the two callers differ by one value**
(`assets.classify`). It compares `definition_version` against the
manifest, the declared input *set* against the recorded one, each recorded
`input_versions[…]` against the version it is handed, and finally
`env_version`. The set comparison is separate on purpose:
`definition_version` hashes the recipe and the decisions, neither of which
an input the spec no longer declares moves — so without it a dropped
dependency leaves the output reporting current forever. The worker hands
live digests; the read-only walk hands `None` for anything it has already
decided will run, meaning "this is going to change". That single value is
the entire difference between them — one input, conservatively chosen, not
a second body of logic — the same discipline as layer 1's
`converge(write=False)`. It is the one place in the layer where a bug is
quiet rather than loud, which is exactly why it may not have two
implementations.

**`stale` wins over `behind` when both apply.** The artifact is going to
be remade either way, and reporting "left alone" about something the run
is about to rebuild is the one wrong answer.

**`behind` does not propagate, and a behind upstream still feeds its
dependents.** It says the environment moved, not that the bytes are wrong,
so `TaskResult.usable` includes it and `data_version` flows on unchanged.
Propagating it would mark one old artifact's entire downstream forever and
kill the signal; the mosaic — how many environments and commits an output's
ancestry spans — is a project-level question, not a per-output flag.

**`--refresh` widens a run by exactly one state.** It is not an escape
hatch (it asks for *more* work, not less), and it must not become a
rebuild-the-world flag: a `current` output stays current under it. There
is deliberately no flag in the other direction — nothing suppresses the
rebuild of a stale output, because deleting the directory is the user's
own file operation and is stronger consent than a flag.

**`up_to_date` does not count `behind`, and is not true of a run that
failed.** `lc materialize --check` is a gate, and a project of curated
results would otherwise never pass it again — that is the `behind` half.
The other half is that `made` stays empty when *every* recipe fails, so
`not made and not planned` reported "nothing to do" over a list of
failures. It is `ok and not made and not planned`; those two are the
first keys of the JSON report and are what an agent branches on.

**`lc status` reports; `--check` gates.** Status always exits 0 — a state
is not a failure — and it is the only verb that shows the commit each
output was made at, for every state and not only the interesting ones.
It reads manifests and hashes inputs; it runs nothing, commits nothing,
and does not mind a dirty tree, because the moment you most need to know
what state a project is in is when it is not clean. Two verbs answering
the same question with different exit codes is how a script comes to
depend on the wrong one, so keep the split sharp.

**A read-only verb never tracebacks, and an entry point never
misattributes.** `lc status` and `--check` read projects that are *in a
state* — that is what they are for — so anything `_predicted` cannot read
becomes the same `None` an absent input already produces: it will be
remade, and the recipe is where that failure belongs with a real error.
(The concrete way in: `data_version`'s directory walk keeps dangling
symlinks deliberately, so an unfetched annexed file cannot drop silently
out of a digest — one that is *not* an annex link then reaches `open()`.)
The same rule at the other end: `worker.main` is what every
`[DATALAD RUNCMD]` record names, and its "no output `<x>`" message covers
the task lookup **only**. It once wrapped the whole body, so a `KeyError`
raised anywhere inside astra's validation or resolution was reported as a
bad target — a rerun misdiagnosing itself, at the one place nobody is
watching.

**`git_sha` in a manifest is the commit the run *started* at**, not the
commit the run went on to create. It is the code that produced the output.
A test that reads `dataset.head()` after materializing and expects a match
is asserting the wrong thing.

**One *project* uv hop, one spelling** (`project.uv_prefix(root, *,
sync)`). The only thing its callers disagree about is `sync`: a probe
converges the environment it is about to describe, a recipe must not, or
every concurrent worker writes the same `.venv`.

The run record's `cmd` is the deliberate second shape, and it is not the
drift the rule guards against: it is *project-less* by construction
(`uv run --no-project --with lightcone-cli==<v>`), so it shares no flag
with `uv_prefix` — no `--project`, no `--locked`, no sync selection,
because there is no project environment involved. It builds an engine to
run, where `uv_prefix` enters an environment already built. Routing one
through the other would mean a helper with two disjoint output shapes.
What keeps *that* hop from drifting is that the worker it invokes
converges the project environment itself, so the record never has to
spell how.

**A run syncs the environment; it does not report on it.** `uv run
--locked` asserts only that `uv.lock` still matches `pyproject.toml`, and
workers pass `--no-sync` — so a lock edited without a sync would leave
recipes importing packages the lock does not describe while every manifest
recorded the *new* lock's `env_version`. Measured: the recipe imported
`packaging 26.3` under a lock saying `24.2`, and uv accepted it silently.
`materialize()` converges right after the dirty check — before the graph
— so the state is made impossible rather than detected. (The dirty check
moved *in front* of the converge with layer 6: in containerized mode the
converge can commit an image archive, and `dataset.save` stages scoped
but commits the whole index, so on a dirty tree the user's staged edits
would be swept into the image commit. Direct mode is order-insensitive —
its sync touches only ignored paths — so both modes run one order.)
`--check` needs neither: `env_version` is the lock's bytes, so a drifted
`.venv` cannot change what it answers.

**A run fetches its declared inputs; the read-only verbs never do.**
`materialize` batch-runs `git annex get` over the graph's in-tree
declared inputs before anything hashes (driver-side — the storage
invariant that nobody is ever asked to run an annex command by hand),
so a bytes-free clone materializes straight to up-to-date. A failed
fetch is a *warning*, never a refusal: independent tasks still run and
the task whose input is unreachable reports its own failure. `--check`
and `status` stay transfer-free — there an unfetched input is a
reported fact, and the report now says `lc materialize` resolves it.
Out-of-tree inputs are not fetched (no annex holds them — the recorded
weaker promise), and `test_check_mode_never_fetches` pins the read-only
half.

**A run takes every core, and there is no flag to say otherwise.** How
much of a machine a run may use — and which machine — is one question, and
it belongs to a declared execution backend rather than to a `--jobs` knob
only a `LocalCluster` could honour.

**A dirty tree is a refusal, and `--check` is exempt.** Every
materialization is committed with the code that produced it, so a run that
started dirty could not say what that code was. Check mode does not
refuse: reading the state of a project before deciding what to commit is
what it is for. The refusal splits by path class because the remedies are
opposite — work the researcher owns gets committed, and anything under
`results/` is lc's to write and is wreckage to discard.

**A run leaves the tree exactly as clean as it found it.** The worker
resets the output directory *before* executing, so a failed recipe, a
crash, or a Ctrl-C would otherwise leave tracked files deleted or
half-written — and the next run's refusal would tell the user to commit
truncated, manifest-less garbage into `results/`, destroying the one
property the layer exists for. So `ok` → `dataset.save`, and `failed`,
`blocked` or never-reported → `dataset.restore`, with the consumption
loop in a `try/finally` so an interrupt restores whatever is still
outstanding. This is what makes the dirty-tree refusal survivable rather
than a trap.

**The run record names declared paths, never resolved ones.** Every
declared input under `data/` is an annex symlink, so a `Path.resolve()`
in the record's `inputs` writes `.git/annex/objects/SHA256E-…` — the
storage rather than the input, and a path nothing can `datalad get`. This
shipped once; `plan.declared_path` is lexical now, never resolved.

**The run record is genuinely re-runnable, and every record pins its
engine.** `uv run --no-project --with '<requirement>' -- python -m
lightcone.engine.worker <universe>/<output_id>` — an ephemeral
environment that reconstructs the *engine*, while the worker itself
reconstructs the *project* environment from the rerun commit's own lock
(`main()` syncs before anything executes; `uv run --no-sync` against a
missing `.venv` silently creates an empty one, so a clone's rerun would
otherwise run recipes in a bare environment under a manifest recording
the lock's `env_version`). The bare recipe would reconstruct nothing lc
adds (no locked environment, no boundary, no gates, no manifest) and
would commit bytes the identity model never produced; `lc materialize`
cannot be it either, because `datalad rerun` removes the declared outputs
first and that dirties the tree materialize refuses to start from.

The requirement (`materialize._engine_requirement`) pins a release by
version and a dev build by its source commit — hatch-vcs embeds the
commit in the version, and the repository URL comes from
`[project.urls]`, the engine's own metadata rather than a constant. So a
rerun works during development too, against the commit that ran
(verified against GitHub: uv resolves the short sha and hatch-vcs builds
the matching version from the clone). An unpushed commit fails a rerun
loudly at resolution, which beats silently finding another engine; a
dirty tree pins the last commit, and the version's `.dYYYYMMDD` marker in
the manifest is what says the bytes had drifted from it. The e2e rerun
tests monkeypatch the requirement seam to a wheel built from the working
tree (`UV_FIND_LINKS`), because a git pin can only ever build *committed*
code and the suite must execute the code under test.

**The worker module is not an `lc` verb and not a console script.** It
makes the output unconditionally, commits nothing, leaves the tree dirty by
design. `lc --help` advertising it would hand people a footgun, and a
`[project.scripts]` entry would put it on `$PATH` through
`uv tool install`. `lightcone/_sandbox_exec.py` is the same shape for the
same reason. Keep it cheap to import — **no click, no rich** — it is on
the path of every task and every rerun.
`test_the_worker_module_imports_neither_click_nor_rich` pins the imports
and `test_help_does_not_advertise_the_worker` the absence from `--help`;
nothing pins the absence of a `[project.scripts]` entry, so treat that
as a review item.

**The record's format is datalad's, so it is tested through datalad.**
`get_run_info` matches with a regex and returns `(None, None)` on any
mismatch, after which `rerun` says "no command; skipping" and **exits 0** —
a golden test over our own JSON would stay green through a silent break.
So the suite asserts through datalad's parser *and* runs a real
`datalad rerun`, and `datalad` is a **dev** dependency only. Nothing in lc
imports it.

**A policy describes the project; it never prepares it.** `exec_policy`
creates nothing in the project tree — the worker resets the output
directory, so the whole of an output's lifecycle stays in the module
that owns it, and `results/` is granted only if it already exists. The
one thing it does put on disk is the per-run private `$HOME`
(`mkdtemp`), and that is why `sandbox.scope` takes a *built* policy: the
`rmtree` of that directory has one owner. (`wrap` stays pure; the
impurity lives in policy construction, once.)

**There is one policy, `exec_policy`, and a recipe gets exactly what a
probe gets.** The tree is read-only apart from `results/`, for both. So
layer 5's promise is not "in the direction that matters" — it is simply
true: a command that works under `lc run` works as a recipe, with nothing
in between to reason about.

A recipe is deliberately **not** narrowed to its own output directory.
That would be a second answer to "are these bytes what produced them",
and the manifest's `data_version` is the first — content-addressed,
checked by `lc verify`, and the only one that survives a rebuild on
another machine. Two mechanisms for one guarantee is one more than can be
kept honest, and the sandbox's is the one that cannot travel.

The residue, recorded rather than argued away: a cross-write that lands
*before* the victim task hashes leaves a manifest that is self-consistent
and wrong, which no checksum can see. It needs concurrent tasks and a
hardcoded sibling path, and the threat model here is accidental leakage
rather than a hostile recipe — but `lc verify` will not catch that one, so
do not describe it as covered.

**`cluster_for_run()` is the seam, and it is two methods wide.**
`submit(fn, *args, key=…)` and `completed(handles)`. That is all the
driver asks of a scheduler and all a venue has to supply — which is what
lets the suite run a graph inline in-process, and what will let something
larger than a laptop land behind it without the driver noticing. A venue
owes one thing beyond the two methods: its worker processes must run an
interpreter that imports `lightcone.engine` at the driver's version —
see the engine-is-the-host's-uv-tool decision for how each venue
provides that, and for what workers do *not* need (git, git-annex).

### Recorded deviations from the spec (layers 2 and 4)

- **`git_dirty` is not written.** Spec §3 lists it; the start-of-run
  refusal makes it constant. The limitation that comes with that, stated:
  the check is at start of run while manifests are written per-output much
  later, so a user who edits `src/fit.py` while a long graph runs gets a
  manifest whose `git_sha` no longer describes the code that ran, and
  nothing records it.
- **The manifest carries what this layer can honestly fill.**
  `schema_version`, `output_id`, `universe_id`, `recipe`,
  `definition_version`, `env_version`, `data_version`, `decisions`,
  `input_versions`, `git_sha`,
  `git_remote`, `lc_version`, `hermeticity` — and, since layer 6,
  `image` (`{tag, id, archive, arch}`, defaulting to ``None`` because
  that is the true value for a host run — not back-compat machinery,
  and `SCHEMA_VERSION` stays 1 pre-release), and since layer 8
  `started_at` / `finished_at` (ISO 8601 UTC, **millisecond** precision
  because RO-Crate consumers parse `endTime` with at most three
  fractional digits; attestation like `lc_version`, defaulted `""`,
  never read by `classify`). Spec §3's longer list —
  `uv_version`, `platform`, `worker_runtime`, `python_build`,
  `dpkg_snapshot_sha256`, `sdist_built`, `env_snapshot`, `gpu_driver` —
  is attestation nothing here reads; it lands with the verb that reads
  it (`worker_runtime` is additionally derivable from
  `hermeticity.mechanism`, so it may never land at all).
- **`env_version` has four terms.** Layer 6 added the image term —
  the system layer's identity document, hashed as the literal `null`
  for a direct project so the formula stays one formula. (The spec's
  separate `Containerfile.extra` term went with the concept; see the
  layer-6 decisions.)
- **A recipe is handed to `bash -c`.** ASTRA recipes are command lines,
  not argv, and `bash` is in the exec allowlist — so it is granted by the
  same rule that grants everything else a recipe may run.

## Key Invariants (layer 5)

**The seam is a pure argv rewrite.** Every mechanism is a
`Backend.wrap(policy, argv) -> argv'` — a function turning a command into
*a different command that sandboxes itself*. Seatbelt is natively that
shape; Landlock is not (it is a self-restriction), which is exactly what
`lightcone/_sandbox_exec.py` exists to fix. Because both reduce to argv,
`boundary.run` never branches on platform, and **every backend is fully
testable on a host that cannot run it** — `tests/test_sandbox_wrap.py`
checks the Landlock wrap and the generated SBPL on whatever machine is
running, with no privileges. Keep `wrap` pure: no temp files, no file
descriptors, no global state. `tests/test_sandbox_wrap.py::test_wrap_is_pure`
pins it.

**`detect()` in `sandbox/boundary.py` holds the only `sys.platform`
branch.** Everything downstream branches on `Capability.kind`, a *value*.
Adding bubblewrap or podman later is one module plus one line there.

**Three types, deliberately distinct** (`sandbox/model.py`): `Policy` is
*what we will enforce* (mechanism-free path sets), `Capability` is *what
this host can do* (the probe's answer), `Attestation` is *what was
actually enforced* (spec §7's manifest field, derived from the flags
applied — never from what the mechanism matrix says should have
happened). Collapsing any two of them is how a sandbox starts lying.

**`Unavailable` is a real backend, not a special case.** It satisfies the
protocol, wraps to the argv it was given, and attests `fs: open`. Saying
so is the caller's job; pretending is nobody's. There is deliberately
**no flag surface around the sandbox** — no opt-out, no require, no
debug dump. Enforcement always happens where a mechanism exists, and a
host without one gets the downgrade note, not a choice.

**A denial is never invisible.** `denial.explain()` is a best-guess
heuristic over the child's stderr and is *allowed to return nothing* —
a command can swallow the `PermissionError` or rewrap it. That is why
`denial.trailer()` fires on **every** nonzero sandboxed exit,
unconditionally. Both fallbacks are pinned in `tests/test_sandbox_denial.py`.

**The exec tier is per-file, and read ≠ execute.** `/usr` is readable (the
dynamic linker needs it) and never executable — `/usr/bin` holds `bash`
and `latex` alike, so a directory grant there would admit every
undeclared tool on the host and leave the layer enforcing nothing.

**Three things are load-bearing and non-obvious**, each found by a real
failure and each now pinned by a test:

| Grant | Without it |
|---|---|
| the realpath'd **ELF loader** in the exec set | *every* dynamically linked binary fails EACCES — bash and python included |
| **read** on the uv-managed interpreter's install root | `Failed to import encodings module` — the stdlib sits beside the binary, outside the project and outside `/usr` |
| **read** on `/dev/urandom` | `failed to get random numbers` — CPython seeds hash randomization before `main` |

**Never grant EXECUTE on a directory that could be a system prefix.**
Landlock unions rights over ancestors, so a single EXECUTE grant on
`/usr` silently outranks the entire per-file allowlist and leaves the
layer enforcing nothing — while every test still passes, because the
allowlisted binaries are exactly the ones that were going to work. This
shipped once: the venv interpreter's *install root* was granted EXECUTE
unconditionally, which is `/usr` for any venv built on a system python
(`uv venv --python-preference only-system`, most CI images, HPC site
pythons). The rule now: the interpreter **file** always; its install
root gets EXECUTE only when it is the interpreter's *own tree* (a
uv-managed store, a framework version directory — macOS framework
builds re-exec into `Resources/Python.app`), never a `_SHARED_PREFIXES`
member; and READ goes to the root regardless, for the stdlib.
`test_a_system_interpreter_does_not_make_the_whole_prefix_executable`
pins it.

**The env overlay belongs to the seam, not to the parent and not to each
backend.** `boundary.env_argv()` composes `policy.env` into an `env K=V …`
prefix *inside* the wrap, once, for every mechanism — never merged into
the `subprocess` environment, because everything *outside* the rewrite
must keep the real one. `uv` resolves its cache from `XDG_CACHE_HOME` and
its managed interpreters from `XDG_DATA_HOME`; overlaying those for the
`uv run` hop points it at a throwaway `mkdtemp` that `scope()` then
deletes, so every probe re-downloads the world, air-gapped hosts fail
outright, and `~/.config/uv/uv.toml` and `~/.netrc` go missing.

Two rules, and the second was learned the hard way. **If `prefix` is
outside the boundary, nothing the boundary imposes may reach it.** And
**anything every backend must do belongs to the seam, not to the
backends** — while each applied its own overlay, `Unavailable` applied
none, so an unenforced run silently got a different environment as well
as no enforcement. A mechanism added later cannot forget what it never
had to remember.

**The shim stays alone.** `lightcone/_sandbox_exec.py` imports nothing but
the stdlib and nothing from lightcone — `lightcone` is a namespace package
with no `__init__`, so `python -m lightcone._sandbox_exec` executes that
file and nothing else. It runs on every sandboxed exec; an engine import
there would put click, rich, and the astra stack on that path. Two tests
pin it. Its setup failures exit the reserved code **97**, distinguishable
from anything a command could return, and it never falls through to
running the command unsandboxed.

**Layer boundaries showed up as parameters we did *not* add.**
`writable_project`, output-dir write scope, and scratch dirs belong to
later layers and are absent, not stubbed. (Layer 6 then landed the
`podman`/`docker` attestation values and the containerized policy shape
— as one keyword on `exec_policy` and one backend, not as the stubs this
rule kept out.)

**The macOS profile is vendored, not authored.**
`sandbox/profiles/{base,network,platform-defaults}.sbpl` come from the codex CLI
(itself Chrome-derived), with a provenance header naming the upstream
commit and a single `LIGHTCONE DELTA`. The macOS read baseline is not
derivable from first principles — it is a list of things that break,
found one production failure at a time: `/dev/dtracehelper`, the
`/dev/fd` and pty regexes, firmlink-parent traversal under
`/System/Volumes/Data`, the `opendirectoryd.libinfo` lookup without which
`getpwuid()` raises `KeyError`, `/opt/homebrew/lib`. Keep them
near-verbatim so `diff` against upstream stays the re-sync tool; put our
own rules in the generator, not in the vendored text. The one delta —
upstream's blanket `(allow process-exec)`, which exists because codex
does not restrict exec — is pinned by a test so a re-sync cannot silently
restore it.

**Not controlling the network takes more than allowing sockets.** On
macOS, name resolution and TLS trust go through *mach services*
(`SystemConfiguration.DNSConfiguration`, `configd`, `SecurityServer`,
`trustd.agent`, `ocspd`), which the base's `(deny default)` blocks —
`(allow network*)` opens the socket families and nothing else. That is
why `network.sbpl` is emitted unconditionally: without it the
attestation reads `network: allowed` on a host where every lookup fails,
which is worse than either honest answer. Linux needs no equivalent
because Landlock gates the filesystem only.

**One resolution rule for the utility allowlist**, `policy.utility()`.
Anything that hardcodes a tool's path instead is a second answer to the
same question, and the two diverge the moment a host keeps its copy
somewhere else. `env` is the load-bearing case: the seam execs it to
apply the overlay, so a stale `/usr/bin/env` is not a missing
convenience but a denial on the first exec of *every* run — `/usr` is
readable and never executable. Pinned by
`test_the_env_the_seam_execs_is_one_the_policy_granted`, which points
the search path at a copy, because a test against the real path passes
on this host and ships the bug.

**A derived read root may never be `$HOME` or above** (`_stdlib_root`).
The interpreter's install root is granted READ for the stdlib beside it,
and for an interpreter installed straight into `~/bin` that root *is*
the home directory — silently undoing the private-`$HOME` design, which
is the whole point of the environment overlay. Reading `pyvenv.cfg`'s
`base-prefix` instead does not help: it reports the same directory. The
guard is the fix; failing loudly on a layout nobody uses beats voiding
the guarantee for the people who do.

**The two mechanisms disagree about the write root itself.** Landlock
follows POSIX — unlinking a directory needs write on its *parent* — so a
recipe granted write on its output directory cannot remove that
directory. Seatbelt's `(allow file-write* (subpath …))` covers the
directory node, and permits it. Found by CI, which is the point of
running one suite on both. Nothing depends on either answer (the worker
resets the directory anyway, and a recipe that removes it fails to record
its output), so the asymmetry is documented rather than papered over —
but a test that asserts one mechanism's answer will go red on the other.

**SBPL is last-match-wins; Landlock unions.** This asymmetry decides
where a rule can live, and it cuts both ways. A later `(deny …)` can take
back an earlier allow, which is how `_read_only_guard` takes back write
on the read roots the vendored defaults would otherwise hand out;
Landlock has no equivalent — a narrower rule only ever *adds* — so on
Linux the policy simply never names them.

But the same asymmetry means SBPL does **not** give nesting for free.
Landlock unions, so a writable output directory inside a readable project
tree just works. In SBPL the guard's `(deny file-write* PROJECT)` would
revoke it — which is why `generate_profile` restates the **write tier
last**, after the guard. Get that order wrong and layer 4 materializes on
Linux and refuses on macOS, with the golden test still green because it
only checks that the guard is present. Verified empirically, not assumed.

## Key Invariants (layer 6)

**Mode is derived, never configured.** A `[tool.lightcone.image]` table
in `pyproject.toml` *is* the escalation to containerized mode; deleting
it is the way back. `project.mode()` reads presence only; what the table
*means* is `engine/image.py`'s question, so an invalid declaration
refuses where it is consumed, naming the key at fault.

**The user never sees a Containerfile.** The whole declaration surface is
Modal-shaped TOML — `base` (digest-pinned or the default constant),
`apt-install`, `run-commands`, `env` — a closed set, because every key is
hashed. `pip_install` deliberately has no equivalent: the Python
environment is the lock's business, never the image's, and `run-commands`
is the bounded escape (this deletes spec §2's `Containerfile.extra`).
The render exists only inside a transient build context; the image's
`LABEL io.lightcone.image` carries the identity document, so the archive
stays self-describing without one.

**The engine never enters the image.** The container is the *recipe's*
execution world: driver, git, annex, dask and classification all stay the
host's `lc`, and exactly two things ever run in-image — the environment
converge (`container.sync`, network on, project `:rw`, host uv cache
mounted, into `.lightcone/venv`) and each recipe/probe exec
(`--network none`). This deviates from spec v6.1's full-stack rule,
recorded: v6.1's reason was the host-sync deadlock, which the
in-container sync solves, and the spec's own Perlmutter row ("recipe
wrap, step 3 only — never the dask worker") is this exact shape. What it
buys: no delegation machinery, no git through a `--userns=keep-id` bind
mount, no engine version in the tag, and one engine per run by
construction. The host `.venv` is inert in containerized mode
(`current_project` does not require it; `lc init` converges no
environment — a host sync of a containerized lock is the deadlock in
miniature); `.lightcone/` is gitignored machine state.

**The image is the system layer only.** Base + apt (only when
`apt-install` is nonempty — the engine needs nothing from apt, so spec
§2's rule is restored) + the pinned uv + the pinned interpreter into
`/opt/python`. No git, no engine, and **no project file ever enters the
build context** — the context is a scratch directory holding one rendered
Containerfile, which is what makes "code edits never trigger a build"
structural. `bash` is a base-contract check (recipes are `bash -c`), and
each contract violation is a reserved exit code mapped to a refusal
naming the base (43 musl, 44 no bash, 45 no apt), never a raw build log.
The readability `chmod` rides inside the layers that write `/opt` — a
layer of its own would copy-on-write the whole interpreter tree into
every archive, doubling it.

**The dataset is the image store; runtime stores are caches.** `lc build`
saves the image as a `docker-archive` at
`.datalad/environments/<tag>/image` — the `datalad containers-add`
layout — annexed and committed, so the exact bytes travel through
`git annex get` with no registry and no credentials. This supersedes the
spec's `dpkg_snapshot_sha256` residue: apt is name-pinned, so a rebuild
months later yields different bytes under the same tag, and the archive
keeps the bytes themselves. The dot-path is the trap:
**git-annex routes dotfiles to git whatever `annex.largefiles` says**,
so `dataset.save` opts in unconditionally (`annex.dotfiles=true`,
per-add like `annex.thin`) — without it the archive, or a `.cache.h5` a
recipe writes into its output directory, lands as a full blob in git,
silently, with every test green. The attributes alone decide from
there (dot-named manifests keep their own exemption); a user's plain
`git add` keeps git-annex's stock behavior. `test_dataset.py` pins both
directions. And because `.gitattributes` is user-authored and lc only
appends, the build **probes the routing with `git check-attr` before
saving** — an archive the attributes would hand to git is a refusal
naming the line, the same probe-don't-assume rule as the `results/`
ignore check. The archive
is the arch it was built for (recorded in the manifest); multi-arch is a
layer-7 item, as is apptainer/singularity — which is *why* the format is
`docker-archive`: all four runtimes consume it, and HPC hosts that
cannot build obtain images through the repository.

**`runtime_for_run(root, *, build)` is one function with two
strictnesses**, and the split
is load-bearing: `lc build` and the materialize preflight may **build +
save + commit** (announced by the CLI, which owns the console — the
engine never prints); the probe and the worker entry point only ever
**find** — a missing archive refuses naming the exact `lc build`
(`lc run` never builds; the worker never commits), unfetched content is
**fetched by lc itself** (`git annex get`, driver-side — the storage
invariant is that nobody is ever asked to run an annex command by hand,
so the refusal is reserved for a fetch with no reachable copy), and an
unloaded image is a silent `<runtime> load`. Execution pins the **id** — sha256 of the
archive's config blob, readable with no runtime, the same computation
datalad's docker adapter makes — never a tag, so a retagged store image
cannot substitute. **A dropped archive never substitutes**: a rebuild is
a new archive commit under a new id; old manifests keep naming what ran.

**Builds and the archive commit happen only on a clean tree, and only
after the graph.** The dirty check runs before `runtime_for_run` in
materialize, and `lc build` refuses dirt itself: `dataset.save` stages
scoped but commits the whole index, so a build on a dirty tree would
sweep the user's staged edits into the image commit — and the tag
derives from `pyproject.toml`, so the declaration must be committed
before the image it defines. The graph (spec validation, the lock scan)
resolves before the image too, in materialize and in the worker entry
point alike: a refusal over a typo must not cost a minutes-long build or
leave an archive commit behind a failed run, and a failing sync must not
bury "no output `x`".

**Two identities, one document.** The canonical identity document
(declaration resolved + the uv digest; the interpreter pin deliberately
absent — its raw bytes are already an `env_version` frame) feeds
`env_version` as its fourth frame, `null` for direct projects — so
declaring an image, or an engine release that bumps the default-base or
uv constants, puts containerized outputs `behind`, honestly, while
direct projects never move on an engine release. The **tag** hashes the
rendered Containerfile *and* the document, so a render-only generator
change rebuilds the image without staling anything — accepted residue,
attested by the manifest's image id and the archive's bytes. Read from
`pyproject.toml` only, never `identity._uv_config()` — uv's
"`uv.toml` replaces `[tool.uv]`" rule must not reach this table.

**The mount table is the mechanism** (`sandbox/oci.py`). The
containerized `exec_policy` shape is the same policy minus the host: no
OS read baseline, no stdlib root, no exec set — the image *is* those,
and everything in it was declared — leaving exactly the paths that
become mounts, project `:ro`, `results/` `:rw`, declared inputs `:ro`,
the private HOME `:rw`, `--tmpfs /tmp`, over a **`--read-only` rootfs**:
without that flag a write outside the declared set *succeeds* into the
container's ephemeral layer and vanishes while the run attests
`fs: declared` — the silent-loss path, closed by making it a denial.
Mounts are **resolved source, declared destination** — the containerized
policy is the one shape that keeps its paths unresolved, because they
are addresses the recipe uses (a symlinked `/data` input resolved on
both sides would leave the container with no `/data` at all).
`--security-opt label=disable`, because SELinux hosts otherwise refuse
every bind read and `:z` would relabel the user's own files. No
in-container Landlock, no seccomp probe, no shim-in-image: the engine
container never gets the tree `:rw`, so mounts alone express the whole
policy, and the attestation (`mechanism: podman|docker`,
`fs: declared`, `network: denied`) is derived flag-for-flag from the
argv — `--network none` is the codebase's one honest `denied`, loopback
intact. One `OCIBackend`, data-parameterized: podman and docker differ
in spellings (`--userns=keep-id`+`--pull=never` vs `--user uid:gid`),
not shape.
Runtime is **host capability** — detected podman → docker, the
`detect()` discipline on a second axis, with `container.backend()`
holding the only mode branch — and never part of any identity. The
containerized `tmp_home` lives under the project's `.lightcone/` rather
than the system temp dir: it is a mount source, and macOS's podman
machine shares the project tree while `/var/folders` arrives empty.

**The seam learned one asymmetry, `contains_prefix`.** A host mechanism
keeps the `uv run` hop *outside* the wrap (trusted host plumbing); a
backend that is itself a world takes it *inside* — there is no trusted
host plumbing inside a container — and applies the env overlay natively
as `--env` flags, never through `boundary.env_argv`'s host-resolved
`env` binary (NixOS resolves it to a path no Debian image has). Declared
on every backend, so a new mechanism must answer the question. The
container environment is an allowlist: the overlay plus `LC_SANDBOX`,
never the ambient environment.

**The record stays runtime-neutral, and the worker is its executor.**
`cmd` is still the engine-pinned worker — verified against
datalad-container's source before deciding: `containers-run` expands its
`cmdexec` template at *record* time and `datalad rerun` executes the
literal string (the extension has no rerun hook), so a bare-recipe `cmd`
would rerun on the host, unconfined, with no manifest. Instead the
record is declarative — the task id, the commit's own spec, the
committed archive — and the worker re-resolves container *and runtime*
on the rerun host, keeping the sandbox, the gates and the manifest. The
containerized record adds `extra_inputs: [<archive>]`, which stock
`datalad rerun` fetches through the annex before executing — the smoke
suite proves the whole claim on a bytes-free clone. `lc build` also
writes `datalad.containers.<tag>.{image,cmdexec}` so ad-hoc
`datalad containers-run` works for humans, explicitly outside lc's
guarantees.

**The runtime is resolved once per run and handed down** — the HEAD
discipline, a frozen `container.Runtime` through `worker.materialize` —
because resolving per task could answer differently mid-run. The rerun
entry point resolves its own, as it does HEAD: it *is* the driver of its
one-task run. `lc status` gained the three header lines (mode, image
state, sandbox) — repository facts only, no runtime and no network
required, which is where the denial note and the runtime-missing
refusal point.

## Key Invariants (layer 7)

**The venue is detected, never configured, and only in one place.**
`cluster_for_run()` is the whole ladder — a SLURM allocation
(`SLURM_JOB_ID` set) spans every node it was granted, anything else is
the local machine — and nothing outside that function asks where a run
executes. The allocation *is* the resource declaration: the user already
answered every sizing question at `salloc`/`sbatch`, so lc adds no venue
config surface, no report field, and no status line (a venue is host
state, and the status header is repository facts). The venue speaks only
when it refuses, and those refusals carry the job facts (nodes expected
vs connected, srun's exit code). A future submission-model venue
(dask-jobqueue) is one more branch in this ladder plus the config table
it genuinely needs — nothing else changes.

**The allocation branch is `venue.slurm_client()`**: a scheduler in the
driver process bound to `SLURMD_NODENAME` (the default loopback bind is
unreachable from peer nodes), one `srun --overlap --ntasks=<nodes>
--ntasks-per-node=1` launching `sys.executable -m
distributed.cli.dask_worker` — the driver's own interpreter, the tool
env on the shared filesystem, so driver and workers are the identical
installation and `-m` cannot resolve to a different install the way a
PATH-found `dask` can. One worker process per node with
`--nthreads=<cpus>` (tasks block in `subprocess.wait()` with the GIL
released — the local branch's own rationale), `--no-nanny` (srun won't
relaunch either; the nanny logs a spurious death on every clean
retirement), `--memory-limit 0` (the real work is in subprocesses behind
the exec boundary, so Dask's memory manager could only pause workers
over phantom numbers), `--death-timeout 60` (a worker whose driver died
exits instead of holding its node to walltime), and `--local-directory`
on a **literal `/tmp`** — never the project tree, explicit so ambient
`DASK_TEMPORARY_DIRECTORY` cannot point it there, and literal rather
than the driver's `tempfile.gettempdir()` because a site prolog can
scope `TMPDIR` to the node or job step that set it, leaving a
driver-resolved path absent on the allocation's other nodes. The
driver's node
hosts a worker too, and a single-node allocation takes the same srun
path — one path means the venue is exercised on the cheapest allocation.

**The srun child is the one documented exception to `project._run`.**
That seam is run-to-completion capture; this child lives as long as the
run, and its stderr must reach the terminal live (srun's own errors are
the user's to see as they happen). It is a `subprocess.Popen` with the
rationale at the call site, and the fake-srun tests keep the argv
inspectable. Worker connection is a poll loop rather than
`wait_for_workers`, so a dead srun is reported as *its exit code*
immediately, not as a timeout two minutes later; teardown retires
workers first (they exit 0, srun ends silently — killing srun prints
"srun: forcing job termination" on every clean run) and escalates
wait → terminate → kill, bounded, never a hang. A leaked `SLURM_JOB_ID`
with no srun on PATH is a loud refusal, never a silent local fallback —
and the other leak shapes get the same treatment: a non-integer SLURM
count refuses naming the variable (`venue._int_env`), and a
`SLURMD_NODENAME` that does not resolve becomes a `ProjectError` naming
the bind rather than the raw `socket.gaierror` (distributed wraps it in
a `RuntimeError`, so the constructor catches both).

**The login guard is materialize-scoped, table-driven, and comes
first.** `venue.require_compute_node()` refuses iff a known center's
marker is in the environment and `SLURM_JOB_ID` is not (a marker is set
on compute nodes too; the allocation is what distinguishes them),
naming that center's copy-pasteable `salloc` and `sbatch --wrap 'lc
materialize'` commands. The centers live in `venue._SITES` — one row
each: name, marker variable, and the center's own allocation spellings,
**verified against the center's documentation, never guessed** (the
remedies rule). NERSC is the seeded row; supporting another center is
one row, and nothing else moves — the conftest scrub derives its marker
list from the table, and `test_a_new_center_is_one_table_row` pins that
the row alone drives the message. The guard is the first line of
`materialize()`, before even the tool checks: the allocation is the
remedy with queue latency, so the user submits it first and fixes
whatever later refusals name while waiting. The rerun entry point
(`worker.main`) is guarded too — a rerun executes a recipe, and the
record's `cmd` is how recipes reach a login node without `lc` in the
command line. `check()`, `status()` and `lc run` never call it — a login
node is exactly where "where does this project stand" gets asked.

**Containerized runs assume a homogeneous allocation**, and the one
part of that lc can check is *enforced*: a multi-node allocation with a
containerized project **refuses** unless the runtime's image store spans
nodes — `container._SHARED_STORE_RUNTIMES`, a positively-stated fact
like `_PODMAN_FAMILY`, holding podman-hpc alone — because podman's and
docker's stores are node-local: the image loads on the driver's node
only, and every task scheduled elsewhere would fail at `run <id>` with
`--pull=never` forbidding the fetch. The check sits in `materialize()`
before the runtime resolves (a refusal must not cost an image build, so
it asks `runtime_hint()`, letting a wholly missing runtime reach
`runtime_for_run`'s own refusal) and not in `runtime_for_run` (the rerun
entry point shares that, and a rerun is a one-node run wherever it
sits). The rest stays a
recorded assumption: every node offers the same runtime binary and sees
the shared-filesystem project tree. podman-hpc is what makes multi-node
true on NERSC — `migrate` squashes the image to the shared filesystem,
where every compute node runs it.

**podman-hpc is a spelling, not a shape.** It rides the existing
`OCIBackend` (standard podman flags, `--userns=keep-id`,
`--pull=never`), joins the podman family in `uid_flags`, `_loaded`
(`image exists`) and `_build` (`save --format docker-archive`) — the
set stated once as `container._PODMAN_FAMILY` and asked positively at
every site, so a future runtime falls *outside* it by default instead
of inheriting podman behavior through a `!= "docker"` back door — and
adds
exactly one step: `podman-hpc migrate <image-id>` in `runtime_for_run`,
**outside the load branch** — after a fresh `lc build` the image is
already in the store and the load never runs, but compute nodes only see
migrated images. Migrate runs driver-side wherever `runtime_for_run`
runs (login node at `lc build`, materialize preflight, the rerun entry
point); one migrate serves all nodes. Detection order is
**podman-hpc → podman → docker**: a site installs the wrapper precisely
because plain podman's node-local overlay store is invisible to compute
nodes, so where both are on PATH the bare sibling is the broken one.
Presence-only detection (no daemon, no machine); the no-runtime refusal
still names only podman/docker — podman-hpc is site-installed, never a
user remedy. It is build-capable (it wraps `podman build`; NERSC login
nodes are where a matching-arch archive comes from), so all three
runtimes are both build- and run-capable and a capability predicate
would be dead code — it lands with the first run-only runtime.

**The architecture gate refuses before the load.** A wrong-arch
`load` *succeeds* and then dies as `exec format error` deep inside a
recipe — so `_require_arch` compares the archive's recorded arch against
`platform.machine()` (via the closed `_OCI_ARCH` map) before anything is
loaded, naming both arches and the fix: build on a matching host (on
NERSC, a login node), commit, push, pull. Ignorance is not a mismatch —
an archive that does not say, or an unmapped host machine, passes.
Recorded residue: an arm64 mac that *could* emulate an amd64 archive is
refused too (emulation is unattested ten-times-slower execution), and
full multi-arch (arch-suffixed archive paths, `--platform` cross-builds)
remains open, with the venue that makes it real.

**Design headroom for a daemonless runtime (apptainer/singularity),
deferred but load-bearing**: `Runtime` stays facts (root/mode/name/
tag/id/arch), never mechanism — the committed→fetched→loaded ladder
stays name-branched inside `runtime_for_run`, and a store-less runtime's
analogue is a one-time archive→SIF conversion into gitignored
`.lightcone/` cache keyed by the same runtime-independent config-blob
id (the reason `docker-archive` stays the store format).
`container.backend()` stays the single construction point; a future
world-backend is one dataclass in `sandbox/` plus one branch there. A
runtime that cannot deny network must attest `network: allowed` with no
denial flag emitted — no consumer may assume containerized ⇒ denied,
and `_sandbox_line`'s containerized prose is the one place that
assumption lives today. `boundary.run`'s exit-125 note is a
podman/docker-family fact, not a `contains_prefix` fact — it becomes
mechanism-keyed when a non-OCI backend lands.

**Pending the one-time Perlmutter spike** (run `tests/
test_container_smoke.py` on a login node, then one materialize through
`sbatch`; record findings here): `--overlap` and `--cpus-per-task`
behavior inside salloc/sbatch steps; `nidXXXXXX` resolution from peer
nodes (else `--interface hsn0`); `SLURM_CPUS_ON_NODE` on a CPU node
(128 vs 256 hyperthreads); cold-Lustre `distributed` import vs the
120 s worker wait; `podman-hpc migrate` accepting a bare image id and
re-running cheaply; `--network none` on compute nodes (the
`network: denied` attestation hangs on it); podman-hpc `--module`
site-injected mounts vs the honesty of `fs: declared` (the one item
that could add a flag); whether Landlock is in the SLES boot LSM list
(either answer is handled — a host without it attests `fs: open` with
the downgrade note); `git annex add`/`get` timings on `$SCRATCH` and
`$CFS` (the recorded Lustre residue).

## Key Invariants (layer 8)

**The project is the crate, and materialize maintains it — there is no
export verb.** `ro-crate-metadata.json` sits at the project root as a
derived artifact, converged by `lc materialize` the way `uv.lock` is:
`engine/crate.py` renders it from repository state, the hook
(`materialize._converge_crate`) string-compares it against the tree, and
only a difference is written and committed — alone, in its own trailing
commit, which `datalad rerun` skips harmlessly as "no command". Deposit
is `git archive` / `datalad export-archive` on a repository that is
already a crate; nothing is copied and there is no bundle directory.
The rerun entry point deliberately does not regenerate it (it is one
task's executor, not the driver), so the crate lags until the next
materialize — recorded residue.

**Publication intent is derived, never configured.** A
`[project].license` in `pyproject.toml` turns crate maintenance on —
RO-Crate *requires* a license, materialize must not refuse to run
science over a missing key, and inventing CC-BY-4.0 (the pre-rebuild
default) asserts terms over someone's data. Absent ⇒ no crate and one
report line; removed later ⇒ the file is left (it is in committed
history either way) and the line says it is no longer maintained. The
same shape as `[tool.lightcone.image]` deriving containerized mode.

**The document is a pure function of repository state, and that is what
makes convergence sound.** The clock never enters it: `datePublished` is
the newest manifest `finished_at` (the spec file's own last-commit date
for a never-materialized project) and **must override rocrate's
default**, which stamps construction time — the old exporter carried a
`datePublished` its code never set. Entities are built in sorted order
and serialized with `sort_keys`;
`test_rendering_twice_at_the_same_state_is_byte_identical` pins it. The
render is also injected-pure: `dataset.last_writer` comes in as a
callable and `dsid` as a value, so `tests/test_crate.py` runs with no
git at all.

**Run identity comes free from `git_sha`.** The driver reads HEAD once
per run and hands it down, so every output of one materialize carries
the same sha — grouping manifests by it *is* grouping by run, and that
is what makes the Provenance profile expressible with no new manifest
field: one `OrganizeAction` + workflow-level `CreateAction` per run, a
`ControlAction` per output execution, a `HowToStep` per output id
(deduped across universes: a step is spec structure, an action is one
execution — exactly how the multiverse maps on).

**The crate's `Person` is the author of the output's *saving* commit,
via `last_writer` — never the manifest's `git_sha`**, which is the
commit the run *started* at and can be someone else's. No `--author`
flag, no env var, no config probe: `require_committer` already
guarantees the commit has an author.

**The manifest stays canonical; the crate does not transliterate it.**
`env_version`, `definition_version` and `hermeticity` get no schema.org
spelling — the manifest itself is in the crate as a `File`, `subjectOf`
its output's `Dataset`, so nothing is lost and no vocabulary is
invented. What does get real vocabulary comes from the workflow-run
`@context` (`https://w3id.org/ro/terms/workflow-run`), without which
`containerImage`, `sha256` and `ContainerImage` are undefined terms
JSON-LD drops on expansion — the pre-rebuild exporter's silent failure.
The committed archive is one entity, `["File", "ContainerImage"]`,
identity (`sha256` = config-blob id) and payload together.

**The validator floor is pinned as a set, not a count.**
`tests/test_crate_smoke.py` materializes a real project and runs the
official `rocrate-validator` (dev dependency) against Provenance Run
Crate 0.5: REQUIRED must be clean, and RECOMMENDED failures must stay
within `_FLOOR` — checks the crate cannot truthfully satisfy (the
workflow's id is its in-crate path and the shape wants `^http`; an
annex-stored image has no registry; lc knows no publisher and no
affiliation). A new failure is a regression, a disappearing one is the
floor to shrink. Gated like the container smoke suite:
`LC_CRATE_TESTS_REQUIRED=1` in CI turns the no-validator skip into a
failure, two tests cover the guard.

**A foreign write is `stale`, everywhere, and it is history, not
hashing.** The hole layer 4 recorded: a skip returns the *recorded*
digest, so a hand-edited-and-committed output (the agent-forged-file
scenario) reads `current` forever. The check: every output is
committed, so a hand edit requires a commit, and `dataset.last_writer`
names it — an output is cleanly written iff the commit that last
touched its directory carries its own run-record subject.
`materialize.datalad_run_subject` is the one spelling of that subject
(named for whose format it is — datalad's, matched by `rerun`'s regex),
shared by `run_record` (the composer) and `_foreign_write` (the
comparator), because two strings here would drift. A hit is a
*contradiction*, not a circumstance — the manifest no longer describes
the bytes — so it classifies `stale` and **the next run remakes it**,
the same philosophy as the dirty refusal's path split: `results/` is
lc's to write, and a committed hand edit is wreckage with a commit
message. Three coherent surfaces, one walk: `lc status` reports it
stale (still exit 0; `OutputStatus.foreign_write` keeps the named
commit for machine consumers), `--check` plans it, and materialize's
driver answers the history question up front and hands each worker the
foreign commit's message — workers have no git, the HEAD discipline on
a third value. History enters `assets.classify` as **one more input
value**, exactly like check mode's sentinel: computed by whoever has
git, handed in, and the rule stays pure — so `calls_for_a_remake`
remains the one bool that turns a state into an action, with no
`foreign or …` re-spelled at any call site. `last_writer` answers "cannot say" as
empty, never an error — a read-only verb must not refuse over an
unborn HEAD or a stripped `.git`. A full-rehash `lc verify` was
considered and rejected: O(bytes), blind on unfetched outputs (history
answers on a bytes-free clone), and with no lifecycle moment enforcing
it. What it leaves to existing tools, on purpose: uncommitted edits
are a dirty tree, annex object corruption (the thin-write hazard
included) is `git annex fsck`, and a manifest lc itself mis-recorded
is the accepted residue only a rehash would catch. A commit that
*forges* the run-record subject defeats the check — as a regenerated
hash would defeat a rehash; the threat model is accidental damage and
shortcuts, not adversaries (spec §7's line).

**Forging in a test must break the hard link first.** Results are
committed thin, so two byte-identical outputs share one annex object —
an in-place `write_text` on one dirties the other, which is the
recorded thin hazard demonstrating itself. `test_materialize._forge`
unlinks before writing; a new tampering test should too.

### Recorded decisions

- **The engine is the host's uv tool, never a project dependency**
  (2026-08, reversing spec §2's engine-in-lock rule and deleting layer 3).
  `lc init` scaffolds no `lightcone-cli` dependency, and there is no
  launcher and no delegation: the `lc` that was invoked is the engine that
  runs. What the pin bought, and where each guarantee went:
  - *The engine inside `env_version`.* Gone, and deliberately: an lc
    upgrade now moves nothing — no output ever reads `behind` over an
    engine release, completing the reversal that already took
    `env_version` out of `definition_version`. The engine is attestation
    (`lc_version` in every manifest, `worker.lc_version()`), not identity.
  - *Reruns reconstruct the exact engine from the commit's lock.*
    Re-provided in the run record itself: `cmd` pins the engine through
    `uv run --no-project --with '<requirement>'` — by version for a
    release, by source commit for a dev build (see the layer-4 run-record
    invariant). Needs PyPI or the git remote reachable at rerun time
    where the lock needed neither; recorded, not hidden.
  - *Driver/worker version skew "structurally impossible".* Re-provided
    by sharing the **installation** instead of the lock. What a Dask
    worker process actually needs is `lightcone.engine` importable at a
    matching version — pickle serializes `worker.materialize` by
    reference and `Task`/`Versions`/`TaskResult` by class reference.
    Pinned by the suite since layer 7
    (`test_a_processes_cluster_fits_through_the_seam`, a full
    materialization through a `LocalCluster(processes=True)`): the
    code works unchanged across process boundaries, and workers
    need **no git and no git-annex** (the driver owns git alone;
    `data_version` is pure file hashing). So per venue: on HPC the tool env lives on the shared
    filesystem and the venue launches workers on the driver's own
    interpreter (`sys.executable` — dask-jobqueue's `python=`), which
    makes driver and workers the identical installation; in containerized
    mode driver and workers share the image, same result. Both also
    satisfy `distributed`'s own client/scheduler/worker coherence
    requirement for free. A connect-time engine-version probe is needed
    only for a cluster lc did not launch (a pre-existing gateway with its
    own image). One venue cost to remember: the `assets.Versions` memo
    degrades to once **per worker process**, so a declared input shared
    by many tasks is re-hashed per process — efficiency, not correctness.
  - *The engine's dependency closure left the record entirely, and is not
    replaced.* The project lock used to pin what the engine resolved —
    most concretely the git-annex build that wrote the bytes. Now
    `lc_version` names the engine and nothing pins what it was made of.
    Accepted rather than re-provided: the alternative is hashing an
    environment lc does not own into artifacts it does, which is the
    over-sensitivity the `behind` model exists to avoid.
  - *Layer 6 resolved its note the other way:* the image gets **no
    engine layer at all**. The engine stays on the host in containerized
    mode too — the container is the recipe's world, never the engine's —
    so the engine version is not a tag input and an lc upgrade still
    rebuilds nothing. See the layer-6 invariants.
  What it buys: a project's resolution is freed from the engine's own
  pins (no astra-tools/click/rich/distributed/git-annex in any project
  lock); the git-annex wheel floor gates only the tool install; the eval
  workflow exercises the branch under test rather than a released engine
  resolved from PyPI; and recipes no longer find `lc` or `git-annex` on
  the sandbox PATH — the project `.venv/bin` no longer carries them,
  which is the boundary telling the truth. The `UV_*` ambient scrub the
  launcher would have done is still worth having and is tracked
  separately; it protects `env_version`'s install-settings term, not the
  delegation that is gone.
- **Reads stay restricted, and the OS baseline is ours to maintain.**
  Codex restricts reads too now, but its Linux read baseline is a *mount
  table*, not a path list — there is nothing to adopt there. Keeping the
  FHS allowlist is a deliberate choice about which way the failure
  points: a missing allowlist entry is a **loud** `EACCES` that gets
  reported and fixed once for everyone, while any exclusion-based scheme
  fails **silently**, in exactly the thing the layer exists to prevent.
  Worth remembering when the list next comes up short: none of the three
  bugs we hit (ELF loader, interpreter root, `/dev/urandom`) came from
  the list — two were derived paths and one was already in it.
- **Devices, `/proc`, and `/sys` are granted generously, on purpose.**
  Writable: the terminal set (`/dev/tty`, `/dev/pts`, `/dev/ptmx`), the
  discard devices (`/dev/null`, `/dev/zero`, `/dev/full`), and `/proc`
  and `/sys` whole. The threat model is accidental leakage, not a hostile
  recipe (spec §7), and **none of these is a channel undeclared *inputs*
  arrive through** — that is the test to apply when the next one comes
  up. Tightening them buys nothing and costs real failures:
  `pty.openpty()`, `/dev/full` ENOSPC handling, `oom_score_adj`, MPI and
  CUDA runtimes poking `/sys`.
  It also reads more permissive than it is — Landlock only ever *removes*
  access, never adds it, so ordinary Unix permissions remain the real
  gate (devpts hands each pty to its allocating user at 0620; nearly all
  of `/proc` and `/sys` is root-owned). Granting them just stops lc
  adding a second, more confusing denial on top of the OS's own.
  The line is drawn at `/dev/urandom` and `/dev/random`, which stay
  read-only: writing those seeds the *host's* pool — a side effect on
  the machine rather than on the run.
  The general rule, learned here: **a grant whose absence produces an
  unattributable error is one to make freely.** Without devpts,
  `pty.openpty()` raises `OSError: out of pty devices` — naming neither
  a path nor the sandbox, so `denial.explain()` can extract nothing and
  only the trailer fires. A denial the user cannot act on is worse than
  the access it withheld.
- **Landlock stays the Linux mechanism; bubblewrap is not adopted.**
  Codex moved its Linux default to bwrap+seccomp (Landlock is now
  `--use-legacy-landlock`) because it needs rights *subtraction*:
  `read_only_subpaths` inside a writable root, to stop an agent writing
  `.git/hooks` and escalating. Landlock cannot express that, and their
  own older code silently dropped the carve-out on Linux as a result.
  **That requirement is not ours** — spec §7's threat model is accidental
  leakage, not a hostile recipe, and our policy's exceptions always
  *widen* (a writable output dir inside a readable tree), which is
  exactly the direction union semantics handle for free. The cost would
  be real: a bundled `bwrap` binary, a user-namespace probe, a WSL1
  refusal, and the Ubuntu 24.04 AppArmor wall that made
  `hermeticity-enforcement.md` §3 call bwrap "an opportunistic upgrade,
  never the requirement". Re-add triggers: a policy shape that genuinely
  needs subtraction, or ambient `bwrap` becoming universal.
- **The ASTRA `container:` directive is ignored, entirely.** astra's
  boilerplate writes `container: python:3.12-slim` into `astra.yaml`, and
  lightcone-cli does nothing with it: not read, not stripped, not
  validated, not migrated. The environment is `pyproject.toml` + `uv.lock`
  (spec §2), so a scaffolded project simply carries a key no code path
  consults. Don't "fix" this by reconciling the two — a later layer will
  decide whether the key is dropped upstream, refused, or migrated.
- **Multi-runtime, podman recommended** (2026-08, layer 6 — superseding
  an earlier "podman only" plan decision). podman and docker ship
  tested; the backend seam and the `docker-archive` format are chosen so
  apptainer/singularity become thin layer-7 backends over the same
  archive (they exec `docker-archive:` content directly, and HPC hosts
  that cannot build obtain images through the annex). Build-capable
  (podman|docker) and run-capable (all four, eventually) are therefore
  separate questions in `container.py`. docker's daemon is probed at
  detection — a CLI with the daemon down is the common broken state.
- **Bare-recipe run records: considered and rejected** (2026-08).
  datalad expands container templates at *record* time and `rerun`
  executes the literal string — datalad-container has no rerun hook — so
  a record whose `cmd` is the recipe replays on the host, unconfined,
  with no manifest, and a containerized spelling would freeze one
  runtime's flags into git forever. The worker-as-executor keeps the
  record declarative and runtime-neutral; mechanical no-lc replay is
  layer 8's WRROC export, for which the manifest + archive now carry
  everything.
- **Single-arch archives, recorded residue** (layer 6 → layer 7). The
  committed archive is the architecture it was built for, and the
  manifest says which. An Apple-silicon build cannot serve an amd64
  venue; solving that (arch-suffixed archive paths, `--platform`
  cross-builds) is layer 7's, with the venue that makes it real. Old
  archives accumulate in annex history; reclaiming them is the user's
  `git annex unused`/`drop` — no GC verb.

### Recorded deviations from the spec

- **No `AGENTS.md` scaffolding** (spec §2 calls for an agent notes
  stanza). Dropped by decision: it documented four verbs, three of which
  don't exist yet, and the scaffold shouldn't assert behavior the CLI
  can't deliver. Revisit at layer 4, when the verbs it describes are
  real. `tests/test_project.py::test_converge_writes_no_agent_notes`
  pins the absence, so re-adding it is a deliberate act.
- Scaffolded file bodies stay descriptive of what works today — see the
  trimmed `results/README.md` for the same reason.
- **The Landlock policy travels as JSON on argv, not as an inherited
  ruleset FD** (spec §7 specifies the FD, built before fork and passed
  with `pass_fds`). The shim builds and applies the ruleset itself, which
  is also what the codex CLI does. This makes `wrap()` a pure function and
  closes §11's own blocking spike — *"does the Landlock FD survive
  `uv run`'s spawn/exec chain? an FD cannot be reopened"* — by making the
  question moot. It also survives into a container later, where a host FD
  cannot. The document is deliberately *not* versioned: the wrap always
  invokes the shim on lc's own interpreter, so writer and reader are the
  same lightcone-cli by construction, and a compatibility field would be
  backward-compat machinery with no consumer.
- **Network is not controlled, on either platform**, by decision. §7's
  matrix has Seatbelt record `denied`; the generated SBPL explicitly
  allows network and both platforms attest `network: allowed`. Symmetric
  and honest — nothing pretends to a control it does not apply. (codex
  ships a seccomp denylist for this; adding one is a live option, not a
  gap we are hiding.)
- **`lc run` has no rename guard and no sandbox flags.** §4's guard
  against `lc run <output_id>` existed only for muscle memory from the
  pre-rebuild CLI — backward compatibility we do not promise — and §7's
  `--require-sandbox` / `--no-sandbox` / `--sandbox-debug` are all
  absent: there is no hatch to escape the sandbox, so there is nothing
  for the flags to switch. The verb takes a command and nothing else.
- **The denial's remedies are only what works today** — `uv add` for a
  Python package, the system layer (`apt-install` + the containerize
  note, real since layer 6), the ASTRA input declaration for data, and
  "output goes in `results/`" plus `tempfile.mkdtemp()` for a write
  denial. Nothing in a denial message names a verb, flag, or declaration
  that does not exist.
- **`Attestation` has no serializer of its own.** An earlier draft
  carried a `to_manifest()` with no caller — deleted, because "no dead
  code" applies to this layer's own conveniences too. It is persisted by
  the layer that needs it: the worker writes `asdict(outcome.attestation)`
  into the manifest's `hermeticity` field. `lc run` is still a probe with
  no output (§4), so there the attestation is returned and printed, never
  persisted.
- **`results/` is writable; the rest of the tree is not**, where §4 gives
  a probe no output and therefore no in-tree write scope at all. A probe
  gets the same write scope a recipe does, so a probe that works means a
  recipe will — and the environment a run starts with is the one it
  finishes with.
  - **The shape was chosen because all three mechanisms express it
    natively.** A writable directory *nested inside* a read-only tree is
    the widening direction: Landlock unions rights over ancestors, SBPL
    restates the write tier after the guard, and podman mounts the
    project `:ro` with `results` `:rw` over it — all verified by running
    them. The reverse — a writable tree with `.venv` carved out — needs
    rights *subtraction*, which podman and SBPL can do and **Landlock
    cannot at all**. That asymmetry is the whole argument: the read-only
    shape is the only one direct mode and containerized mode can both
    deliver, so it is the only one that gives a single UX.
  - This was briefly reversed (PR #174) on the premise that "a container
    bind-mounts the working tree read-write". True of the default, not of
    the mount table we will write — and the experiment that settled it is
    two `podman -v` flags. Don't re-derive it from the default again.
  - **`_exec_set` grants no directory except the interpreter's own
    install tree** (see the layer-5 EXECUTE invariant) — and never
    `.venv/bin`. A directory grant is a grant on whatever the directory
    holds *later*, which was a live hole for as long as the tree was
    writable: `cp /usr/bin/git .venv/bin/` ran a tool the allowlist
    denies by name. Belt-and-braces under a read-only tree, one scandir,
    keep it.
  - **A write-denial test must target a path the OS would let you
    write.** `printf x > /etc/…` passes with no sandbox at all — the OS
    refuses it for any non-root user — so it pins nothing. The sound
    target is a user-owned path that the *policy* makes read-only: a
    declared input. Mutation-check every denial test by running the same
    command through `Unavailable()` and confirming it succeeds.
  - **Enforcement fixtures must not live under `/tmp`.** It is in the
    write baseline, so anything pytest's `tmp_path` hands you is
    *granted*. The old denial tests passed only because a project under
    `/tmp` used to drop `/tmp` from the policy; with that gone they went
    green while testing nothing. `tests/test_sandbox_enforcement.py`'s
    `outside` fixture is rooted at `$HOME`, which is outside every grant
    by construction.
- **`/run` is granted whole**, where codex names only
  `/run/current-system/sw`. It reaches `/run/user/$UID` — dconf, the
  gnupg and keyring sockets, portal state. Kept deliberately: the test
  is whether undeclared *inputs* arrive through a path, and runtime
  sockets are not something a recipe accidentally reads data from.
  `/etc/resolv.conf` is a symlink into `/run` wherever systemd-resolved
  is in use, so the grant also keeps DNS working.

## Extending the Codebase

| To... | Read | Key patterns |
|---|---|---|
| Add the next layer | the spec (§11 = the layer ordering) | Land code + tests + deps together; update the layer table above. Docs are deliberately deferred |
| Change what a scaffolded file contains | `src/lightcone/engine/templates/files/` | Edit the `.tmpl`; add new ones to `TEMPLATE_NAMES`, and a renderer only if the file needs a substituted value or a merge policy |
| Add a value to the scaffold | `src/lightcone/engine/templates/__init__.py` | Derive it from the environment or our own metadata before introducing a constant |
| Change what gets converged | `src/lightcone/engine/project.py` + `tests/test_project.py` | `_Converger.item` / `.file`; repairs only ever append |
| Change how a project stores bytes | `src/lightcone/engine/dataset.py` + `templates/files/gitattributes.tmpl` | Every command through `project._run`; test it against a real annex (`real_tools`) |
| Change how an output is identified | `src/lightcone/engine/identity.py` + `tests/test_identity.py` | Sensitivity tests both ways: what must move the hash, and what must not |
| Change what the image is made of | `src/lightcone/engine/image.py` + `tests/test_image.py` | Pure; structure-and-ordering tests, never byte goldens; every declaration key is hashed |
| Change how images are built, stored or entered | `src/lightcone/engine/container.py` (+ `sandbox/oci.py` for the exec argv) + `tests/test_container.py` | Everything through `project._run`; keep `runtime_for_run`'s two strictnesses; runtime differences are spellings inside `OCIBackend`, never new shapes |
| Change when an output is remade | `src/lightcone/engine/assets.py` + `tests/test_assets.py` | One `classify()`, two callers; `--check` differs by one input value, never by logic. Ask first whether the thing that moved *contradicts* the project or is a *circumstance* — the second is `behind`, not `stale` |
| Change how the spec becomes a graph | `src/lightcone/engine/plan.py` + `tests/test_plan.py` | Ask `astra.resolve`; if the answer is missing, the fix is a PR to astra-tools. Anything ambiguous is a `ProjectError`, never a guess |
| Change how a recipe runs | `src/lightcone/engine/worker.py` + `tests/test_worker.py` | Never raises, never writes git; mutation-check every denial test |
| Change what a run commits | `src/lightcone/engine/materialize.py` + `tests/test_materialize.py` | The driver owns git alone; the tree ends as clean as it started |
| Change where a run executes | `src/lightcone/engine/venue.py` + `materialize.cluster_for_run` + `tests/test_venue.py` | One detection ladder, in `cluster_for_run` alone; venues are detected, never configured; test by faking the host (env vars + a stub srun), never the code |
| Change what the crate says | `src/lightcone/engine/crate.py` + `tests/test_crate.py` | Pure builder: sorted iteration, no clock, git injected as `writer`; structure tests, never byte goldens — the one byte-level claim is render-twice-identical. The validator floor lives in `tests/test_crate_smoke.py::_FLOOR` |
| Change how a foreign write is detected | `dataset.last_writer` + `materialize._foreign_write` + `tests/test_dataset.py` | History, never hashing; `datalad_run_subject` is the one spelling of the record's subject; a foreign write classifies `stale` in every verb |
| Add a CLI verb | `src/lightcone/cli/commands.py` | `@main.command()`; keep logic in the engine, raise `ProjectError`, render here |
| Add a sandbox mechanism | `src/lightcone/engine/sandbox/` | One module with a `Backend` (`wrap` pure, `attest` honest) + one line in `detect()`. Nothing above the seam changes |
| Change what a sandboxed command may touch | `sandbox/policy.py` + `tests/test_sandbox_policy.py` | Path sets only — no mechanism ever leaks in here |
| Change a denial message | `sandbox/denial.py` + `tests/test_sandbox_denial.py` | Remedies must be copy-pasteable and real *today*; the trailer stays unconditional |

## Test Patterns

- `tests/conftest.py` — the `tools` autouse fixture stubs
  `engine.project._run`, emulating each tool's observable effect (`uv lock`
  writes `uv.lock`, `uv sync` makes `.venv`, `git init` makes `.git`,
  `git annex init` marks the repository annexed) and recording every argv.
  `uv_calls(tools)` narrows it to uv; `probes(calls)` to the read-only
  `--check` probes. Under the stub, tests are hermetic: no network, no
  resolution, no subprocesses. The `real_tools` fixture opts back out,
  putting the real `_run` back — and everything built on it (`analysis`,
  and `test_materialize.py`'s `engine_dist` wheel build plus the rerun
  tests' ephemeral `uv run --with` resolve) does spawn, resolve, and may
  touch the network.
- `tests/test_dataset.py` — the storage seam, tested against real tools
  via `real_tools`, deliberately: the question is whether bytes land in
  the annex or as a blob in git, and a fake answering it would only
  restate what the code already believes. Every bug this file found (the
  missing `.gitattributes` default, the pointer-file trap, `filter=annex`)
  was invisible to a stub.
- `tests/test_project.py` — discovery and convergence semantics, called
  directly. This is where scaffold behavior is tested.
- `tests/test_plan.py` tests what lc adds — directories, edges,
  `definition_version`, target resolution, the validation gate — and **not**
  what a spec means. Scoping, `from:`, `when:` and the recipe grammar are
  covered by `astra-tools`' own suite; asserting them here again would
  re-create the second implementation this layer just deleted. Every
  fixture must be a spec `astra validate` accepts, which the gate now
  enforces for free.
- `tests/test_identity.py`, `test_assets.py`, `test_plan.py` — **pure**,
  and the whole of layer 2 plus the graph. Nothing spawns, nothing on disk
  beyond `tmp_path`.
- `tests/test_worker.py`, `tests/test_materialize.py` — real recipes,
  through the real boundary, against a real repository, via the `analysis`
  fixture in `conftest.py`. That is the price of testing execution: whether
  the gates hold, whether bytes land in the annex, and whether the tree is
  clean afterwards are not questions a stub can answer. It is cheap anyway
  — the fixture's project declares no dependencies, so `uv lock` and
  `uv sync` together cost milliseconds.
  - **`cluster_for_run()` is the one new monkeypatch point.** Most tests
    swap in an inline scheduler and never start Dask; exactly one starts a
    real `LocalCluster`, because a seam is only worth having if the thing
    it abstracts still fits through it.
- `tests/test_cli.py` — the CLI surface only: flags reaching the engine,
  rendering, exit codes, error translation. Rich wraps output at terminal
  width, so assert on short unwrappable fragments.
- `tests/test_templates.py` — template loading (guards the packaging of
  package data), strict substitution, and the `.gitignore` /
  `.gitattributes` entry/repair logic. Content assertions live here; `test_project.py` asserts only that
  the file written *is* the template, so a template edit touches one file.

The sandbox suite splits along the seam, which is what makes it cheap:

- `tests/test_sandbox_policy.py`, `test_sandbox_wrap.py`,
  `test_sandbox_denial.py` — **pure, and run on every OS.** The policy is
  data, `wrap` is a function, and the denial renderer is a function, so
  the Landlock wrap and the macOS SBPL are both checked on Linux CI with
  no privileges and nothing spawned.
- `tests/test_sandbox_shim.py` — the shim as a **real subprocess**,
  because its contract *is* its argv and exit codes. Needs no kernel
  support: every case here is a setup failure or a pure-function check.
- `tests/test_sandbox_enforcement.py` — **the kernel's answer**, written
  once for both mechanisms. See below; this is the one that matters.
- `tests/test_run.py` — what `lc run` decides *before* it execs: the
  current-directory project check, declared inputs, the uv hop. Nothing
  spawns.
- `tests/test_venue.py` — the venue's surface is ambient (env vars, an
  srun on PATH), so the suite fakes the *host*, never the code: SLURM
  variables set deliberately, a bash stub standing in for srun, and the
  end-to-end tests run a real graph through the real detection, bind,
  launch and teardown path on any machine — real worker processes, no
  SLURM anywhere. `SLURMD_NODENAME=127.0.0.1` keeps the scheduler bind
  hermetic against CI DNS. No required-vs-skip gating: nothing depends
  on host capability. The `venue_env` autouse fixture in conftest scrubs
  the venue variables suite-wide — without it the whole suite fails on a
  NERSC login node (the guard) and the real-cluster test would srun
  across a live allocation.
- `tests/test_image.py` — **pure**: the declaration, the document, the
  render's structure and ordering, tag sensitivity both ways, and the
  `env_version` integration. `tests/test_sandbox_oci.py` — **pure**: the
  mount table, the argv spellings, the attestation, and the
  `contains_prefix` composition through a recorded fake `Popen`.
- `tests/test_container.py` — the image lifecycle against a **stubbed**
  `project._run` that models each runtime command's observable effect
  (a `save` writes a structurally real docker-archive, a `load` marks
  the id present), so every refusal and every strictness is asserted on
  recorded argv with nothing spawned.
- `tests/test_container_smoke.py` — **the runtime's answer**, gated like
  the enforcement suite: skips without a runtime,
  `LC_CONTAINER_TESTS_REQUIRED=1` on Linux CI turns the skip into a hard
  failure, and two tests cover the guard itself. Parameterized over the
  runtimes present. It builds a real image, commits a real archive into
  a real annex, materializes through the real Dask cluster, and runs a
  real `datalad rerun` on a bytes-free clone — the record's whole claim.
  Each denial sits beside its mutation check.
- `tests/test_crate.py` — **pure**: fixture manifests on `tmp_path`, a
  hand-built graph, a stub `writer` — no git anywhere. Structure and
  ordering only; the single byte-level assertion is
  render-twice-identical, the property convergence rests on.
  `tests/test_crate_smoke.py` — **the validator's answer**, gated with
  `LC_CRATE_TESTS_REQUIRED=1` (all CI runners — the validator is a dev
  dependency, so none may skip): a real materialize, then the official
  `rocrate-validator` against the Provenance profile, REQUIRED clean and
  RECOMMENDED pinned to the recorded `_FLOOR` set.

Note the autouse `tools` fixture stubs `engine.project._run` only —
sandbox tests spawn real processes deliberately, and are the one place in
the suite that does.

### The enforcement suite, and why it is shaped that way

`test_sandbox_enforcement.py` is the only file that can tell you the
layer works. Four properties, each of which it would be easy to lose:

1. **One suite, both mechanisms.** The same tests run Landlock on Linux
   and Seatbelt on macOS, parameterised by `detect()` alone. That is the
   seam paying rent — and it is the only way the two stay honest, since
   *a leak only Linux catches is a leak*. macOS is in the CI matrix for
   exactly this: it is the sole place the generated SBPL is ever
   executed.
2. **The real policy.** It runs against `exec_policy` — what an actual
   `lc run` *and* an actual recipe get — never a policy hand-built to
   make the point. A test
   that grants exactly what it is testing cannot discover that the
   *shipped* policy grants something else. (This is how `/usr` sat in the
   exec set through a full green suite.)
3. **Real leaks, tried literally.** Undeclared *tools* are executed,
   undeclared *libraries* are `dlopen`ed, undeclared *data* is read —
   the three channels of spec §7. Assertions about path sets belong in
   `test_sandbox_policy.py`; this file runs the command.
4. **It cannot pass by not running.** `LC_SANDBOX_TESTS_REQUIRED=1` is
   set in CI, which turns "no mechanism here, skip" into a hard failure.
   Two tests cover the guard itself, because an unfailing guard is worse
   than none.

**When you add an enforcement test, mutation-check it**: run the same
command through `Unavailable()` and confirm it *succeeds*. A denial test
that would pass unsandboxed is testing nothing, and the failure mode is
silent. Every leak case here was checked that way.

## Conventions

- Ruff for linting (E, F, I, N, W, UP), line length 100, target Python 3.11
- mypy strict mode with `namespace_packages = true`,
  `explicit_package_bases = true`
- **Google-style docstrings** on every public function: an imperative
  one-line summary, then `Args:` / `Returns:` / `Raises:` / `Yields:`
  where they carry information. Concise — a design decision gets a
  sentence or two, not its history; the long form belongs in this file.
  Two deliberate exceptions: **click command callbacks**, whose docstring
  *is* the `--help` text and must stay prose, and properties, whose value
  the summary already describes.
- Comments and docstrings carry *why*, never a narrative of how the code
  came to be
