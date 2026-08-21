# Development Setup

Everything runs through [uv](https://docs.astral.sh/uv/); there is no
task runner and no other build tooling.

## Clone & install

```bash
git clone https://github.com/LightconeResearch/lightcone-cli.git
cd lightcone-cli
uv sync --group dev
```

That resolves the engine and the dev tools (pytest, ruff, mypy,
datalad, the rocrate validator) into `.venv`. `uv run lc --version`
runs the checkout's `lc`.

You also need `git` on `PATH` (the one tool uv cannot install);
git-annex arrives as a wheel with the sync.

## The loop

```bash
uv run pytest                        # the suite
uv run ruff check src/ tests/        # lint (--fix to apply)
uv run mypy src/                     # strict mode
```

These three are exactly what CI runs (`tests.yml`, `lint.yml`) — green
locally means green there, modulo the gated suites below.

Most of the suite is hermetic: an autouse fixture stubs the engine's
one subprocess seam, so tests spawn nothing and touch no network. The
exceptions opt in explicitly — see [Testing](testing.md).

### The gated suites

Three suites answer questions only a real mechanism can, and each
skips where its mechanism is missing — with an environment variable CI
sets to turn the skip into a hard failure:

| Variable | Suite | Needs |
|---|---|---|
| `LC_SANDBOX_TESTS_REQUIRED=1` | `test_sandbox_enforcement.py` | Landlock (Linux) or Seatbelt (macOS) |
| `LC_CONTAINER_TESTS_REQUIRED=1` | `test_container_smoke.py` | podman or docker |
| `LC_CRATE_TESTS_REQUIRED=1` | `test_crate_smoke.py` | nothing beyond dev deps |

## Building the docs

```bash
uv sync --group docs
uv run zensical build        # renders into site/
uv run zensical serve        # live preview
```

The site deploys on release (`docs-deploy.yml`), so docs track the
released CLI, not `main`.

## Building the wheel

```bash
uv build
```

CI runs this only to publish. The version comes from hatch-vcs — the
git tag for a release, tag-plus-commit for a dev build — which is also
what lets a run record pin a dev engine by its source commit.

## Pre-PR checklist

1. `uv run pytest` — including, if your change touches the sandbox,
   containers, or the crate, the relevant gated suite on a host that
   can run it.
2. `uv run ruff check src/ tests/` and `uv run mypy src/`.
3. New behavior lands with its tests, in the same PR.
4. Read [Extending](extending.md) — it says where each kind of change
   belongs, and the invariants it must keep.
