# lightcone.engine.runner

Execution backends for ASTRA recipes: Docker/Podman, local subprocess, venv, and SLURM.

## `ASTRAContainerRunner`

```python
class ASTRAContainerRunner:
    def __init__(
        self,
        project_root: str,
        backend: str = "docker",
        default_container: str | None = None,
        target_config: dict | None = None,
        container_runtime: str | None = None,
    ): ...
```

Executes ASTRA recipes via Docker, local subprocess, venv, or SLURM.

**Constructor parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `project_root` | `str` | — | Absolute path to ASTRA project directory |
| `backend` | `str` | `"docker"` | Execution backend: `"docker"`, `"local"`, `"venv"`, `"slurm"` |
| `default_container` | `str \| None` | `None` | Analysis-level container image (per-recipe overrides this) |
| `target_config` | `dict \| None` | `None` | Parsed SLURM target config (used when `backend="slurm"`) |
| `container_runtime` | `str \| None` | `None` | Local container runtime binary (`"docker"` or `"podman"`) |

### `execute(command, output_id, universe_id, ...) → ExecutionResult`

Dispatches to the configured backend. Falls back to venv/local on Docker failure.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `command` | `str` | Recipe command string (without CLI args) |
| `output_id` | `str` | Output identifier |
| `universe_id` | `str` | Universe being materialised |
| `container` | `str \| None` | Per-recipe container override |
| `inputs` | `list[str] \| None` | Input IDs this recipe depends on |
| `resources` | `dict \| None` | Resource requirements (cpus, memory, gpus, etc.) |
| `params` | `dict \| None` | Universe decision parameters (injected as CLI args) |
| `external_inputs` | `dict \| None` | `{input_id: source_path}` for external data |
| `cwd_override` | `str \| None` | Working directory (for sub-analysis recipes) |

---

## `ExecutionResult`

```python
@dataclass
class ExecutionResult:
    exit_code: int
    output_path: Path
    metadata: dict[str, Any] = field(default_factory=dict)
```

**`metadata` keys:**

| Key | Description |
|-----|-------------|
| `"backend"` | Backend name used (`"docker"`, `"local"`, `"venv"`, `"slurm"`) |
| `"stdout"` | Last 2000 chars of stdout |
| `"stderr"` | Last 2000 chars of stderr |
| `"container_command"` | Full Docker/Podman CLI command (container backends) |
| `"slurm_job_id"` | SLURM job ID (SLURM backend) |

---

## Module helpers

### `translate_resources_to_docker_flags(resources) → list[str]`

Converts ASTRA `resources:` dict to Docker `--cpus`, `--memory`, `--gpus` flags.

### `translate_resources_to_slurm_directives(resources, ...) → list[str]`

Converts ASTRA `resources:` dict to SLURM `#SBATCH` directives.

### `generate_sbatch_script(command, container, ...) → str`

Generates a complete `sbatch` script string from recipe parameters and target config.

---

## Backend dispatch

`execute()` selects a backend in this order:

```
backend == "local"  → _run_local()
backend == "slurm"  → _run_slurm()
backend == "venv"   → _run_venv()
otherwise           → _run_container()
                       ↓ on failure
                      _run_venv()   (if .venv exists)
                       ↓
                      _run_local()  (last resort)
```

## Resource translation table

| ASTRA field | Docker flag | SLURM directive |
|-------------|------------|-----------------|
| `cpus` | `--cpus=N` | `--cpus-per-task=N` |
| `memory` | `--memory=Xg` | `--mem=Xg` |
| `gpus` (per-node) | `--gpus=N` | `--gpus-per-node=N` |
| `nodes` | — | `--nodes=N` |
| `time_limit` | — | `--time=HH:MM:SS` |

## CLI argument injection

Universe decisions are always appended:

```
{recipe.command} --universe {universe_id} --{key1} {val1} --{key2} {val2} ...
```

## venv dependency management

`_run_venv()` calls `_ensure_venv_deps()` once per runner instance. This runs `pip install -q -r requirements.txt` into `.venv/`. Gated by `_venv_deps_checked` flag.
