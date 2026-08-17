# Troubleshooting

## `ModuleNotFoundError` in a recipe or probe

The package is not in the project's lock. Fix the environment, never
the symptom:

```bash
uv add <package>
```

(In a containerized project: `uv add --no-sync <package>`.) Never
`pip install` into anything by hand — the sandbox only grants the
locked environment, so out-of-lock installs are invisible to recipes by
design.

## `blocked by lc sandbox: cannot execute/read …`

The recipe touched something outside its declared set. The message
itself carries both remedies — declare the tool in
`[tool.lightcone.image]` (containerizes the project), or declare the
file as an input in `astra.yaml`. To investigate:

```bash
lc run --sandbox-debug     # a shell inside the sandbox
```

## A recipe fails with a permissions/missing-file error but no denial message

Some programs swallow the underlying `PermissionError` and report
something else. Every failing sandboxed recipe prints the trailer
pointing at `lc run --sandbox-debug` — start there and try the exact
failing command inside the sandbox shell.

## `the environment image lc-env-… is not built — run: lc build`

`lc run` never builds images (a two-second probe must not silently
absorb a multi-minute build). Run `lc build` once; `lc materialize`
builds automatically and announces it.

## `environment changed: N materialized output(s) are now stale`

Not an error — the lock (or system layer) changed since those outputs
were produced, so their recorded environment no longer matches.
`lc materialize` re-runs exactly what's stale.

## `environment changed mid-run`

The lock or `pyproject.toml` was edited while `lc materialize` was
running. Finish environment edits, then re-run — the double gate exists
so no manifest can claim an environment its recipe didn't run under.

## `uv.lock contains unauditable dependencies`

The lock references a path/directory/editable dependency (other than
the project's own package). Those bytes aren't pinned by the lock, so
provenance can't cover them — pin the dependency to a registry, or
vendor the files as declared inputs.

## `No astra.yaml found`

You're outside a project. `lc` discovers the project by walking up to
the nearest `astra.yaml`; run `lc init` to create one.

## macOS: `… lies outside the podman machine's shared directories`

The project (or a declared input) isn't visible inside podman's Linux
VM. The message names the fix:

```bash
podman machine set --volume /path/shown/in/the/error
podman machine stop && podman machine start
```

## `Another lc materialize holds the lock`

A concurrent run (or a crashed one whose process is still alive) holds
the project's run lock. Wait for it, or if you're certain it's gone,
delete the lockfile the message names.
