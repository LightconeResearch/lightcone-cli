# lc build

```text
lc build [--force]
```

Build the project's environment image (containerized mode). On a
direct-mode project this is an explanatory no-op — there is no image to
build until `[tool.lightcone.image]` is declared.

## What it builds

The image is **generated, never authored**: the locked environment plus
the declared system layer render to a Containerfile with a fixed
layering —

1. the digest-pinned base (the engine's default Debian base, or the
   project's declared `base`);
2. the base-contract checks (glibc, `/bin/sh`, apt when
   `system-packages` are declared) — each violation is a pointed
   build-time refusal, never a raw build log;
3. the apt layer (sorted `system-packages`), **before** the
   environment sync, so lock-level system dependencies (sdist builds,
   rpy2-style imports) resolve where the system layer actually is;
4. the pinned uv binary and the exact `.python-version` interpreter;
5. `uv sync --locked --exact --no-install-project --compile-bytecode`
   into `/opt/venv` — the build context is exactly the rendered
   Containerfile, `pyproject.toml`, and `uv.lock`; **project code never
   enters an image**;
6. the optional `Containerfile.extra` stage;
7. the final ENV contract (offline overlay — nothing inside a running
   image ever touches the network for packages).

## Identity

The tag `lc-env-<hash>` is a pure function of the repo plus the engine:
code edits never move it, environment edits always do. Builds are
incremental — a tag hit is a no-op; `--force` rebuilds anyway. The
build records the produced image id and a snapshot of the installed
system packages (`.lightcone/image/`, machine-local); execution is
pinned to that record.
