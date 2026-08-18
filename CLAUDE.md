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
| 5 | **Sandbox layer** — Landlock / Seatbelt, exec-shim, denial UX, `lc run` | ✅ **done** |
| 6 | Container hatch — `[tool.lightcone.image]`, generated Containerfile, `lc build` | ⬜ |
| 7 | Venues — Perlmutter, hub/GKE, Cloud Build | ⬜ |
| 8 | `lc status`, `lc verify`, WRROC export | ⬜ |

Layer 5 landed **out of order**, ahead of 2–4: `lc run` is the spec's
*probe* verb, and a probe has no output, so it needs neither manifests
(layer 2) nor the fabric (layer 4) — only project discovery, which came
with it. That makes it the smallest honest consumer of the exec boundary,
and the boundary is what layer 4 will then plug recipes into.

The spec's §11 (Migration) is the authoritative ordering; the table above
is the working map. **Layer boundaries are also dependency boundaries** —
a dependency enters `pyproject.toml` with the layer that needs it, not
before.

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
├── _sandbox_exec.py        # the Landlock shim — stdlib only, zero lightcone imports
├── cli/                    # the CLI only: flags, rendering, exit codes
│   ├── __init__.py         # exposes main(); the launcher hooks in here (layer 3)
│   └── commands.py         # lc init, lc run
└── engine/
    ├── __init__.py         # docstring only
    ├── project.py          # what a project is: convergence + discovery
    ├── run.py              # what `lc run` is: the probe + the uv hop
    ├── sandbox/            # the exec boundary
    │   ├── __init__.py     # the public surface (detect, run, scope, the types)
    │   ├── model.py        # Policy · Capability · Attestation · Backend protocol
    │   ├── policy.py       # what a probe may touch
    │   ├── boundary.py     # detect() + run(): the mechanism-blind half
    │   ├── landlock.py     # Linux backend
    │   ├── seatbelt.py     # macOS backend
    │   └── denial.py       # the denial UX
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

**There is no project discovery, by decision.** `lc init` is handed its
directory (defaulting to `.`); `lc run` assumes the current directory is
the project root — `project.current_project()` checks only that the
environment is there (`pyproject.toml`, `uv.lock`, `.venv`) and does not
require an `astra.yaml`, so any uv project can be probed. No walk-up:
the directory you invoke from is the project, or it is a clean error.

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
    deliberate trade: paying compilation once here beats paying it on the
    first import of every run.
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
so the resolved symlink target would be runnable, which is `/usr` for
any venv built on a system python (`uv venv --python-preference
only-system`, most CI images, HPC site pythons). The grant is on the
interpreter **file**; only READ goes to the install root, for the stdlib.
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
`writable_project`, output-dir write scope, scratch dirs, `in_container()`,
and the `podman*`/`pod*` attestation branches all belong to layers 4 and 6
and are absent, not stubbed.

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

### Recorded decisions

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
- **`lc run` does not detect containerized mode.** The
  `[tool.lightcone.image]` refusal was removed with the rest of the
  future-facing surface; a system-layer declaration is currently inert,
  like `astra.yaml`'s `container:` key.
- **The denial's remedies are only what works today** — `uv add` for a
  Python package, plus the ASTRA input declaration. Nothing in a denial
  message names a verb, flag, or declaration that does not exist.
- **No manifest is written, and no serializer for one.** A probe has no
  output (§4), so the `Attestation` is returned and printed rather than
  persisted. An earlier draft carried a `to_manifest()` with no caller —
  deleted, because "no dead code" applies to this layer's own
  conveniences too. It lands with layer 4, which is what needs it.
- **The project tree is writable**, where §4 gives a probe no output and
  therefore no in-tree write scope at all. The boundary exists to be a
  no-install stand-in for running in a container, and a container
  bind-mounts the working tree read-write; forbidding writes inside the
  project made lc worse at the one job it has without catching anything
  the design cares about, which is a reach *outside* the project. Note
  this cannot be narrowed to "everything but `.venv`": Landlock unions
  rights over ancestors and cannot subtract, so a writable project is
  necessarily a writable `.venv`.
  - **The exec allowlist has to survive that**, and it did not at first:
    `.venv/bin` was granted as a *directory*, so `cp /usr/bin/git
    .venv/bin/ && git` ran a host tool the allowlist denies by name.
    Grants there are per *file* now, taken when the policy is built, so
    what a run writes afterwards is not runnable. `_exec_set` grants no
    directory anywhere; the rule is the same one `/usr/bin` has always
    had, and now it is load-bearing twice over.
  - What remains is a module dropped into site-packages, which imports.
    That is inherent — Landlock cannot carve `.venv` out — and `uv sync
    --exact` does **not** clean it up: measured, it prunes packages by
    RECORD and leaves unmanaged files alone. Don't claim otherwise; a
    writable environment is writable, exactly as in a container.
  - Two things went with it, both of which existed *only* to serve the
    read-only tree: the filter that dropped `/tmp` from the write scope
    when the project lived under it, and `PYTHONPYCACHEPREFIX`. The
    latter redirected bytecode into a `$HOME` deleted after every run,
    so every in-tree module recompiled every time; a writable tree
    caches in place, which is what a container does and what the
    scaffolded `.gitignore` already expects.
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
| Change what a scaffolded file contains | `src/lightcone/engine/templates/files/` | Edit the `.tmpl`; add new ones to `TEMPLATE_NAMES` + a renderer |
| Add a value to the scaffold | `src/lightcone/engine/templates/__init__.py` | Derive it from the environment or our own metadata before introducing a constant |
| Change what gets converged | `src/lightcone/engine/project.py` + `tests/test_project.py` | `_Converger.item` / `.file`; repairs only ever append |
| Add a CLI verb | `src/lightcone/cli/commands.py` | `@main.command()`; keep logic in the engine, raise `ProjectError`, render here |
| Add a sandbox mechanism | `src/lightcone/engine/sandbox/` | One module with a `Backend` (`wrap` pure, `attest` honest) + one line in `detect()`. Nothing above the seam changes |
| Change what a sandboxed command may touch | `sandbox/policy.py` + `tests/test_sandbox_policy.py` | Path sets only — no mechanism ever leaks in here |
| Change a denial message | `sandbox/denial.py` + `tests/test_sandbox_denial.py` | Remedies must be copy-pasteable and real *today*; the trailer stays unconditional |

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
2. **The real policy.** It runs against `probe_policy` — what an actual
   `lc run` gets — never a policy hand-built to make the point. A test
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
- Docstrings and comments carry *why*, and cite the spec section
  (`spec §7`) when they encode a design decision
