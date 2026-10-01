# lc build

Build the project's system-layer image, and commit it. Containerized
projects only — a project containerizes by declaring a
`[tool.lightcone.image]` table in `pyproject.toml`, and on a direct
project this verb just says so and exits.

## Synopsis

```text
lc build [OPTIONS]
```

Idempotent: an image that is already built and committed is left
alone.

## What the image is

The image is the *system layer* only: the declared base (digest-pinned,
or the default), the declared apt packages, and the pinned Python
interpreter. Your analysis environment is not in it — recipes' Python
packages come from the project's lock, synced into the container at run
time — and neither is `lc` itself. That is what makes "editing code
never rebuilds the image" structural: no project file enters the build
context at all.

The declaration is a closed set of keys, hashed into the image's
identity:

```toml
[tool.lightcone.image]
base = "docker.io/library/debian@sha256:..."   # optional; default pinned by lc
apt-install = ["libfftw3-dev"]                 # optional
run-commands = ["curl -L ... | tar xz"]        # optional, the bounded escape
env = { OMP_NUM_THREADS = "1" }                # optional
```

## The archive is the store

`lc build` saves the built image into the repository —
`.datalad/environments/<tag>/image`, a `docker-archive` committed
through git-annex — so the exact bytes travel with the project: a
clone obtains them with a fetch, no registry and no credentials
involved. Execution always pins the image's content *id*, never a tag,
so nothing can substitute a different image under the same name.

The archive records the architecture it was built for, and a host that
can't execute that architecture is refused up front — build where the
architecture matches the machines that will run recipes (on NERSC, a
login node).

## Requirements

- A clean tree — the image commit must not sweep your staged edits in,
  and the tag derives from the committed declaration.
- A build-capable runtime: `podman-hpc`, `podman`, or `docker`
  (detected in that order; nothing to configure).

`lc materialize` also builds as a preflight when the committed
declaration has no image yet, announcing it first — `lc build` exists
so you can pay the minutes when *you* choose to.

## Options

| Option | Default | Effect |
|--------|---------|--------|
| `--json` | off | Emit the result as JSON on stdout. |

## The JSON result

```json
{
  "mode": "containerized",
  "tag": "lc-env-1a2b3c4d5e6f7a8b",
  "id": "sha256:...",
  "archive": ".datalad/environments/lc-env-1a2b3c4d5e6f7a8b/image",
  "action": "built"
}
```

`action` is `"built"` when this invocation built and committed the
image, `"present"` when it was already there. On a direct project the
result is just `{"mode": "direct"}`.

## Examples

```bash
lc build           # build + commit, or confirm it's already there
lc build --json    # the machine-readable form
```
