# lightcone.engine.container

The container layer. Two surfaces: build-time (`compute_image_tag`,
`build_image`, `pull_image`) and run-time wrap (`wrap_recipe`,
`make_image_tag_resolver`).

Source: `src/lightcone/engine/container.py`.

## Constants

| Constant | Value |
|----------|-------|
| `RUNTIMES` | `("podman-hpc", "podman", "docker")` — detection priority order |
| `KUBERNETES` | `"kubernetes"` — deliberately **not** in `RUNTIMES` |
| `REGISTRY_ENV` | `"LIGHTCONE_REGISTRY"` — deployment-injected registry prefix |
| `DEPENDENCY_FILES` | `requirements{,-dev,-test}.txt`, `pyproject.toml`, `setup.py`, `setup.cfg`, `poetry.lock`, `Pipfile.lock`, `uv.lock`, `conda-lock.yml`, `environment.y{a,}ml` |

Detection order: `podman-hpc` first, because anyone who installed the HPC
wrapper did so on purpose and plain podman would build images compute
nodes can't read; then podman (rootless, no daemon); docker last, gated
behind a `docker info` probe so a down daemon doesn't silently win over a
healthy podman.

`kubernetes` is separate from `RUNTIMES` because it names no binary and
is never found on PATH. It is selected only by site detection (a Dask
Gateway deployment — see [api/site_registry](site_registry.md)) or by an
explicit pin in `~/.lightcone/config.yaml`, and it takes a different code
path throughout: no local build, no recipe wrap, registry refs instead of
local-store tags.

## Runtime detection

### `detect_runtime() → str | None`

Returns the first usable runtime in `RUNTIMES`. "Usable" means the
binary is on PATH and (for docker) `docker info` succeeds. Returns
`None` if nothing's available.

The host site's declared `container_runtime` is moved to the front of the
order (`_detection_order()`), but it is only a *hint* — a
missing-from-PATH preference falls through to the next candidate. The one
exception: a site declaring `container_runtime: kubernetes` short-circuits
PATH probing entirely, since there is no binary to find.

### `load_runtime(*, project_path=None) → RuntimeChoice`

Resolve the runtime to use. Reads `container.runtime` from
`~/.lightcone/config.yaml`:

- `auto` (default) → first available, else `"none"` with `explicit=False`.
  On a site declaring `container_runtime: kubernetes` (a Dask Gateway
  deployment), auto resolves to `kubernetes` with no PATH probing.
- `docker | podman | podman-hpc` → explicit; binary must exist or
  raises `ContainerBuildError`.
- `kubernetes` → explicit; no binary involved (the worker pod is the
  container).
- `none` → explicit opt-out.
- Anything else → `ContainerBuildError`.

`project_path` is accepted for future per-project overrides but is not
consulted today.

### `RuntimeChoice` (dataclass)

```python
@dataclass(frozen=True)
class RuntimeChoice:
    runtime: str         # docker | podman | podman-hpc | kubernetes | none
    explicit: bool       # True if pinned, False if `auto` produced this
```

`explicit=False` + `runtime="none"` means auto fell back silently. Callers
should warn — that case mismatches the manifest's recorded
`container_image` against what actually executed.

## Image identity

There is **one** content-addressed identity, spelled two ways depending
on where the image lives. Both spellings carry the same digest on every
backend, so a `code_version` computed by the Snakefile generator and one
computed by the status walker can never disagree.

### `image_identity(project_name, containerfile, project_path) → (safe_name, digest)`

The source of truth. The 12-char sha256 digest covers:

1. The **Containerfile** contents.
2. Every **dependency file** from `DEPENDENCY_FILES` present at the
   project root.
3. The contents of every **`COPY`/`ADD` source** referenced from the
   Containerfile — files hashed directly, directories walked recursively
   in sorted relative-path order. `COPY .` therefore hashes the whole
   build context.

Each entry is framed with a kind label and its project-relative path
before its bytes, so moving a line between files (or renaming a file)
changes the digest.

`_parse_copy_sources()` handles backslash continuations and the JSON exec
form (`COPY ["src", "dest"]`), and skips what isn't in the host build
context: `--from=<stage>` copies, URLs, and `git@` arguments. Heredoc
`COPY <<EOF` is not interpreted. Paths that escape the project root are
dropped. Directory walks skip `_COPY_DIR_EXCLUDE` subtrees (`.git`,
`.venv`, `results`, `.lightcone`, `.snakemake`, caches, `node_modules`,
`dist`, …) so the tag doesn't churn when someone touches `results/` or
runs the tests.

The same iteration (`_iter_build_context_entries`) is used to *stage* the
build context in `_populate_build_context()`, which guarantees by
construction that the hashed set and the built set are identical.

### `compute_image_tag(project_name, containerfile, project_path) → str`

The local-image-store spelling: `lc-<sanitized-name>-<digest>`.
Sanitization is lowercase + spaces → hyphens.

### `registry_image_ref(project_name, containerfile, project_path, *, registry) → str`

The registry spelling: `<registry>/lc-<name>:<digest>` — the digest moves
into the tag position so one repository per project accumulates its image
history.

### `deployment_registry() → str | None`

The deployment-injected `LIGHTCONE_REGISTRY` prefix (trailing slash
stripped), or `None` off-deployment.

### `runtime_registry(runtime) → str | None`

Which spelling a given runtime resolves identities against: the
deployment registry on `kubernetes`, `None` (local-store tags)
everywhere else. Shared by the Snakefile generator and the status walker.

### `find_dependency_files(project_path) → list[Path]`

Sorted list of dependency files actually present.

### `hash_file_contents(files) → str`

Framed SHA-256 digest over a list of files. A standalone helper — the
image digest uses the richer `image_identity()` path above.

### `is_containerfile(spec, project_path) → bool`

True if `spec` resolves to an existing file (i.e. it's a Containerfile,
not a registry image).

## Build

### `build_image(tag, containerfile, context, *, runtime, build_args=None) → ContainerBuildResult`

Run `<runtime> build -t <tag> -f <containerfile> [--build-arg …] <context>`
against a **staged** context (`_populate_build_context()` mirrors the
Containerfile and its `COPY`/`ADD` sources into a tempdir — NERSC's DVS
mounts don't implement `llistxattr`, which buildah calls unconditionally
on every `COPY` source). For `podman-hpc`, also runs
`podman-hpc migrate <tag>` so compute nodes can read the image.

Raises `ContainerBuildError` on any failure, and immediately for a
`runtime` outside `RUNTIMES` — `kubernetes` has no local builder and goes
through [`engine.cloudbuild`](cloudbuild.md) instead.

### `pull_image(image, *, runtime) → None`

Run `<runtime> pull <image>`, then (for podman-hpc) `migrate`. Used by
`lc build` to pre-stage registry images so `lc run` can pass
`--pull=never`.

### `image_exists_locally(tag, *, runtime) → bool`

Check the local image store. Routes to `image_exists_podman_hpc(tag)`
for `podman-hpc`, otherwise runs `<runtime> image inspect <tag>`.

### `_podman_hpc_migrate(tag)` (private)

Wraps `podman-hpc migrate`. Raises `ContainerBuildError` on failure.

## Run-time wrap

### `wrap_recipe(recipe, *, image, runtime) → str`

Wrap `recipe` so it executes inside `image` under `runtime`. Returns a
shell-command string for Snakemake's `shell()`.

No-op cases (`recipe` returned unchanged):

- `image is None`
- `runtime == "none"`
- `runtime == "kubernetes"` — the Dask worker pod executing the recipe
  was started from `image`; wrapping would containerize twice. The
  image still flows into `code_version` and the manifest.

Otherwise produces:

```bash
<runtime> run --rm --pull=never \
  -v "$PWD":"$PWD" -w "$PWD" \
  <image> bash -c '<shlex.quote(recipe)>'
```

`--pull=never` is critical: it sidesteps podman's
`unqualified-search-registries` resolution, which fails for our
content-addressed `lc-<name>-<hash>` tags. The cost: registry images
have to be pre-pulled by `lc build`.

The bind mount and `-w "$PWD"` ensure recipes that write to relative
paths land in the project tree. Snakemake invokes us with `cwd=project`,
so `$PWD` is the project root.

Snakemake placeholders inside `recipe` (`{output[0]}`, `{input.X}`,
`{wildcards.universe}`) are preserved — they substitute through Python's
`str.format` at execution time, after wrapping.

### `make_image_tag_resolver(project_path, project_name, *, registry=None) → Callable`

Returns a memoizing wrapper around `resolve_image_for_run`. Multiple
outputs typically share a Containerfile; resolving re-hashes the file,
all dependency files (lockfiles can be megabytes), and every `COPY`
source, so caching by spec string for the lifetime of the caller's loop
matters. Pass `registry=runtime_registry(runtime)` to get registry refs.

### `resolve_image_for_run(spec, *, project_path, project_name, registry=None) → str | None`

Translate an `astra.yaml` `container:` value into the image tag the
runtime will execute:

- `None` / empty → `None`
- Containerfile path → `lc-<name>-<hash>` (the tag `lc build` would
  produce), or `<registry>/lc-<name>:<hash>` when `registry` is given
  (a deployment with a remote builder — same content-addressed
  identity, spelled for a registry)
- Anything else → returned as-is

## Status

### `get_container_status(spec, project_path, project_name, *, runtime) → ContainerStatus`

Without building or pulling, return a `ContainerStatus` describing what
would happen. On the `kubernetes` runtime with a deployment registry set,
`exists` comes from
[`cloudbuild.registry_image_exists()`](cloudbuild.md) — a registry HEAD
rather than a local image-store probe — and `image` is the registry ref.

### `ContainerStatus` (dataclass)

```python
@dataclass
class ContainerStatus:
    type: str                                # "none" | "prebuilt" | "build"
    image: str | None = None                 # the tag (always set for "prebuilt"/"build")
    exists: bool | None = None               # local-store presence (None for "none" runtime)
    containerfile: str | None = None         # the spec, only set for "build"
```

## Exceptions

### `ContainerBuildError`

Raised by `build_image`, `pull_image`, `_podman_hpc_migrate`, and
`load_runtime` (configuration errors). Message carries the failing
runtime and stderr.

## Tests

`tests/test_container.py` covers detection, image tag computation,
build invocation, recipe wrapping, and the `RuntimeChoice` resolution
matrix.
