# lightcone.engine.container

The container layer. Two surfaces: build-time (`compute_image_tag`,
`build_image`, `pull_image`) and run-time wrap (`wrap_recipe`,
`make_image_tag_resolver`).

Source: `src/lightcone/engine/container.py`.

## Constants

| Constant | Value |
|----------|-------|
| `RUNTIMES` | `("podman", "docker", "podman-hpc")` — detection priority order |
| `DEPENDENCY_FILES` | `("requirements.txt", "requirements-dev.txt", "requirements-test.txt", "pyproject.toml", "setup.py", "setup.cfg", "poetry.lock", "Pipfile.lock")` |

Detection priority is podman before docker for two reasons: it's
rootless (less surprising on shared machines), and the docker probe
includes `docker info` so a stopped daemon doesn't silently win over a
healthy podman.

## Runtime detection

### `detect_runtime() → str | None`

Returns the first usable runtime in `RUNTIMES`. "Usable" means the
binary is on PATH and (for docker) `docker info` succeeds. Returns
`None` if nothing's available.

### `load_runtime(*, project_path=None) → RuntimeChoice`

Resolve the runtime to use. Reads `container.runtime` from
`~/.lightcone/config.yaml`:

- `auto` (default) → first available, else `"none"` with `explicit=False`.
- `docker | podman | podman-hpc` → explicit; binary must exist or
  raises `ContainerBuildError`.
- `none` → explicit opt-out.
- Anything else → `ContainerBuildError`.

`project_path` is accepted for future per-project overrides but is not
consulted today.

### `RuntimeChoice` (dataclass)

```python
@dataclass(frozen=True)
class RuntimeChoice:
    runtime: str         # docker | podman | podman-hpc | none
    explicit: bool       # True if pinned, False if `auto` produced this
```

`explicit=False` + `runtime="none"` means auto fell back silently. Callers
should warn — that case mismatches the manifest's recorded
`container_image` against what actually executed.

## Image tag computation

### `compute_image_tag(project_name, containerfile, project_path) → str`

Returns `lc-<sanitized-name>-<sha256[:12]>`. The hash covers the
Containerfile contents plus every dependency file from `DEPENDENCY_FILES`
that exists at the project root.

Sanitization: lowercase + spaces → hyphens.

### `find_dependency_files(project_path) → list[Path]`

Sorted list of dependency files actually present. Used by
`compute_image_tag`.

### `hash_file_contents(files) → str`

Concatenated SHA-256 hex digest of the listed files. Internal helper.

### `is_containerfile(spec, project_path) → bool`

True if `spec` resolves to an existing file (i.e. it's a Containerfile,
not a registry image).

## Build

### `build_image(tag, containerfile, context, *, runtime, build_args=None) → ContainerBuildResult`

Run `<runtime> build -t <tag> -f <containerfile> [--build-arg …] <context>`.
For `podman-hpc`, also runs `podman-hpc migrate <tag>` so compute nodes
can read the image. Raises `ContainerBuildError` on any failure.

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

### `make_image_tag_resolver(project_path, project_name) → Callable`

Returns a memoizing wrapper around `resolve_image_for_run`. Multiple
outputs typically share a Containerfile; resolving re-hashes the file
plus all dependency files (lockfiles can be megabytes), so caching by
spec string for the lifetime of the caller's loop matters.

### `resolve_image_for_run(spec, *, project_path, project_name) → str | None`

Translate an `astra.yaml` `container:` value into the image tag the
runtime will execute:

- `None` / empty → `None`
- Containerfile path → `lc-<name>-<hash>` (the tag `lc build` would
  produce)
- Anything else → returned as-is

## Status

### `get_container_status(spec, project_path, project_name, *, runtime) → ContainerStatus`

Without building or pulling, return a `ContainerStatus` describing what
would happen.

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
