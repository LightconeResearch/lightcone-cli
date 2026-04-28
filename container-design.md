# Design: Containerized Claude Code environment (`lc launch`)

> Companion to `redesign.md`. Extends the Snakemake-based execution layer with a
> fully containerized development environment: `lc launch claude` spawns a sandboxed
> Claude Code session with `lc` tools and a nested container runtime pre-installed.
> All recipe building and execution happens inside this environment.

---

## Context

The redesign.md establishes Snakemake as the execution backbone and content-addressed
OCI manifests as the integrity layer. The missing piece is the **entry point**: today,
a user who opens a project has no standardised, sandboxed environment in which to run
the agent. They rely on host-installed tools, host Python, and host container runtimes —
none of which are version-pinned or reproducible across machines or collaborators.

This design adds `lc launch <target>` as the canonical entry point. After
`lc init my-project && cd my-project`, the first and only command is
`lc launch claude`. Everything else — planning the workflow with the agent,
building recipe images, running analyses — happens inside the container.

---

## Design decisions

| Decision | Choice | Rationale |
|---|---|---|
| Nested container strategy | True nested (option C) | Full reproducibility: each recipe runs in its own image |
| Image format | OCI tarballs (`.lightcone/images/*.tar`) | Runtime-neutral, portable, file-based provenance |
| Claude container source | Built locally from bundled Containerfile | No registry dependency; version-controlled alongside `lc` |
| Inner build tool | `buildah` | Daemonless, rootless, OCI-native; pairs with Apptainer |
| Inner execution tool | `apptainer exec oci-archive:` | Reads OCI tarballs directly; no load step; HPC-native |
| HPC compatibility | `/dev/fuse` passthrough + `podman-hpc` outer runtime | Perlmutter-tested pattern; FUSE overlay enables both tools |
| `lc launch` scope | General dispatch (`lc launch <target>`) | `claude` is first target; pattern extensible |
| Enforcement | `LIGHTCONE_CONTAINER=1` env var + warning | Host-side still works; in-container use is encouraged |

---

## User-facing workflow

```
lc init my-project && cd my-project
lc launch claude                    ← always the first step
  [inside container]
  [Claude: define question, plan workflow, write astra.yaml + Containerfiles]
  lc build                          ← buildah builds recipe images → .lightcone/images/*.tar
  lc run                            ← snakemake + apptainer exec oci-archive:... per rule
  lc status / lc verify             ← offline manifest checks (unchanged)
  exit
lc launch claude                    ← resume; .lightcone/images/ tarballs persist on host
```

---

## Architecture

```
host
├── lc launch claude
│     ├── detect host runtime (docker / podman / podman-hpc)
│     ├── render .lightcone/containers/claude-env.Containerfile (LIGHTCONE_VERSION substituted)
│     ├── build lc-claude-env-<hash> if tarball absent   ← host runtime builds it
│     ├── save to .lightcone/images/lc-claude-env-<hash>.tar
│     └── exec -it <image>
│           -v <project_abs>:<project_abs> -w <project_abs>
│           -e ANTHROPIC_API_KEY --device /dev/fuse
│
└── [inside claude container]
      ├── claude (TUI)
      ├── lc build  → buildah build → buildah push oci-archive:.lightcone/images/<tag>.tar
      └── lc run    → snakemake
                         └── per rule: apptainer exec oci-archive:.lightcone/images/<tag>.tar
                                       write_manifest()  (host-side Python)
```

**What runs where:**

| Component | Location | Tool |
|---|---|---|
| `lc launch` itself | host | docker / podman / podman-hpc |
| Claude Code TUI | inside Claude container | — |
| `lc build` (recipe images) | inside Claude container | `buildah` |
| `lc run` / snakemake orchestration | inside Claude container | — |
| Per-recipe shell commands | nested recipe container | `apptainer exec oci-archive:` |
| `write_manifest()` | inside Claude container | Python (host-side of rule) |

---

## New file: `claude/lightcone/containers/claude-env.Containerfile`

```dockerfile
FROM ubuntu:24.04

# FUSE support — required by both Apptainer and buildah overlay storage
RUN apt-get update && apt-get install -y --no-install-recommends \
    fuse3 libfuse2 squashfuse \
    buildah \
    git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Apptainer — pinned, installed from .deb
ARG APPTAINER_VERSION=1.4.0
RUN curl -fsSL \
    https://github.com/apptainer/apptainer/releases/download/v${APPTAINER_VERSION}/apptainer_${APPTAINER_VERSION}_amd64.deb \
    -o /tmp/apptainer.deb \
    && dpkg -i /tmp/apptainer.deb && rm /tmp/apptainer.deb

# Python + uv + lightcone-cli (version injected at render time, not build-time ARG)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"
ARG LIGHTCONE_VERSION
RUN uv pip install --system "lightcone-cli==${LIGHTCONE_VERSION}"

# Node.js LTS + Claude Code CLI
ARG NODE_VERSION=22
RUN curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && rm -rf /var/lib/apt/lists/*

# Marker checked by lc build / lc run
ENV LIGHTCONE_CONTAINER=1

WORKDIR /workspace
ENTRYPOINT ["claude"]
```

**Version pinning:** `lc launch` renders a copy of this Containerfile to
`.lightcone/containers/claude-env.Containerfile` with `${LIGHTCONE_VERSION}`
substituted to the running `lc` version string. `compute_image_tag()` hashes
the rendered file, so upgrading `lc` automatically invalidates the cached image.

---

## New module: `src/lightcone/engine/launcher.py` (~150 LOC)

```python
@dataclass(frozen=True)
class LaunchTarget:
    name: str
    containerfile: Path        # source Containerfile (in lightcone package)
    entrypoint: list[str]
    env_passthrough: list[str]
    devices: list[str]         # e.g. ["/dev/fuse"]

BUILTIN_TARGETS: dict[str, LaunchTarget] = {
    "claude": LaunchTarget(
        name="claude",
        containerfile=_package_containers_dir() / "claude-env.Containerfile",
        entrypoint=["claude"],
        env_passthrough=["ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "HOME", "TERM"],
        devices=["/dev/fuse"],
    ),
}
```

`resolve_launch_target(name, project_root)` — looks up built-in targets first,
then `.lightcone/launch/<name>.yaml` for project-local targets.

`_package_containers_dir() -> Path` — returns the path to
`claude/lightcone/containers/` inside the installed lightcone package
(resolved via `importlib.resources` or `Path(__file__).parent / "containers"`).

`_render_containerfile(target, project_root) -> Path` — copies the source
Containerfile to `.lightcone/containers/<name>.Containerfile`, substituting
`ARG LIGHTCONE_VERSION` with `ARG LIGHTCONE_VERSION=<running_lc_version>` so
the content hash (and therefore image tag) changes whenever `lc` is upgraded.

`_compute_launch_image_tag(rendered_cf) -> str` — calls `compute_image_tag()`
with `project_name=target.name`, `containerfile=rendered_cf`, and
`project_path=rendered_cf.parent` (the `.lightcone/containers/` dir has no
additional dependency files to hash, which is correct — the rendered Containerfile
already encodes the `lc` version and Apptainer version).

`launch_target(name, *, choice, project_root)`:
1. Resolve target definition
2. Render Containerfile → `.lightcone/containers/<name>.Containerfile`
3. Compute image tag from rendered Containerfile
4. Build + save tarball if absent (`tarball_path_for_tag()` in `.lightcone/images/`)
5. Load into runtime store if not already present (`load_image_from_tarball()`)
6. `exec` the interactive container (replaces the current process, never returns)

Mount strategy: `-v <project_abs>:<project_abs> -w <project_abs>`.
Snakemake output paths, manifest paths, and tarball paths are identical
inside and outside the container — no path translation.

`podman-hpc` specifics: `_exec_interactive()` adds `--no-setns` and ensures
`/dev/fuse` is in the device list (already used elsewhere in the codebase).

---

## Changes to `src/lightcone/engine/container.py`

### New runtime: `"apptainer"`

```python
RUNTIMES = ("podman", "docker", "podman-hpc", "apptainer")
# detect_runtime() checks shutil.which() for each in order
```

### `build_image()` — apptainer branch

```python
if runtime == "apptainer":
    subprocess.run(
        ["buildah", "build", "--format=oci", f"--tag={tag}", str(context)],
        check=True,
    )
    tarball = tarball_path_for_tag(tag, project_path)
    subprocess.run(["buildah", "push", tag, f"oci-archive:{tarball}"], check=True)
```

### `build_image()` — existing docker/podman/podman-hpc branch

Unchanged except one added line after the existing build call:
```python
save_image_as_tarball(tag, tarball_path_for_tag(tag, project_path), runtime=runtime)
```

### `image_exists_locally()` — apptainer branch

```python
if runtime == "apptainer":
    return tarball_path_for_tag(tag, project_path).exists()
```

### `wrap_recipe()` — apptainer branch

```python
if runtime == "apptainer":
    tarball = f".lightcone/images/{image}.tar"
    return f"apptainer exec --fakeroot oci-archive:{tarball} bash -c {shlex.quote(recipe)}"
```

### New helpers

```python
def save_image_as_tarball(tag: str, tarball_path: Path, *, runtime: str) -> None:
    """<runtime> save <tag> > <tarball_path>  (streaming, no RAM buffer)"""

def load_image_from_tarball(tarball_path: Path, *, runtime: str) -> None:
    """<runtime> load -qi <tarball_path>  — loads into the runtime's local store"""

def tarball_path_for_tag(tag: str, project_path: Path) -> Path:
    return project_path / ".lightcone" / "images" / f"{tag}.tar"
```

---

## Changes to `src/lightcone/cli/commands.py`

### New command: `lc launch <target>`

```python
@main.command("launch")
@click.argument("target")
def launch(target: str):
    """Launch an interactive containerized environment for this project."""
    project = _project_root()
    choice = load_runtime(project_path=project)
    launcher.launch_target(target, choice=choice, project_root=project)
```

### Enforcement warning in `lc build` and `lc run`

```python
_CONTAINER_WARNING = (
    "⚠  Running outside the Claude container. "
    "Use [bold]lc launch claude[/bold] for the full sandboxed workflow."
)

def _warn_if_not_containerized(console: Console) -> None:
    if not os.environ.get("LIGHTCONE_CONTAINER"):
        console.print(_CONTAINER_WARNING)
```

Called at the top of both `build()` and `run()` command functions.

---

## Files changed / created

| File | Change |
|---|---|
| `src/lightcone/engine/launcher.py` | NEW (~150 LOC) — LaunchTarget, resolve, launch |
| `claude/lightcone/containers/claude-env.Containerfile` | NEW — Claude Code environment |
| `src/lightcone/engine/container.py` | Add apptainer runtime, buildah build path, tarball helpers |
| `src/lightcone/cli/commands.py` | Add `lc launch`, add `_warn_if_not_containerized()` |
| `tests/test_launcher.py` | NEW — target resolution, render, tag, launch smoke test |
| `tests/test_container.py` | Add apptainer/buildah branch tests, tarball helper tests |

Files **not** changed: `snakefile.py`, `manifest.py`, `status.py`, `verify.py`.
The `runtime` parameter already flows through `snakefile.generate()`; adding
`"apptainer"` to `container.py` is sufficient.

---

## Open questions (resolved)

1. **Nested strategy** → Option C (true nested containers). ✓
2. **Image format** → OCI tarballs in `.lightcone/images/`. ✓
3. **Claude container source** → Built locally from bundled Containerfile. ✓
4. **Inner build tool** → `buildah` (installed in claude-env). ✓
5. **Inner execution tool** → `apptainer exec oci-archive:`. ✓
6. **`lc build` host-side** → Still works, prints enforcement warning. ✓
7. **`lc launch` scope** → General dispatch; `claude` is first built-in target. ✓

---

## Verification

End-to-end test path:

1. `lc init test-project && cd test-project`
2. `lc launch claude` — should detect host runtime, build `lc-claude-env-<hash>.tar`,
   launch container, drop into Claude Code TUI
3. Inside container: `lc build` — should produce `.lightcone/images/lc-<name>-<hash>.tar`
4. Inside container: `lc run` — snakemake rule should invoke
   `apptainer exec oci-archive:.lightcone/images/...tar`, manifest written afterward
5. Inside container: `lc verify` — chain should validate
6. Exit container; re-enter with `lc launch claude` — tarballs still present,
   `lc build` skips (already built), `lc run` executes normally

Unit tests:
- `test_launcher.py`: `resolve_launch_target("claude")`, `_render_containerfile()`,
  `compute_image_tag()` stability, `launch_target()` smoke (mock subprocess)
- `test_container.py`: `tarball_path_for_tag()`, `image_exists_locally()` for apptainer,
  `wrap_recipe()` apptainer branch, `build_image()` apptainer branch (mock buildah)

Perlmutter-specific: `lc launch claude` with `runtime=podman-hpc` should add
`--no-setns` and `--device /dev/fuse` to the outer run command.
