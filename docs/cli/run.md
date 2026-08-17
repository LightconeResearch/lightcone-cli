# lc run

```text
lc run [CMD…] [--no-sandbox] [--sandbox-debug]
```

The **probe** verb: run an arbitrary command in byte-for-byte the
recipe environment — the locked interpreter and packages, the sandbox
included. With no CMD, opens a shell there (announced).

```bash
lc run python -c "import astropy; print(astropy.__version__)"
lc run python src/explore.py
lc run                       # sandboxed shell in the recipe environment
```

## Semantics

- Direct mode ≡ `uv run --locked --exact CMD` from the project root,
  inside the sandbox. Containerized mode runs the same command inside
  the digest-pinned project image.
- A probe has no output, so its **write scope is the tmp scope only** —
  never in-tree. Its read scope is the project plus the union of all
  declared inputs.
- `lc run` **never builds an image** — on a containerized project whose
  image is missing it errors with the exact `lc build` command, so a
  two-second probe can't silently absorb a multi-minute build.

## The rename guard

Outputs are materialized, not run. A first argument naming a declared
output errors before any exec:

```text
outputs are materialized, not run — did you mean: `lc materialize best_fit`?
```

## Diagnostics

| Flag | |
|---|---|
| `--sandbox-debug` | open a shell *inside* the sandbox, to see exactly what a recipe can see |
| `--no-sandbox` | run without enforcement (recorded as unsandboxed) |
