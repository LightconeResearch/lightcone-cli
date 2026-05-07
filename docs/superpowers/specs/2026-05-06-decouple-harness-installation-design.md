# Design Spec: Decouple Harness Installation from Container Image

**Date:** 2026-05-06  
**Branch:** sandboxing-execution  
**Status:** Implemented

---

## Problem

The original `lc launch claude` command shipped a single `claude-env.Containerfile` that bundled both system tooling (Apptainer, buildah, Node.js, uv) and the Claude Code CLI into one published image. This caused two problems:

1. **Licensing**: Publishing an image containing Claude Code (a proprietary CLI) raises redistribution concerns.
2. **Extensibility**: Supporting a second harness (Mistral Vibe, OpenCode) required a separate published image per harness, bloating the registry.

---

## Solution

Publish one neutral `lightcone-sandbox` base image (system tools only). On first `lc launch <harness>`, install the harness inside a running container and commit the result as a local image (`lightcone-<harness>:<version>`). Subsequent launches detect the committed image and skip straight to exec.

---

## Architecture

### Base Image (`lightcone-sandbox.Containerfile`)

Renamed from `claude-env.Containerfile`. Key changes:
- Removed: `npm install -g @anthropic-ai/claude-code` and `ENTRYPOINT ["claude"]`
- Added: `unzip` to apt-get (required by OpenCode install script)
- Changed: `ENTRYPOINT ["bash"]`
- Kept: Python 3.12-slim-bookworm, FUSE, Apptainer 1.4.0, buildah, Node.js LTS, uv, lightcone-cli, `LIGHTCONE_CONTAINER=1`

Published as: `ghcr.io/lightconeresearch/lightcone-sandbox:<version>`  
No harness-specific images are published.

### LaunchTarget Dataclass

Two new fields added to `LaunchTarget` (frozen dataclass in `launcher.py`):

```python
install_cmds: list[str]      # shell commands joined with " && " and run via sh -c
committed_tag_prefix: str    # e.g. "lightcone-claude" → tag "lightcone-claude:<lc_version>"
```

The `entrypoint` field (pre-existing) now carries the full binary + args, e.g. `["claude", "--dangerously-skip-permissions"]`. The launcher passes `--entrypoint <binary>` before the image tag and appends remaining args after.

### Harness Targets

All three harnesses share the `lightcone-sandbox.Containerfile` as their base and `registry_name="lightcone-sandbox"` for GHCR pull. The constant `_SANDBOX_IMAGE_NAME = "lightcone-sandbox"` is used throughout.

| Field | claude | mistral-vibe | opencode |
|---|---|---|---|
| `install_cmds` | `npm install -g @anthropic-ai/claude-code` | `uv tool install mistral-vibe` | `npm install -g opencode-ai` |
| `committed_tag_prefix` | `lightcone-claude` | `lightcone-mistral-vibe` | `lightcone-opencode` |
| `entrypoint` | `["claude", "--dangerously-skip-permissions"]` | `["vibe"]` | `["opencode"]` |
| `env_passthrough` | `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `CLAUDE_CODE_OAUTH_TOKEN`, `HOME`, `TERM` | `MISTRAL_API_KEY` | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `MISTRAL_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY` |
| `run_as_host_user` | `True` | `False` | `False` |

### Home Mounts (Granular Strategy)

`home_mounts` is an explicit list of sub-paths of `$HOME` to bind-mount. Trailing `/` = directory, others = file. Missing host paths are auto-created before mounting via `_ensure_host_path()`.

**Excluded** (never mounted — sensitive or ephemeral):
- claude: `.claude/projects/`, `.claude/logs/`, `.claude/statsig/`
- mistral-vibe: `.vibe/logs/`, `.vibe/.env`
- opencode: `.local/share/opencode/storage/`

### `_ensure_harness_image()` Flow

```
committed_tag = f"{target.committed_tag_prefix}:{lc_version}"

if not reinstall and image_exists_locally(committed_tag):
    return committed_tag          # fast path: second+ launch

if reinstall:
    rmi committed_tag             # remove old image to avoid dangling layers

tmp = "lc-install-<name>-<uuid8>"
try:
    docker run --name tmp base_image sh -c install_cmd
    docker commit tmp committed_tag          (capture_output=True)
except CalledProcessError:
    raise ContainerBuildError(...)
finally:
    docker rm -f tmp             (check=False, capture_output=True)

return committed_tag
```

`launch_target()` calls `_ensure_harness_image()` after loading the base image and before `_exec_interactive()`. The returned committed tag replaces the base image tag for both the exec and the tracking tag.

### `--reinstall` Flag

```
lc launch claude --reinstall
```

Forces re-installation: removes the existing committed image (to avoid dangling layer accumulation), then runs install again and commits a fresh image.

---

## Files Changed

| File | Change |
|---|---|
| `claude/lightcone/containers/lightcone-sandbox.Containerfile` | Renamed from `claude-env.Containerfile`; stripped harness install and ENTRYPOINT; added `unzip` |
| `src/lightcone/engine/launcher.py` | Added `install_cmds`, `committed_tag_prefix` to `LaunchTarget`; added `_SANDBOX_IMAGE_NAME`, `_image_exists()`, `_ensure_host_path()`, `_ensure_harness_image()`; defined 3 harness targets; updated `launch_target()` and `_exec_interactive()` |
| `src/lightcone/cli/commands.py` | Added `--reinstall` flag; updated `lc launch` help text |
| `tests/test_launcher.py` | Tests for all new helpers and harness targets; `TestLaunchTargetEnsureHarness` for end-to-end wiring |
| `tests/test_cli.py` | `test_launch_reinstall_forwarded_to_launch_target` |

---

## Key Invariants

- The base `lightcone-sandbox` image contains no proprietary software — safe to publish.
- Harness images are local-only (never pushed); they are rebuilt by `--reinstall` or when the committed tag is absent.
- Sensitive config paths (logs, session history, API key files) are never mounted into the container.
- `_ensure_harness_image` always cleans up the temp container (via `finally`), even on install failure.
- `CalledProcessError` from subprocess is always wrapped as `ContainerBuildError` — consistent with the rest of the launcher module.
