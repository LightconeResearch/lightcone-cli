# The environment

The locked environment **is** the execution environment. A project is
`pyproject.toml` + `uv.lock` + `.python-version`; everything a recipe
may use is declared there (or in `astra.yaml` as data inputs), and the
sandbox makes that declaration mechanical rather than aspirational.

## Two modes, one substrate

Mode is **derived, never configured**:

- **direct** (the default): the environment lives in the project tree
  (`.venv`), no image is ever built, no container runtime is needed.
- **containerized**: triggered solely by declaring a system layer in
  `pyproject.toml`. From then on the generated image is the execution
  world — recipes, workers, and probes all run from its baked
  environment.

`lc status` always tells you which mode you are in and why.

## The sandbox

Every recipe (and every `lc run` probe) executes inside an OS sandbox
restricted to its declared set:

- **writes**: its own `results/<universe>/<output>/` directory, scratch,
  and `/tmp` — sibling outputs, manifests, and `astra.yaml` are
  mechanically protected from a misbehaving recipe;
- **reads**: the project tree, declared inputs, and the OS baseline;
- **executes**: the locked environment's binaries plus a small,
  versioned set of shell utilities.

On Linux this is [Landlock](https://landlock.io/) (kernel ≥ 5.13,
unprivileged, nothing to install); on macOS, Seatbelt. Each output's
manifest records the enforcement that actually ran — mechanism, file
scope, network posture — in its `hermeticity` field. If no mechanism is
available the run proceeds *and says so*; nothing is ever silently
unsandboxed. `--require-sandbox` turns that into a refusal.

A recipe that legitimately writes intermediates elsewhere in the tree
declares it, in the repo:

```toml
[tool.lightcone.sandbox]
writable-project = ["my_output"]
```

Its manifest then honestly records `fs: project-rw`.

## The denial message

When the sandbox blocks something, the error is the interface:

```text
blocked by lc sandbox: cannot execute /usr/bin/latex —
not part of the declared environment.

  if this is a tool the recipe needs, declare it in the system layer:
      [tool.lightcone.image]
      system-packages = ["texlive-latex-base"]
    note: this containerizes the project — podman required — and
    re-stages all materialized outputs.

  if this is a data file, declare it as an input in astra.yaml: …

  diagnostics: lc run --sandbox-debug · lc run --no-sandbox · lc status
```

Both remedies are always shown; the escape hatches stay in the
diagnostics trailer. `lc run --sandbox-debug` opens a shell *inside*
the sandbox to poke at what a recipe can see.

## The container hatch

When a dependency genuinely cannot come from PyPI — R, Julia, TeX,
compilers, a system library a locked package links against — declare it:

```toml
[tool.lightcone.image]
system-packages = ["r-base-core", "libhdf5-dev"]
```

That one table is the entire surface, and its presence is the
escalation. `lc` renders the locked environment *plus* the declared
system layer into a content-addressed image:

- The image is **generated, never authored** — there is no Containerfile
  to write. (`Containerfile.extra` exists as a bounded escape for build
  steps beyond apt; it is content-hashed into the identity.)
- The image is a *cache of the lock plus the system layer*: project
  code never enters an image, so **code edits never trigger a build**.
  Environment edits rebuild — exactly when a rebuild is meaningful.
- Execution is **digest-pinned**: the tag is a pure function of the
  repo plus the engine, the build records the produced digest, and
  every run asserts it.
- A custom base (e.g. a CUDA userland) is supported, digest-pinned:
  `base = "nvcr.io/nvidia/cuda:12.4.1-runtime-ubuntu22.04@sha256:…"`.
  Tag-only refs are refused — the identity must be a function of the
  repo, not of registry state.

Check PyPI first: h5py wheels bundle libhdf5, NVIDIA ships CUDA wheels,
BLAS rides inside numpy. The hatch is for what genuinely has no wheel.

Dependency verbs never change: `uv add` runs on the host, bare, in both
modes (add `--no-sync` in containerized projects — the host `.venv` is
inert there). The next `lc materialize` picks changes up through the
ordinary rebuild path; `lc run` never builds (it points you at
`lc build`).

Escalation is reversible: delete the table and the project returns to
direct mode. Either direction is an environment edit — every
materialized output goes stale, and `lc` says so up front:

```text
environment changed: 14 materialized output(s) are now stale
```

## What the manifest records

Every output's `.lightcone-manifest.json` carries the environment
identity (`env_version` — lock, interpreter pin, install settings,
system layer), the runtime attestation (platform, interpreter build, uv
version, GPU driver, threading knobs), the container image tag+digest
and the system layer's package snapshot hash when one ran, and the
`hermeticity` record. `lc verify` additionally surfaces outputs that
were produced unsandboxed or from a dirty git tree.

The claim is **pinned environment identity, never bit-identical
outputs** — you always know exactly what an output was computed with,
and exactly what it would take to recompute it.
