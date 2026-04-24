# Parsl-backed SLURM Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-recipe `sbatch` SLURM execution with a single-allocation Parsl pilot model — one `lc run` acquires one (or two) SLURM allocation(s) via WorkQueueExecutor and dispatches every recipe in the analysis tree into them, paying the queue wait once.

**Architecture:** Dagster keeps lineage/UI/status; Parsl replaces only the execution substrate under `backend: slurm`. The `lc run` CLI wraps `dagster.materialize(...)` in a `with parsl.load(config):` block. Each Dagster asset's `_run_slurm` body becomes a `bash_app(...).result()` call routed to the right pilot executor by recipe resources. Per-recipe sbatch generation, `sacct` polling, and per-recipe QoS clamping (~650 LOC) are deleted.

**Tech Stack:** Python 3.11, Parsl ≥ 2024.1.0, ndcctools (WorkQueue, Python bindings), Dagster ≥ 1.9, Click, pytest, ruff, mypy. Existing project uses uv + hatchling.

**Spec:** `docs/superpowers/specs/2026-04-24-parsl-slurm-execution-design.md` (commits `7cfd28e`, `71e02b7`).

---

## File map

**New files:**

- `src/lightcone/engine/parsl_backend.py` — pure functions: target dict → `parsl.Config`, recipe-resources → WorkQueue per-task spec, executor-routing, pre-flight QoS validation. ~150 LOC.
- `tests/test_parsl_backend.py` — unit tests (pure functions, no Parsl runtime). ~250 LOC.
- `tests/test_runner_parsl.py` — integration tests using `WorkQueueExecutor + LocalProvider`. ~200 LOC.
- `tests/manual/test_perlmutter_smoke.py` — manual smoke test, not in CI. ~80 LOC.
- `docs/hpc/parsl-pilot.md` — user docs for the new `pilots:` schema and `worker_init` patterns.

**Modified files:**

- `src/lightcone/engine/runner.py` — delete `_run_slurm`, `_validate_and_adjust_qos`, `_parse_time_minutes`, `generate_sbatch_script`, `_podman_hpc_run_command`, `_shell_quote`, `_parse_sbatch_job_id`, `_poll_slurm_job`, `_check_sacct`, `_check_squeue_fallback`, `translate_resources_to_slurm_directives`, `_normalise_time_limit`. Add new ~30-line `_run_slurm` that delegates to a Parsl `bash_app`. Add `build_recipe_shell_command(...)` helper (~50 LOC) — the externally-mounted-input + container-wrapping logic lifted from the deleted sbatch generator.
- `src/lightcone/engine/assets.py` — collapse the `if backend == "slurm":` branch (~55 lines) to a single line that copies `target_config["pilots"]` into `runner_config`. Drop the `resolve_run_config`/`get_option_*` imports inside the SLURM branch.
- `src/lightcone/cli/commands.py` — `lc run` wraps `dg.materialize(...)` in `with parsl.load(build_parsl_config(...)):` when `backend == "slurm"`. Drop the `--strategy` flag (now dead). Help text scrubbed to remove pilot/Parsl/SLURM internals (per agent–target contract).
- `pyproject.toml` — add `parsl >= 2024.1.0` dep; add doc note about `ndcctools` install (conda).
- `tests/test_runner.py` — delete the doomed test classes wholesale (`TestSlurmResourceTranslation`, `TestNormaliseTimeLimit`, `TestGenerateSbatchScript`, `TestSlurmRunner`, `TestCheckSacct`, `TestExternalInputs` (the slurm-script half), `TestQoSValidation`, `TestHelpers`'s sbatch-related cases). Keep `TestResourceTranslation` (Docker), `TestDockerRunner`.
- `tests/conftest.py` — add `parsl_local_pilot` fixture.
- `tests/test_cli_run.py` — patch `parsl.load` so CLI tests don't boot a DFK.
- `tests/test_assets.py` — adjust any tests that pass `target_config={"scheduler": ...}` → `target_config={"pilots": ...}`.

**Unchanged:**

- `src/lightcone/engine/slurm_info.py` — its data structures and `check_qos_eligibility` are reused by the new pilot-scope pre-flight; only the per-recipe call sites go away.
- `src/lightcone/engine/targets.py` — schema-resolution helpers stay; `OPTION_AXES`, intent options, cluster cache all keep working.

---

## Task ordering rationale

Tasks build bottom-up so each is independently testable and committable:

1. Task 1 — Dependency in place
2. Tasks 2-5 — Pure functions in `parsl_backend.py` with unit tests (resources, routing, config, validation)
3. Task 6 — Extract `build_recipe_shell_command` from old runner (pure code move)
4. Task 7 — Rewrite `_run_slurm` body on top of `bash_app`, with LocalProvider integration tests
5. Task 8 — `apply_cli_overrides_to_pilots` helper
6. Task 9 — Wire `parsl.load` into `lc run`; drop dead `--strategy` flag
7. Task 10 — Rewrite `assets.py` SLURM branch for the new schema
8. Task 11 — Delete dead per-recipe SLURM code
9. Tasks 12-13 — Docs (HPC pilot guide, CLAUDE.md update)
10. Tasks 14-15 — Manual smoke test + final verification

Demolition (Task 11) lands **after** the new path is fully working and tested, so the diff is reviewable as "added new path, then removed old path" — never with both broken at once.

---

## Task 1: Add Parsl dependency

**Files:**
- Modify: `pyproject.toml`
- Test: none (dep change only)

- [ ] **Step 1: Add parsl to runtime dependencies**

In `pyproject.toml`, locate the `dependencies = [...]` array and add `parsl`:

```toml
dependencies = [
    "astra-tools>=0.2.2",
    "click>=8.0",
    "pyyaml>=6.0",
    "rich>=13.0",
    "dagster>=1.9",
    "dagster-webserver>=1.9",
    "dagster-docker>=0.25",
    "langfuse>=2.0",
    "parsl>=2024.1.0",
]
```

`ndcctools` (the WorkQueue C library + Python bindings) is **not** added as a Python-level dep — it's conda-only on most platforms and gets installed at site setup time. The runtime check happens in Task 4.

- [ ] **Step 2: Sync the env**

Run: `uv sync --group dev`
Expected: lockfile updated; `parsl` installed.

- [ ] **Step 3: Smoke import**

Run: `uv run python -c "import parsl; print(parsl.__version__)"`
Expected: prints a version like `2024.x.y`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock 2>/dev/null || git add pyproject.toml
git commit -m "deps: add parsl for pilot-based SLURM execution"
```

(`uv.lock` is `.gitignore`d in this repo, so the second add may no-op — that's fine.)

---

## Task 2: Pure functions — recipe resources → WorkQueue spec

**Files:**
- Create: `src/lightcone/engine/parsl_backend.py`
- Test: `tests/test_parsl_backend.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_parsl_backend.py`:

```python
"""Tests for the Parsl backend (pure functions, no Parsl runtime)."""
from __future__ import annotations

import pytest

from lightcone.engine.parsl_backend import (
    recipe_resources_to_parsl,
)


class TestRecipeResourcesToParsl:
    def test_empty_resources(self):
        assert recipe_resources_to_parsl({}) == {}

    def test_cpus(self):
        assert recipe_resources_to_parsl({"cpus": 4}) == {"cores": 4}

    def test_memory_gb(self):
        # WorkQueue expects memory in MB
        assert recipe_resources_to_parsl({"memory": "16GB"}) == {"memory": 16384}

    def test_memory_mb(self):
        assert recipe_resources_to_parsl({"memory": "512MB"}) == {"memory": 512}

    def test_memory_lower_case(self):
        assert recipe_resources_to_parsl({"memory": "8gb"}) == {"memory": 8192}

    def test_gpus(self):
        assert recipe_resources_to_parsl({"gpus": 2}) == {"gpus": 2}

    def test_time_limit_hours(self):
        # WorkQueue's wall_time is seconds
        assert recipe_resources_to_parsl({"time_limit": "2h"}) == {
            "wall_time": 7200,
        }

    def test_time_limit_minutes(self):
        assert recipe_resources_to_parsl({"time_limit": "30m"}) == {
            "wall_time": 1800,
        }

    def test_time_limit_int_minutes(self):
        assert recipe_resources_to_parsl({"time_limit": 90}) == {
            "wall_time": 5400,
        }

    def test_time_limit_hhmmss(self):
        assert recipe_resources_to_parsl({"time_limit": "01:30:00"}) == {
            "wall_time": 5400,
        }

    def test_full(self):
        spec = recipe_resources_to_parsl(
            {"cpus": 8, "memory": "32GB", "gpus": 1, "time_limit": "1h"}
        )
        assert spec == {
            "cores": 8,
            "memory": 32768,
            "gpus": 1,
            "wall_time": 3600,
        }

    def test_unknown_keys_ignored(self):
        # nodes is a pilot-level concept, not per-task
        assert recipe_resources_to_parsl({"nodes": 4, "cpus": 2}) == {"cores": 2}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_parsl_backend.py -v`
Expected: `ModuleNotFoundError: lightcone.engine.parsl_backend`.

- [ ] **Step 3: Create parsl_backend.py with the resource mapper**

Create `src/lightcone/engine/parsl_backend.py`:

```python
"""Parsl backend — translates lightcone targets into a Parsl Config.

Owns three concerns:
  - Recipe resources (per-task) → WorkQueue per-task spec
  - Target ``pilots:`` dict → ``parsl.Config`` (one executor per pilot)
  - Recipe routing → which executor label handles a given recipe
  - Pre-flight QoS validation at pilot scope (replaces per-recipe clamping)
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Per-task resource mapping
# --------------------------------------------------------------------------

_MEMORY_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(MB|GB|TB)?$", re.IGNORECASE)
_TIME_HMS_RE = re.compile(r"^(\d+):(\d{1,2}):(\d{1,2})$")
_TIME_UNIT_RE = re.compile(r"^(\d+)\s*([hm])?$", re.IGNORECASE)


def _parse_memory_to_mb(value: str) -> int:
    match = _MEMORY_RE.match(value.strip())
    if not match:
        raise ValueError(f"Cannot parse memory value: {value!r}")
    amount = float(match.group(1))
    unit = (match.group(2) or "MB").upper()
    if unit == "MB":
        return int(amount)
    if unit == "GB":
        return int(amount * 1024)
    if unit == "TB":
        return int(amount * 1024 * 1024)
    raise ValueError(f"Unknown memory unit: {unit}")


def _parse_time_to_seconds(value: str | int) -> int:
    """Convert time_limit (str like '2h'/'30m'/'01:30:00' or bare-int minutes) to seconds."""
    if isinstance(value, int):
        return value * 60
    s = str(value).strip()
    if (m := _TIME_HMS_RE.match(s)):
        h, mi, se = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return h * 3600 + mi * 60 + se
    if (m := _TIME_UNIT_RE.match(s)):
        n, unit = int(m.group(1)), (m.group(2) or "m").lower()
        return n * 3600 if unit == "h" else n * 60
    raise ValueError(f"Cannot parse time_limit: {value!r}")


def recipe_resources_to_parsl(resources: dict[str, Any]) -> dict[str, Any]:
    """Translate ASTRA recipe.resources to a WorkQueue per-task spec.

    Output keys: ``cores``, ``memory`` (MB), ``gpus``, ``wall_time`` (seconds).
    Pilot-level keys (``nodes``) are intentionally dropped — those describe
    the allocation, not a single task.
    """
    spec: dict[str, Any] = {}
    if cpus := resources.get("cpus"):
        spec["cores"] = cpus
    if mem := resources.get("memory"):
        spec["memory"] = _parse_memory_to_mb(mem)
    if gpus := resources.get("gpus"):
        spec["gpus"] = gpus
    if (tl := resources.get("time_limit")) is not None:
        spec["wall_time"] = _parse_time_to_seconds(tl)
    return spec
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_parsl_backend.py -v`
Expected: all 11 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lightcone/engine/parsl_backend.py tests/test_parsl_backend.py
git commit -m "feat(engine): add parsl_backend.recipe_resources_to_parsl"
```

---

## Task 3: Pure function — executor routing

**Files:**
- Modify: `src/lightcone/engine/parsl_backend.py`
- Test: `tests/test_parsl_backend.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_parsl_backend.py`:

```python
from lightcone.engine.parsl_backend import (
    PilotRoutingError,
    pick_executor,
)


class TestPickExecutor:
    def test_cpu_only_routes_to_cpu(self):
        pilots = {"cpu": {"nodes": 4, "walltime": "2h"}}
        assert pick_executor({"cpus": 4}, pilots) == "cpu"

    def test_gpu_resource_routes_to_gpu_when_available(self):
        pilots = {
            "cpu": {"nodes": 4, "walltime": "2h"},
            "gpu": {"nodes": 1, "walltime": "1h"},
        }
        assert pick_executor({"gpus": 1}, pilots) == "gpu"

    def test_gpu_resource_falls_back_to_cpu_when_no_gpu_pilot(self):
        # Without a GPU pilot, GPU recipes raise — better to fail fast than
        # silently dispatch to a CPU pilot that won't have GPU resources.
        pilots = {"cpu": {"nodes": 4, "walltime": "2h"}}
        with pytest.raises(PilotRoutingError, match="gpu"):
            pick_executor({"gpus": 1}, pilots)

    def test_multi_node_routes_to_mpi_when_available(self):
        pilots = {
            "cpu": {"nodes": 4, "walltime": "2h"},
            "mpi": {"nodes": 8, "walltime": "4h"},
        }
        assert pick_executor({"nodes": 4}, pilots) == "mpi"

    def test_multi_node_falls_back_to_cpu_when_no_mpi_pilot(self):
        pilots = {"cpu": {"nodes": 4, "walltime": "2h"}}
        # No MPI pilot — falls through to cpu; user is responsible for
        # whether that's actually viable.
        assert pick_executor({"nodes": 2}, pilots) == "cpu"

    def test_no_resources_routes_to_cpu(self):
        pilots = {"cpu": {"nodes": 4, "walltime": "2h"}}
        assert pick_executor({}, pilots) == "cpu"

    def test_no_pilots_raises(self):
        with pytest.raises(PilotRoutingError, match="no pilots"):
            pick_executor({"cpus": 4}, {})

    def test_no_cpu_pilot_for_cpu_recipe_raises(self):
        # Edge case: only a gpu pilot exists, recipe asks for nothing
        pilots = {"gpu": {"nodes": 1, "walltime": "1h"}}
        with pytest.raises(PilotRoutingError):
            pick_executor({"cpus": 2}, pilots)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_parsl_backend.py::TestPickExecutor -v`
Expected: `ImportError: cannot import name 'pick_executor'`.

- [ ] **Step 3: Add `pick_executor` and the error type**

Append to `src/lightcone/engine/parsl_backend.py`:

```python
# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------


class PilotRoutingError(RuntimeError):
    """Raised when a recipe cannot be routed to any configured pilot."""


def pick_executor(
    resources: dict[str, Any],
    pilots: dict[str, Any],
) -> str:
    """Pick the executor label for a recipe given the configured pilots.

    Routing rule (deterministic, in this order):
      1. ``resources.nodes > 1`` and ``mpi`` pilot exists → ``mpi``
      2. ``resources.gpus > 0`` and ``gpu`` pilot exists → ``gpu``
      3. ``cpu`` pilot exists → ``cpu``

    Raises :class:`PilotRoutingError` if no rule matches — better to fail
    fast at task dispatch than to silently route to the wrong pool.
    """
    if not pilots:
        raise PilotRoutingError(
            "no pilots configured in target; cannot route any recipe"
        )

    if resources.get("nodes", 1) > 1 and "mpi" in pilots:
        return "mpi"

    if resources.get("gpus") and "gpu" in pilots:
        return "gpu"

    if resources.get("gpus"):
        # GPU recipe but no GPU pilot — fail fast rather than dispatch to cpu.
        raise PilotRoutingError(
            f"recipe needs gpus={resources['gpus']} but no 'gpu' pilot "
            f"is configured in target (available: {sorted(pilots)})"
        )

    if "cpu" in pilots:
        return "cpu"

    raise PilotRoutingError(
        f"no suitable pilot for resources={resources}; "
        f"available: {sorted(pilots)} (need 'cpu')"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_parsl_backend.py -v`
Expected: 19 tests PASS (11 from Task 2 + 8 new).

- [ ] **Step 5: Commit**

```bash
git add src/lightcone/engine/parsl_backend.py tests/test_parsl_backend.py
git commit -m "feat(engine): add parsl_backend.pick_executor routing"
```

---

## Task 4: Build `parsl.Config` from target

**Files:**
- Modify: `src/lightcone/engine/parsl_backend.py`
- Test: `tests/test_parsl_backend.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_parsl_backend.py`:

```python
from lightcone.engine.parsl_backend import (
    MissingWorkQueueError,
    build_parsl_config,
)


def _have_workqueue() -> bool:
    try:
        import work_queue  # noqa: F401
        from parsl.executors import WorkQueueExecutor  # noqa: F401
        return True
    except ImportError:
        return False


class TestBuildParslConfig:
    """build_parsl_config returns a parsl.Config with one executor per pilot.

    These tests inspect the returned object's attributes rather than
    actually loading the DFK — that's covered by the integration tests.
    """

    def test_missing_pilots_key_raises(self):
        with pytest.raises(ValueError, match="pilots"):
            build_parsl_config({"backend": "slurm"})

    def test_empty_pilots_raises(self):
        with pytest.raises(ValueError, match="pilots"):
            build_parsl_config({"backend": "slurm", "pilots": {}})

    @pytest.mark.skipif(
        not _have_workqueue(),
        reason="ndcctools (WorkQueue) not installed — required for SLURM backend",
    )
    def test_single_cpu_pilot(self):
        target = {
            "backend": "slurm",
            "pilots": {
                "cpu": {
                    "nodes": 4,
                    "walltime": "2h",
                    "qos": "debug",
                    "account": "m1234",
                },
            },
        }
        config = build_parsl_config(target)
        assert len(config.executors) == 1
        ex = config.executors[0]
        assert ex.label == "cpu"
        # Provider sanity
        provider = ex.provider
        assert provider.nodes_per_block == 4
        assert provider.walltime == "02:00:00"
        assert provider.qos == "debug"
        assert provider.account == "m1234"
        assert provider.init_blocks == 1
        assert provider.min_blocks == 1
        assert provider.max_blocks == 1

    @pytest.mark.skipif(
        not _have_workqueue(),
        reason="ndcctools (WorkQueue) not installed",
    )
    def test_cpu_and_gpu_pilots(self):
        target = {
            "backend": "slurm",
            "pilots": {
                "cpu": {"nodes": 4, "walltime": "2h", "account": "m1234"},
                "gpu": {
                    "nodes": 2, "walltime": "1h", "account": "m1234_g",
                    "constraint": "gpu",
                },
            },
        }
        config = build_parsl_config(target)
        labels = {ex.label for ex in config.executors}
        assert labels == {"cpu", "gpu"}
        gpu_ex = next(ex for ex in config.executors if ex.label == "gpu")
        assert gpu_ex.provider.constraint == "gpu"
        assert gpu_ex.provider.account == "m1234_g"

    @pytest.mark.skipif(
        not _have_workqueue(),
        reason="ndcctools (WorkQueue) not installed",
    )
    def test_worker_init_passed_through(self):
        target = {
            "backend": "slurm",
            "pilots": {
                "cpu": {
                    "nodes": 1, "walltime": "30m", "account": "m1234",
                    "worker_init": "module load python\nsource /env/bin/activate",
                },
            },
        }
        config = build_parsl_config(target)
        assert "module load python" in config.executors[0].provider.worker_init

    @pytest.mark.skipif(
        not _have_workqueue(),
        reason="ndcctools (WorkQueue) not installed",
    )
    def test_run_dir_under_results(self, tmp_path):
        target = {
            "backend": "slurm",
            "pilots": {"cpu": {"nodes": 1, "walltime": "30m", "account": "m1"}},
        }
        config = build_parsl_config(target, project_root=tmp_path)
        assert str(tmp_path / "results" / ".parsl") in config.run_dir

    def test_workqueue_missing_raises_clear_error(self, monkeypatch):
        """When ndcctools isn't installed, raise a clear actionable error."""
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "parsl.executors.workqueue.executor":
                raise ImportError("No module named 'work_queue'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        target = {
            "backend": "slurm",
            "pilots": {"cpu": {"nodes": 1, "walltime": "30m", "account": "m1"}},
        }
        with pytest.raises(MissingWorkQueueError, match="ndcctools"):
            build_parsl_config(target)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_parsl_backend.py::TestBuildParslConfig -v`
Expected: `ImportError: cannot import name 'build_parsl_config'`.

- [ ] **Step 3: Implement `build_parsl_config`**

Append to `src/lightcone/engine/parsl_backend.py`:

```python
# --------------------------------------------------------------------------
# Config construction
# --------------------------------------------------------------------------


class MissingWorkQueueError(RuntimeError):
    """ndcctools / work_queue not installed; required for SLURM backend."""


def _walltime_to_hms(value: str | int) -> str:
    """Convert walltime ('2h', '30m', 120, '01:30:00') to HH:MM:SS for SlurmProvider."""
    seconds = _parse_time_to_seconds(value)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _build_pilot_executor(label: str, pilot: dict[str, Any]):
    """Build one WorkQueueExecutor wrapping a SlurmProvider for a single pilot."""
    try:
        from parsl.executors import WorkQueueExecutor
        from parsl.providers import SlurmProvider
    except ImportError as e:
        raise MissingWorkQueueError(
            "WorkQueueExecutor requires the 'ndcctools' package "
            "(provides the 'work_queue' Python module). "
            "Install via: conda install -c conda-forge ndcctools"
        ) from e

    provider_kwargs: dict[str, Any] = {
        "nodes_per_block": pilot["nodes"],
        "walltime": _walltime_to_hms(pilot["walltime"]),
        "init_blocks": 1,
        "min_blocks": 1,
        "max_blocks": 1,
        "exclusive": pilot.get("exclusive", True),
    }
    for key in ("account", "qos", "partition", "constraint", "worker_init"):
        if (val := pilot.get(key)) is not None:
            provider_kwargs[key] = val
    if extra := pilot.get("scheduler_options"):
        provider_kwargs["scheduler_options"] = extra

    provider = SlurmProvider(**provider_kwargs)

    return WorkQueueExecutor(
        label=label,
        provider=provider,
        # Lets recipes declare per-task resources (cores, memory, gpus,
        # wall_time) so WorkQueue can bin-pack heterogeneous tasks within
        # the allocation.
        autolabel=False,
        autocategory=False,
    )


def build_parsl_config(
    target_config: dict[str, Any],
    project_root=None,
):
    """Build a ``parsl.Config`` from a lightcone target dict.

    *project_root* (optional ``Path``) — if given, the DFK's run_dir is
    rooted under ``<project_root>/results/.parsl`` to mirror today's
    ``results/.slurm`` convention.

    Raises ``ValueError`` for missing/empty ``pilots``;
    raises ``MissingWorkQueueError`` if ndcctools is not installed.
    """
    from parsl.config import Config

    pilots = target_config.get("pilots") or {}
    if not pilots:
        raise ValueError(
            "target.pilots must be a non-empty mapping; "
            "this target has no pilots configured"
        )

    executors = [_build_pilot_executor(label, p) for label, p in pilots.items()]

    run_dir = "runinfo"
    if project_root is not None:
        from pathlib import Path
        run_dir = str(Path(project_root) / "results" / ".parsl")

    return Config(
        executors=executors,
        run_dir=run_dir,
        # Pilot is fixed-size; turn off autoscale so Parsl never tries to
        # provision additional blocks. min == max == init == 1 already
        # pins it, but strategy='none' makes the intent explicit.
        strategy="none",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_parsl_backend.py -v`
Expected: tests pass when WorkQueue is available; cleanly skip with the documented reason when it isn't. The two never-skipped tests (`test_missing_pilots_key_raises`, `test_empty_pilots_raises`, `test_workqueue_missing_raises_clear_error`) always pass.

- [ ] **Step 5: Commit**

```bash
git add src/lightcone/engine/parsl_backend.py tests/test_parsl_backend.py
git commit -m "feat(engine): build_parsl_config — target dict to parsl.Config"
```

---

## Task 5: Pre-flight pilot validation

**Files:**
- Modify: `src/lightcone/engine/parsl_backend.py`
- Test: `tests/test_parsl_backend.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_parsl_backend.py`:

```python
from lightcone.engine.slurm_info import ClusterInfo, QoSInfo


class TestValidatePilotsAgainstQos:
    def _cluster_with_debug_max_8_nodes_30min(self):
        return ClusterInfo(
            qos={
                "gpu_debug": QoSInfo(
                    "gpu_debug", max_wall_minutes=30, max_nodes=8, priority=1
                ),
                "gpu_regular": QoSInfo(
                    "gpu_regular", max_wall_minutes=2880, priority=1
                ),
            },
            user_qos=["gpu_debug", "gpu_regular"],
            user_accounts=["m4031"],
            partitions={},
            timestamp="2026-04-24T00:00:00",
        )

    def test_pilot_within_limits_passes(self, monkeypatch):
        from lightcone.engine.parsl_backend import validate_pilots_against_qos

        cluster = self._cluster_with_debug_max_8_nodes_30min()
        monkeypatch.setattr(
            "lightcone.engine.targets.load_cluster_cache", lambda n: cluster,
        )
        monkeypatch.setattr(
            "lightcone.engine.targets.is_cache_stale", lambda n: False,
        )

        validate_pilots_against_qos(
            pilots={"gpu": {"nodes": 4, "walltime": "20m", "qos": "gpu_debug"}},
            target_name="perlmutter",
        )

    def test_pilot_exceeds_max_nodes_raises(self, monkeypatch):
        from lightcone.engine.parsl_backend import (
            PilotConfigError,
            validate_pilots_against_qos,
        )
        cluster = self._cluster_with_debug_max_8_nodes_30min()
        monkeypatch.setattr(
            "lightcone.engine.targets.load_cluster_cache", lambda n: cluster,
        )
        monkeypatch.setattr(
            "lightcone.engine.targets.is_cache_stale", lambda n: False,
        )

        with pytest.raises(PilotConfigError, match="nodes"):
            validate_pilots_against_qos(
                pilots={
                    "gpu": {"nodes": 16, "walltime": "20m", "qos": "gpu_debug"},
                },
                target_name="perlmutter",
            )

    def test_pilot_exceeds_walltime_raises(self, monkeypatch):
        from lightcone.engine.parsl_backend import (
            PilotConfigError,
            validate_pilots_against_qos,
        )
        cluster = self._cluster_with_debug_max_8_nodes_30min()
        monkeypatch.setattr(
            "lightcone.engine.targets.load_cluster_cache", lambda n: cluster,
        )
        monkeypatch.setattr(
            "lightcone.engine.targets.is_cache_stale", lambda n: False,
        )

        with pytest.raises(PilotConfigError, match="wall"):
            validate_pilots_against_qos(
                pilots={
                    "gpu": {"nodes": 4, "walltime": "2h", "qos": "gpu_debug"},
                },
                target_name="perlmutter",
            )

    def test_no_cluster_cache_warns_but_passes(self, monkeypatch, caplog):
        from lightcone.engine.parsl_backend import validate_pilots_against_qos

        monkeypatch.setattr(
            "lightcone.engine.targets.load_cluster_cache", lambda n: None,
        )
        monkeypatch.setattr(
            "lightcone.engine.targets.is_cache_stale", lambda n: False,
        )
        # No cache — best-effort, don't block lc run; log a warning.
        validate_pilots_against_qos(
            pilots={"cpu": {"nodes": 4, "walltime": "2h", "qos": "debug"}},
            target_name="perlmutter",
        )
        assert any("cluster cache" in r.message.lower() for r in caplog.records)

    def test_pilot_without_qos_skipped(self, monkeypatch):
        # No QoS declared in pilot → nothing to check
        from lightcone.engine.parsl_backend import validate_pilots_against_qos

        cluster = self._cluster_with_debug_max_8_nodes_30min()
        monkeypatch.setattr(
            "lightcone.engine.targets.load_cluster_cache", lambda n: cluster,
        )
        monkeypatch.setattr(
            "lightcone.engine.targets.is_cache_stale", lambda n: False,
        )
        validate_pilots_against_qos(
            pilots={"cpu": {"nodes": 100, "walltime": "100h"}},
            target_name="perlmutter",
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_parsl_backend.py::TestValidatePilotsAgainstQos -v`
Expected: `ImportError: cannot import name 'validate_pilots_against_qos'`.

- [ ] **Step 3: Implement the validator**

Append to `src/lightcone/engine/parsl_backend.py`:

```python
# --------------------------------------------------------------------------
# Pre-flight validation (replaces per-recipe QoS clamping)
# --------------------------------------------------------------------------


class PilotConfigError(RuntimeError):
    """A pilot's requested size doesn't fit the QoS it targets."""


def validate_pilots_against_qos(
    pilots: dict[str, Any],
    target_name: str | None,
) -> None:
    """Pre-flight check at ``lc run`` start.

    For each pilot that names a QoS, look the QoS up in the cluster cache
    and verify ``nodes`` and ``walltime`` fit. Raises ``PilotConfigError``
    on the first violation with a message naming the pilot, the QoS, and
    the offending limit.

    If no cluster cache is available (e.g., user hasn't run
    ``lc target refresh``), logs a warning and returns — best-effort,
    don't block ``lc run``.
    """
    if not target_name:
        return

    from lightcone.engine.slurm_info import check_qos_eligibility
    from lightcone.engine.targets import (
        is_cache_stale,
        load_cluster_cache,
    )

    if is_cache_stale(target_name):
        logger.warning(
            "Cluster cache for '%s' is stale or missing. "
            "Run `lc target refresh %s` to update.",
            target_name, target_name,
        )

    cluster = load_cluster_cache(target_name)
    if cluster is None:
        logger.warning(
            "No cluster cache for target '%s' — skipping pilot QoS pre-flight",
            target_name,
        )
        return

    for label, pilot in pilots.items():
        qos = pilot.get("qos")
        if not qos:
            continue
        # Direct lookup; constraint-qualified cache keys are a corner case
        # the user docs cover. Any miss is treated as "unknown qos" — log
        # and skip rather than block.
        qos_info = cluster.qos.get(qos)
        if qos_info is None:
            logger.warning(
                "Pilot '%s' targets qos '%s' which is not in the cluster "
                "cache — skipping pre-flight for this pilot",
                label, qos,
            )
            continue

        wall_seconds = _parse_time_to_seconds(pilot["walltime"])
        rec = check_qos_eligibility(
            qos_info,
            {
                "nodes": pilot["nodes"],
                "gpus_per_node": 0,  # pilots specify nodes, not per-task gpus
                "time_limit_minutes": wall_seconds // 60,
            },
        )
        if not rec.eligible:
            raise PilotConfigError(
                f"pilot '{label}' (nodes={pilot['nodes']}, "
                f"walltime={pilot['walltime']}) does not fit qos '{qos}': "
                + "; ".join(rec.violations)
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_parsl_backend.py -v`
Expected: all tests in this file pass (modulo the WorkQueue-skips from Task 4).

- [ ] **Step 5: Commit**

```bash
git add src/lightcone/engine/parsl_backend.py tests/test_parsl_backend.py
git commit -m "feat(engine): pre-flight QoS validation at pilot scope"
```

---

## Task 6: Extract `build_recipe_shell_command` from old runner

**Goal of this task:** Lift the *content* of the existing `_podman_hpc_run_command` + the external-input-symlinks block out of the (about-to-be-deleted) `generate_sbatch_script`, into a standalone helper. This is a pure code move — it produces the same shell command string, just no longer wrapped in an sbatch script. The new `_run_slurm` will call it in Task 7.

**Files:**
- Modify: `src/lightcone/engine/runner.py`
- Test: `tests/test_runner.py` (new test class)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_runner.py`:

```python
class TestBuildRecipeShellCommand:
    """The shell command lifted out of the sbatch context.

    Same string content the old _podman_hpc_run_command + symlink loop
    produced, but standalone — used by both the Parsl-backed _run_slurm
    and (eventually) any direct-shell debugging.
    """

    def test_no_container_no_external_inputs(self, tmp_path):
        from lightcone.engine.runner import build_recipe_shell_command

        cmd = build_recipe_shell_command(
            command="python scripts/train.py",
            container=None,
            container_runtime=None,
            project_root=tmp_path,
            resources={},
            cwd=str(tmp_path),
            external_inputs=None,
        )
        assert cmd.endswith("python scripts/train.py")
        assert f"cd {tmp_path}" in cmd

    def test_no_container_with_external_inputs(self, tmp_path):
        from lightcone.engine.runner import build_recipe_shell_command

        cmd = build_recipe_shell_command(
            command="python scripts/analyze.py",
            container=None,
            container_runtime=None,
            project_root=tmp_path,
            resources={},
            cwd=str(tmp_path),
            external_inputs={"sim": "/pscratch/sim"},
        )
        assert "mkdir -p data" in cmd
        assert "ln -sfn /pscratch/sim data/sim" in cmd
        assert "python scripts/analyze.py" in cmd

    def test_podman_hpc_basic(self, tmp_path):
        from lightcone.engine.runner import build_recipe_shell_command

        cmd = build_recipe_shell_command(
            command="python scripts/train.py",
            container="ghcr.io/proj/ml:latest",
            container_runtime="podman-hpc",
            project_root=tmp_path,
            resources={"cpus": 4},
            cwd=str(tmp_path),
            external_inputs=None,
        )
        assert "podman-hpc run --rm" in cmd
        assert f"-v {tmp_path}:/workspace" in cmd
        assert "-w /workspace" in cmd
        assert "ghcr.io/proj/ml:latest" in cmd
        assert "python scripts/train.py" in cmd

    def test_podman_hpc_with_gpu(self, tmp_path):
        from lightcone.engine.runner import build_recipe_shell_command

        cmd = build_recipe_shell_command(
            command="python train.py",
            container="img:1.0",
            container_runtime="podman-hpc",
            project_root=tmp_path,
            resources={"gpus": 1},
            cwd=str(tmp_path),
            external_inputs=None,
        )
        assert "--gpu" in cmd

    def test_podman_hpc_multinode_adds_mpi(self, tmp_path):
        from lightcone.engine.runner import build_recipe_shell_command

        cmd = build_recipe_shell_command(
            command="python train.py",
            container="img:1.0",
            container_runtime="podman-hpc",
            project_root=tmp_path,
            resources={"nodes": 2},
            cwd=str(tmp_path),
            external_inputs=None,
        )
        assert "--mpi" in cmd

    def test_podman_hpc_external_inputs_become_volume_mounts(self, tmp_path):
        from lightcone.engine.runner import build_recipe_shell_command

        cmd = build_recipe_shell_command(
            command="python analyze.py",
            container="img:1.0",
            container_runtime="podman-hpc",
            project_root=tmp_path,
            resources={},
            cwd=str(tmp_path),
            external_inputs={"sim": "/pscratch/sim"},
        )
        assert "-v /pscratch/sim:/workspace/data/sim:ro" in cmd

    def test_cwd_changes_to_subanalysis_dir(self, tmp_path):
        from lightcone.engine.runner import build_recipe_shell_command

        sub = tmp_path / "sub"
        sub.mkdir()
        cmd = build_recipe_shell_command(
            command="python sub_script.py",
            container=None,
            container_runtime=None,
            project_root=tmp_path,
            resources={},
            cwd=str(sub),
            external_inputs=None,
        )
        assert f"cd {sub}" in cmd
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_runner.py::TestBuildRecipeShellCommand -v`
Expected: `ImportError: cannot import name 'build_recipe_shell_command'`.

- [ ] **Step 3: Implement `build_recipe_shell_command`**

Add to `src/lightcone/engine/runner.py`, immediately after the existing `translate_resources_to_docker_flags` function (around line 148):

```python
def build_recipe_shell_command(
    command: str,
    container: str | None,
    container_runtime: str | None,
    project_root: Path,
    resources: dict[str, Any],
    cwd: str,
    external_inputs: dict[str, str] | None,
) -> str:
    """Compose the shell command that executes a recipe inside a worker.

    This is the same string the old SLURM backend wrote into an sbatch
    script body (minus the ``#SBATCH`` headers): cd to cwd, optionally
    set up symlinks or volume mounts for external inputs, then run the
    command — wrapping it in ``podman-hpc run`` when a container is
    requested.

    Used by both the Parsl-backed SLURM runner and any direct-shell
    debugging. The output is a single multi-line shell script suitable
    for ``bash -c`` or ``parsl.bash_app``.
    """
    lines = [f"cd {shlex.quote(cwd)}"]

    if container and container_runtime == "podman-hpc":
        lines.append(_podman_hpc_run_command_inline(
            command=command,
            container=container,
            project_root=project_root,
            resources=resources,
            external_inputs=external_inputs,
        ))
        return "\n".join(lines)

    # No container — symlink external inputs into ./data/ for the recipe to read
    if external_inputs:
        lines.append("mkdir -p data")
        for input_id, source in sorted(external_inputs.items()):
            src = shlex.quote(str(source))
            dst = shlex.quote(f"data/{input_id}")
            lines.append(f"ln -sfn {src} {dst}")

    lines.append(command)
    return "\n".join(lines)


def _podman_hpc_run_command_inline(
    command: str,
    container: str,
    project_root: Path,
    resources: dict[str, Any],
    external_inputs: dict[str, str] | None = None,
) -> str:
    """Build a podman-hpc run invocation as a single shell command string.

    Mirrors the old ``_podman_hpc_run_command`` but without an
    ``scheduler_config`` parameter — the ``extra_container_flags`` escape
    hatch is now read from the pilot config when constructing the
    SlurmProvider, and per-task container flags aren't a concept we need.
    """
    parts = ["podman-hpc", "run", "--rm"]

    if resources.get("gpus"):
        parts.append("--gpu")
    if resources.get("nodes", 1) > 1:
        parts.append("--mpi")

    parts.extend(["-v", shlex.quote(f"{project_root}:/workspace"), "-w", "/workspace"])

    for input_id, source in sorted((external_inputs or {}).items()):
        parts.extend(["-v", shlex.quote(f"{source}:/workspace/data/{input_id}:ro")])

    parts.append(container)
    parts.extend(["sh", "-c", shlex.quote(command)])

    return " ".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_runner.py::TestBuildRecipeShellCommand -v`
Expected: all 7 new tests PASS.

- [ ] **Step 5: Verify the existing SLURM tests still pass**

Run: `uv run pytest tests/test_runner.py -v`
Expected: all existing tests still PASS (we haven't deleted anything yet — both the old and new helpers coexist for now).

- [ ] **Step 6: Commit**

```bash
git add src/lightcone/engine/runner.py tests/test_runner.py
git commit -m "refactor(engine): extract build_recipe_shell_command helper"
```

---

## Task 7: Rewrite `_run_slurm` on top of Parsl

**Files:**
- Modify: `src/lightcone/engine/runner.py`
- Modify: `tests/conftest.py` (add `parsl_local_pilot` fixture)
- Create: `tests/test_runner_parsl.py`

- [ ] **Step 1: Add the `parsl_local_pilot` fixture to conftest**

Append to `tests/conftest.py`:

```python
@pytest.fixture
def parsl_local_pilot():
    """Yield with a Parsl DFK loaded with a single LocalProvider-backed
    WorkQueueExecutor labelled 'cpu'. Cleaned up on test exit.

    Use for integration tests that need to actually run bash_app tasks
    without booting a real SLURM allocation.

    Skips the test cleanly if ndcctools (WorkQueue) isn't installed.
    """
    try:
        import parsl
        from parsl.config import Config
        from parsl.executors import WorkQueueExecutor
        from parsl.providers import LocalProvider
    except ImportError as e:
        pytest.skip(f"Parsl not available: {e}")

    try:
        import work_queue  # noqa: F401
    except ImportError:
        pytest.skip("ndcctools (WorkQueue) not installed")

    config = Config(
        executors=[
            WorkQueueExecutor(
                label="cpu",
                provider=LocalProvider(init_blocks=1, min_blocks=1, max_blocks=1),
                autolabel=False,
                autocategory=False,
            ),
        ],
        strategy="none",
    )
    parsl.load(config)
    try:
        yield
    finally:
        parsl.dfk().cleanup()
        parsl.clear()
```

- [ ] **Step 2: Write the failing integration tests**

Create `tests/test_runner_parsl.py`:

```python
"""Integration tests for the Parsl-backed SLURM runner.

These tests use ``WorkQueueExecutor + LocalProvider`` so they exercise
the real Parsl plumbing (bash_app, futures, BashExitFailure handling)
without needing a SLURM cluster.
"""
from __future__ import annotations

import pytest

from lightcone.engine.runner import ASTRAContainerRunner


@pytest.mark.usefixtures("parsl_local_pilot")
class TestRunSlurmViaLocalPilot:
    def test_trivial_command_succeeds(self, tmp_path):
        runner = ASTRAContainerRunner(
            project_root=str(tmp_path),
            backend="slurm",
            target_config={"pilots": {"cpu": {"nodes": 1, "walltime": "5m"}}},
        )
        result = runner.execute(
            command="echo hello",
            output_id="greeting",
            universe_id="baseline",
        )
        assert result.exit_code == 0
        assert result.metadata["backend"] == "slurm"
        assert result.metadata["executor"] == "cpu"
        assert "hello" in result.metadata.get("stdout", "")

    def test_failing_command_returns_nonzero(self, tmp_path):
        runner = ASTRAContainerRunner(
            project_root=str(tmp_path),
            backend="slurm",
            target_config={"pilots": {"cpu": {"nodes": 1, "walltime": "5m"}}},
        )
        result = runner.execute(
            command="exit 7",
            output_id="failing",
            universe_id="baseline",
        )
        assert result.exit_code == 7

    def test_universe_param_is_forwarded(self, tmp_path):
        runner = ASTRAContainerRunner(
            project_root=str(tmp_path),
            backend="slurm",
            target_config={"pilots": {"cpu": {"nodes": 1, "walltime": "5m"}}},
        )
        result = runner.execute(
            command="echo got=$1; shift; echo got=$1",
            output_id="check",
            universe_id="exp1",
            params={"method": "npe"},
        )
        assert result.exit_code == 0
        # _build_cli_args appends '--universe exp1 --method npe' to the command
        assert "exp1" in result.metadata.get("stdout", "")

    def test_pilot_routing_for_unconfigured_executor_raises(self, tmp_path):
        """Recipe asks for gpus but only the cpu pilot is loaded → PilotRoutingError."""
        from lightcone.engine.parsl_backend import PilotRoutingError

        runner = ASTRAContainerRunner(
            project_root=str(tmp_path),
            backend="slurm",
            # Same single-cpu pilot fixture loaded; no gpu pilot configured.
            target_config={"pilots": {"cpu": {"nodes": 1, "walltime": "5m"}}},
        )
        with pytest.raises(PilotRoutingError, match="gpu"):
            runner.execute(
                command="echo unreachable",
                output_id="gpu_recipe",
                universe_id="baseline",
                resources={"gpus": 1},
            )
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_runner_parsl.py -v`
Expected: failures because the new `_run_slurm` body doesn't exist yet — old one still constructs an sbatch script and looks for `pilots`-shaped target config that won't match.

- [ ] **Step 4: Replace `_run_slurm` body**

In `src/lightcone/engine/runner.py`, locate the `_run_slurm` method (currently around line 626) and **replace its entire body** with the new implementation:

```python
    def _run_slurm(
        self,
        command: str,
        container: str | None,
        input_ids: list[str],
        output_id: str,
        universe_id: str,
        resources: dict[str, Any],
        external_inputs: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> ExecutionResult:
        """Execute a recipe via Parsl into a pre-loaded pilot allocation.

        Assumes ``parsl.load(config)`` has been called by the CLI layer
        (see ``lc run``). Routes the recipe to one of the configured
        pilot executors based on its resources, builds a ``bash_app``,
        and awaits the result. Stdout/stderr are read from Parsl's
        per-task log files.
        """
        from parsl.app.app import bash_app
        from parsl.app.errors import BashExitFailure

        from lightcone.engine.parsl_backend import (
            pick_executor,
            recipe_resources_to_parsl,
        )

        pilots = self.target_config.get("pilots") or {}
        label = pick_executor(resources, pilots)
        spec = recipe_resources_to_parsl(resources)
        container_runtime = self.target_config.get("container_runtime", "podman-hpc")

        full_cmd = build_recipe_shell_command(
            command=command,
            container=container,
            container_runtime=container_runtime,
            project_root=self.project_root,
            resources=resources,
            cwd=cwd or str(self.project_root),
            external_inputs=external_inputs,
        )

        @bash_app(executors=[label], parsl_resource_specification=spec or None)
        def _run(stdout=None, stderr=None):  # noqa: ARG001 — Parsl conventions
            return full_cmd

        # Parsl's AUTO_LOGNAME picks unique paths under run_dir/task_logs/
        import parsl
        fut = _run(stdout=parsl.AUTO_LOGNAME, stderr=parsl.AUTO_LOGNAME)

        try:
            fut.result()
            exit_code = 0
        except BashExitFailure as e:
            exit_code = e.exitcode
        except Exception as e:
            # Pilot expired, worker died, etc.
            return ExecutionResult(
                exit_code=1,
                output_path=self.project_root / "results" / universe_id,
                metadata={
                    "backend": "slurm",
                    "executor": label,
                    "stderr": f"parsl task failed: {e!r}",
                },
            )

        stdout_tail = _read_tail(fut.stdout, _TAIL_CHARS) if fut.stdout else ""
        stderr_tail = _read_tail(fut.stderr, _TAIL_CHARS) if fut.stderr else ""

        return ExecutionResult(
            exit_code=exit_code,
            output_path=self.project_root / "results" / universe_id,
            metadata={
                "backend": "slurm",
                "executor": label,
                "stdout": stdout_tail,
                "stderr": stderr_tail,
            },
        )
```

Add the `_read_tail` helper near the top of the file (just below `_TAIL_CHARS`):

```python
def _read_tail(path: str, max_chars: int) -> str:
    """Return the last *max_chars* characters of *path*, empty if missing."""
    try:
        with open(path) as f:
            data = f.read()
    except OSError:
        return ""
    return data[-max_chars:] if len(data) > max_chars else data
```

- [ ] **Step 5: Run new integration tests to verify they pass**

Run: `uv run pytest tests/test_runner_parsl.py -v`
Expected: tests PASS (or cleanly skip if WorkQueue isn't installed locally — that's fine, smoke test in Task 14 will catch it).

- [ ] **Step 6: Commit**

```bash
git add src/lightcone/engine/runner.py tests/conftest.py tests/test_runner_parsl.py
git commit -m "feat(engine): rewrite _run_slurm on top of parsl.bash_app"
```

Note: the old SLURM tests will start failing at this point (because `_run_slurm` no longer reads the old `scheduler` config shape). That's expected — Task 11 deletes them.

---

## Task 8: Add `apply_cli_overrides_to_pilots` helper

**Files:**
- Modify: `src/lightcone/engine/parsl_backend.py`
- Modify: `tests/test_parsl_backend.py`

The agent–target contract keeps per-axis flags (`--qos`, `--constraint`,
`--account`, `--partition`, `--time-limit`) as human escape hatches. With
multi-pilot targets, "apply to all pilots" is the simplest unambiguous
semantics. This helper does that mutation in one place so both `lc run`
and `assets.py` can call it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_parsl_backend.py`:

```python
class TestApplyCliOverridesToPilots:
    def test_no_overrides_returns_unchanged(self):
        from lightcone.engine.parsl_backend import apply_cli_overrides_to_pilots
        pilots = {"cpu": {"nodes": 4, "walltime": "2h", "qos": "debug"}}
        out = apply_cli_overrides_to_pilots(pilots, {})
        assert out == pilots

    def test_qos_overrides_all_pilots(self):
        from lightcone.engine.parsl_backend import apply_cli_overrides_to_pilots
        pilots = {
            "cpu": {"nodes": 4, "walltime": "2h", "qos": "debug"},
            "gpu": {"nodes": 1, "walltime": "1h", "qos": "debug"},
        }
        out = apply_cli_overrides_to_pilots(pilots, {"qos": "regular"})
        assert out["cpu"]["qos"] == "regular"
        assert out["gpu"]["qos"] == "regular"

    def test_time_limit_overrides_walltime(self):
        from lightcone.engine.parsl_backend import apply_cli_overrides_to_pilots
        pilots = {"cpu": {"nodes": 4, "walltime": "2h"}}
        out = apply_cli_overrides_to_pilots(pilots, {"time_limit": "30m"})
        # CLI --time-limit maps onto pilot walltime
        assert out["cpu"]["walltime"] == "30m"

    def test_unknown_override_keys_ignored(self):
        from lightcone.engine.parsl_backend import apply_cli_overrides_to_pilots
        pilots = {"cpu": {"nodes": 4, "walltime": "2h"}}
        out = apply_cli_overrides_to_pilots(
            pilots, {"strategy": "fit", "garbage": "x"},
        )
        assert out == pilots

    def test_input_pilots_not_mutated(self):
        from lightcone.engine.parsl_backend import apply_cli_overrides_to_pilots
        pilots = {"cpu": {"nodes": 4, "walltime": "2h", "qos": "debug"}}
        apply_cli_overrides_to_pilots(pilots, {"qos": "regular"})
        # Original dict unchanged
        assert pilots["cpu"]["qos"] == "debug"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_parsl_backend.py::TestApplyCliOverridesToPilots -v`
Expected: `ImportError: cannot import name 'apply_cli_overrides_to_pilots'`.

- [ ] **Step 3: Implement the helper**

Append to `src/lightcone/engine/parsl_backend.py`:

```python
# --------------------------------------------------------------------------
# CLI override application
# --------------------------------------------------------------------------

#: CLI-flag → pilot-key mapping. ``time_limit`` maps to ``walltime`` because
#: at pilot scope, the user's "time-limit" flag describes the allocation
#: walltime, not a per-task wall.
_CLI_TO_PILOT_KEY = {
    "qos": "qos",
    "constraint": "constraint",
    "account": "account",
    "partition": "partition",
    "time_limit": "walltime",
}


def apply_cli_overrides_to_pilots(
    pilots: dict[str, Any],
    cli_overrides: dict[str, Any],
) -> dict[str, Any]:
    """Return a new pilots dict with CLI overrides applied to every pilot.

    Per the agent–target contract, per-axis CLI flags apply uniformly to
    all pilots — there is no per-pilot CLI surface. Unknown keys (e.g.,
    the now-dead ``strategy``) are ignored.
    """
    if not cli_overrides:
        return pilots
    out: dict[str, Any] = {}
    for label, pilot in pilots.items():
        new_pilot = dict(pilot)
        for cli_key, pilot_key in _CLI_TO_PILOT_KEY.items():
            if (val := cli_overrides.get(cli_key)) is not None:
                new_pilot[pilot_key] = val
        out[label] = new_pilot
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_parsl_backend.py::TestApplyCliOverridesToPilots -v`
Expected: all 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lightcone/engine/parsl_backend.py tests/test_parsl_backend.py
git commit -m "feat(engine): apply_cli_overrides_to_pilots helper"
```

---

## Task 9: Wire `parsl.load` into `lc run`; drop `--strategy`

**Files:**
- Modify: `src/lightcone/cli/commands.py`
- Modify: `tests/test_cli_run.py`

- [ ] **Step 1: Add `_no_parsl_load` autouse fixture to test_cli_run.py**

The existing file is 17 lines. Read it first to confirm structure, then add this fixture at the top (after existing imports):

```python
import contextlib
import pytest


@pytest.fixture(autouse=True)
def _no_parsl_load(monkeypatch):
    """Prevent CLI tests from booting a real DFK."""
    @contextlib.contextmanager
    def fake_load(*a, **k):
        yield
    monkeypatch.setattr("parsl.load", fake_load)
```

- [ ] **Step 2: Drop the `--strategy` Click option from `run`**

In `src/lightcone/cli/commands.py`, around line 1092, locate and **delete**
the entire `--strategy` decorator block:

```python
@click.option("--strategy", default=None,
              type=click.Choice(["fit", "switch"]),
              help="adjustment when options exceed limits: 'fit' trims resources "
                   "to stay in the selected qos (default); 'switch' keeps resources "
                   "and picks another qos")
```

Also delete the `strategy: str | None,` parameter from the `run` function
signature, and the `if strategy: cli_overrides["strategy"] = strategy`
block from the body.

The fit/switch logic was deleted in the new model — pilot is sized once
in the target file. Keeping the dead flag would mislead users.

- [ ] **Step 3: Wrap `dg.materialize` in `with parsl.load(...):`**

Replace the entire `run` function body (from after the docstring) with:

```python
def run(
    outputs: tuple[str, ...],
    universe: str | None,
    target: str | None,
    no_build: bool,
    qos: str | None,
    constraint: str | None,
    time_limit: str | None,
    account: str | None,
    partition: str | None,
) -> None:
    """Materialize ASTRA outputs via Dagster.

    Runs recipes to produce outputs. Without arguments, materializes all
    outputs for all universes. Container build specs are automatically
    built before execution unless --no-build is given.

    Examples:
        lc run                           # all outputs, all universes
        lc run accuracy                  # specific output
        lc run --universe baseline       # specific universe
        lc run accuracy -u baseline      # specific output + universe
        lc run --target perlmutter       # run on the configured perlmutter target
    """
    import parsl as _parsl
    from lightcone.engine.assets import build_definitions
    from lightcone.engine.parsl_backend import (
        PilotConfigError,
        apply_cli_overrides_to_pilots,
        build_parsl_config,
        validate_pilots_against_qos,
    )
    from lightcone.engine.targets import load_target

    output_names = list(outputs)
    project_path = Path.cwd()
    if not (project_path / "astra.yaml").exists():
        console.print("[red]Error:[/red] No astra.yaml found in current directory.")
        raise SystemExit(1)

    target_name = target
    if not target_name:
        lightcone_data = _load_lightcone_config(project_path)
        target_name = lightcone_data.get("target")
        if not target_name:
            from lightcone.engine.targets import load_user_config
            target_name = load_user_config().get("default_target")

    target_config: dict[str, Any] | None = None
    if target_name and target_name != "local":
        target_config = load_target(target_name)

    cli_overrides: dict[str, Any] = {}
    if qos:
        cli_overrides["qos"] = qos
    if constraint:
        cli_overrides["constraint"] = constraint
    if time_limit:
        cli_overrides["time_limit"] = time_limit
    if account:
        cli_overrides["account"] = account
    if partition:
        cli_overrides["partition"] = partition

    universe_id = universe or "baseline"
    backend = (target_config or {}).get("backend", "local")

    # Apply CLI overrides to pilots before validation/Parsl-config build,
    # so the validated and dispatched config matches the agent's intent.
    if backend == "slurm" and target_config is not None:
        target_config = dict(target_config)  # don't mutate caller's dict
        target_config["pilots"] = apply_cli_overrides_to_pilots(
            target_config.get("pilots") or {}, cli_overrides,
        )

    def _materialize() -> None:
        defs = build_definitions(
            project_path, target_config=target_config, universe_id=universe_id,
            no_build=no_build, cli_overrides=cli_overrides or None,
            target_name=target_name,
        )

        console.print("[bold]Materializing outputs...[/bold]")
        import dagster as dg

        all_assets = list(defs.resolve_all_asset_specs())
        if output_names:
            selection = [
                dg.AssetKey([universe_id] + o.split("."))
                for o in output_names
            ]
        else:
            selection = [
                spec.key for spec in all_assets
                if not (spec.metadata or {}).get('external', False)
            ]

        dagster_yaml_path = _find_dagster_yaml(project_path)
        if dagster_yaml_path is None:
            lightcone_dir = project_path / ".lightcone"
            lightcone_dir.mkdir(parents=True, exist_ok=True)
            dagster_yaml_path = lightcone_dir / "dagster.yaml"
            dagster_yaml_content = {
                "storage": {"sqlite": {"base_dir": "results/.dagster"}},
            }
            dagster_yaml_path.write_text(
                yaml.dump(dagster_yaml_content, default_flow_style=False,
                          sort_keys=False)
            )
        instance = dg.DagsterInstance.from_config(str(dagster_yaml_path.parent))

        try:
            result = dg.materialize(
                assets=list(defs.assets),
                selection=selection,
                instance=instance,
            )
            if result.success:
                console.print("[green]✓[/green] Materialization complete")
            else:
                console.print("[red]✗[/red] Materialization failed")
                raise SystemExit(1)
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            raise SystemExit(1)

    if backend == "slurm":
        try:
            validate_pilots_against_qos(
                pilots=target_config["pilots"], target_name=target_name,
            )
        except PilotConfigError as e:
            console.print(f"[red]Pilot config rejected:[/red] {e}")
            raise SystemExit(1) from None
        parsl_config = build_parsl_config(target_config, project_root=project_path)
        with _parsl.load(parsl_config):
            _materialize()
    else:
        _materialize()
```

`parsl.load(config)` is a context manager that returns the loaded DFK on
enter and calls `dfk.cleanup()` on exit (verified against parsl ≥ 2024.1).

- [ ] **Step 4: Run all CLI tests**

Run: `uv run pytest tests/test_cli.py tests/test_cli_run.py -v`
Expected: all PASS. The `_no_parsl_load` fixture stubs out the actual
DFK boot.

- [ ] **Step 5: Commit**

```bash
git add src/lightcone/cli/commands.py tests/test_cli_run.py
git commit -m "feat(cli): wrap lc run in parsl.load for SLURM; drop dead --strategy"
```

---

## Task 10: Rewrite `assets.py` SLURM branch for the new schema

**Files:**
- Modify: `src/lightcone/engine/assets.py`
- Modify: `tests/test_assets.py`

`build_definitions(...)` currently builds a `runner_config["scheduler"]`
dict from the old per-recipe schema (`resolve_run_config`, intent
options, etc., lines ~377-431 of `assets.py`). With the pilot model,
the runner only needs `target_config["pilots"]` and
`target_config["container_runtime"]`. This task collapses the SLURM
branch.

- [ ] **Step 1: Update test_assets.py for the new target shape**

Run: `uv run grep -n '"scheduler"' tests/test_assets.py`

For each match, replace the old shape:
```python
target_config={"scheduler": {...}, "backend": "slurm"}
```
with the new shape:
```python
target_config={
    "backend": "slurm",
    "container_runtime": "podman-hpc",
    "pilots": {"cpu": {"nodes": 1, "walltime": "30m", "account": "m1234"}},
}
```

Run: `uv run pytest tests/test_assets.py -v`
Expected: any tests that exercised the old `scheduler:` resolution now
fail — that's the next step.

- [ ] **Step 2: Replace the SLURM branch in `build_definitions`**

In `src/lightcone/engine/assets.py`, locate the `if backend == "slurm":`
block (around line 377-431) and replace it with:

```python
        if backend == "slurm":
            # Pilot model: the runner only needs the pilots dict and the
            # container runtime. CLI overrides have already been applied
            # to pilots in `lc run`. The intent-based-options machinery
            # (qos/constraint defaults, choices) lives in target.yaml
            # and was applied at load-target time.
            runner_config["pilots"] = target_config.get("pilots", {})
        else:
```

Delete the now-unused imports inside the SLURM branch:
```python
from lightcone.engine.targets import (
    get_cache_key_overrides,
    get_option_choices,
    get_option_default,
    resolve_run_config,
)
```

These imports stay valid in `targets.py` (other callers may use them);
just remove the `assets.py` import.

- [ ] **Step 3: Re-run test_assets.py**

Run: `uv run pytest tests/test_assets.py -v`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add src/lightcone/engine/assets.py tests/test_assets.py
git commit -m "refactor(engine): assets.py SLURM branch uses pilots schema"
```

---

## Task 11: Delete dead SLURM code from runner.py

**Files:**
- Modify: `src/lightcone/engine/runner.py`
- Modify: `tests/test_runner.py`

- [ ] **Step 1: Delete dead test classes**

In `tests/test_runner.py`, delete these classes wholesale:

- `TestSlurmResourceTranslation` (lines ~81-191)
- `TestNormaliseTimeLimit` (~199-216)
- `TestGenerateSbatchScript` (~224-389)
- `TestSlurmRunner` (~412-551)
- `TestCheckSacct` (~559-598)
- `TestExternalInputs` (~605-686)
- `TestQoSValidation` (~694-end)

Also delete the now-unused imports at the top of the file:

```python
# DELETE these from the import block:
from lightcone.engine.runner import (
    _check_sacct,            # delete
    _normalise_time_limit,   # delete
    _parse_sbatch_job_id,    # delete
    _podman_hpc_run_command, # delete
    _shell_quote,            # delete
    generate_sbatch_script,  # delete
    translate_resources_to_slurm_directives,  # delete
)
```

Keep:
```python
from lightcone.engine.runner import (
    ASTRAContainerRunner,
    translate_resources_to_docker_flags,
)
```

Also delete the entire `TestHelpers` class (it tests `_parse_sbatch_job_id` and `_shell_quote`, both about to be deleted).

- [ ] **Step 2: Verify the remaining tests still pass**

Run: `uv run pytest tests/test_runner.py -v`
Expected: PASS — only `TestResourceTranslation`, `TestDockerRunner`, `TestBuildRecipeShellCommand` remain.

- [ ] **Step 3: Delete dead code from runner.py**

In `src/lightcone/engine/runner.py`, delete (in this order, top-to-bottom):

- The entire `_validate_and_adjust_qos` method (~line 466-602)
- The static `_parse_time_minutes` method (~line 604-624)
- The original `_run_slurm` body — already replaced in Task 7, no further action
- The entire SLURM helpers section starting at the `# SLURM helpers` comment (~line 776):
  - `translate_resources_to_slurm_directives`
  - `_normalise_time_limit`
  - `generate_sbatch_script`
  - `_podman_hpc_run_command` (the one with the `scheduler_config` param — leaves `_podman_hpc_run_command_inline` intact)
  - `_parse_sbatch_job_id`
  - `_poll_slurm_job`
  - `_check_sacct`
  - `_check_squeue_fallback`

After deletion, the file should end with `_podman_hpc_run_command_inline` (added in Task 6) and `_run_slurm`'s new body.

- [ ] **Step 4: Verify nothing else imports the deleted symbols**

Run: `uv run grep -rn "_validate_and_adjust_qos\|generate_sbatch_script\|_parse_sbatch_job_id\|_poll_slurm_job\|_check_sacct\|_check_squeue_fallback\|translate_resources_to_slurm_directives\|_normalise_time_limit" src/ tests/ --include="*.py"`
Expected: no matches.

If any matches surface in `src/`, fix them; if in `tests/`, the corresponding test class was missed in Step 1.

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest -x`
Expected: all PASS. This is the moment of truth — the new path fully replaces the old.

- [ ] **Step 6: Run linters**

```bash
uv run ruff check src/ tests/
uv run mypy src/
```
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add src/lightcone/engine/runner.py tests/test_runner.py
git commit -m "refactor(engine): delete legacy per-recipe SLURM code

Removed (now obsolete with Parsl-pilot model):
  - _validate_and_adjust_qos and per-recipe QoS clamping
  - generate_sbatch_script + sbatch directive translation
  - _poll_slurm_job, _check_sacct, _check_squeue_fallback
  - sbatch helpers (_parse_sbatch_job_id, _shell_quote)
  - test classes covering the above

Net: ~650 LOC deleted from runner.py."
```

---

## Task 12: Adjust target schema documentation + add `pilots:` migration note

**Files:**
- Create: `docs/hpc/parsl-pilot.md`
- Modify: `docs/hpc/` index (if one exists; otherwise the `index.md` of `docs/`)

- [ ] **Step 1: Verify which HPC docs exist**

Run: `ls docs/hpc/`
Expected: list of existing HPC docs.

- [ ] **Step 2: Create the pilot doc**

Write `docs/hpc/parsl-pilot.md`:

```markdown
# Parsl pilot model for SLURM targets

`lc run --target <slurm-target>` acquires a single SLURM allocation
(the **pilot**) once at the start, then dispatches every recipe in the
analysis tree into that pool. The queue wait is paid once per `lc run`,
not once per recipe.

## Target schema

A SLURM target declares one or more pilots. Each pilot becomes a
`SlurmProvider` + `WorkQueueExecutor` in the underlying Parsl config.

```yaml
backend: slurm
site: perlmutter
container_runtime: podman-hpc
pilots:
  cpu:
    nodes: 4
    walltime: 2h
    qos: debug
    account: m1234
    worker_init: |
      module load python
      source $HOME/.lightcone/envs/perlmutter/bin/activate
  gpu:
    nodes: 2
    walltime: 1h
    qos: debug
    constraint: gpu
    account: m1234_g
    worker_init: |
      module load python cudatoolkit
      source $HOME/.lightcone/envs/perlmutter-gpu/bin/activate
```

Recognized pilot keys: `nodes`, `walltime`, `qos`, `account`,
`partition`, `constraint`, `worker_init`, `scheduler_options`,
`exclusive` (default `True`).

## Routing

A recipe is dispatched to a pilot based on its `resources`:

1. `resources.nodes > 1` and `mpi` pilot exists → `mpi`
2. `resources.gpus > 0` and `gpu` pilot exists → `gpu`
3. otherwise → `cpu`

A GPU recipe with no `gpu` pilot configured raises immediately at
dispatch time — better to fail fast than dispatch to a CPU allocation
that can't satisfy the request.

## `worker_init` essentials

Workers run on compute nodes, not the login node. They need the
project's Python environment available before tasks can run. Typical
`worker_init`:

```yaml
worker_init: |
  module load python
  source $HOME/.lightcone/envs/perlmutter/bin/activate
```

Anything in `worker_init` runs once per pilot, before tasks dispatch.

## Installing the WorkQueue dependency

WorkQueue's Python bindings come from the `ndcctools` conda package:

```bash
conda install -c conda-forge ndcctools
```

Without it, `lc run --target <slurm-target>` raises a clear error.

## Migrating from the old per-recipe SLURM backend

Old target shape (pre-2026-04):

```yaml
backend: slurm
scheduler:
  account: m1234
  qos: debug
options:
  qos: {choices: [debug, regular], default: debug}
```

New shape:

```yaml
backend: slurm
pilots:
  cpu:
    nodes: 4
    walltime: 2h
    account: m1234
    qos: debug
options:
  qos: {choices: [debug, regular], default: debug}
```

`scheduler:` is gone. The `nodes` and `walltime` that used to live
on individual recipes now describe the pilot's compute budget for
the whole `lc run`. Recipe-level `resources.cpus`/`memory`/`gpus`
still control per-task bin-packing inside the pilot.
```

- [ ] **Step 3: Commit**

```bash
git add docs/hpc/parsl-pilot.md
git commit -m "docs(hpc): document Parsl pilot model and target schema"
```

---

## Task 13: Update CLAUDE.md with the agent–target contract

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add the principle to CLAUDE.md**

In `CLAUDE.md`, find the section describing CLI Patterns or Configuration. Add (or expand) the following paragraph:

```markdown
## Agent–target contract

The agent invoking `lc run` must not need to know how compute happens —
not Parsl, not SLURM, not pilots, not blocks. Its entire interface to
compute is `lc run --target <name>`. If the target's compute envelope
is unsuitable, the human user picks a different target rather than
passing extra knobs at the CLI.

Per-axis flags (`--qos`, `--constraint`, `--account`, `--partition`,
`--time-limit`) remain for interactive human use but are not part of
the agent contract. When an agent needs different compute, switch
targets — do not stack overrides.
```

- [ ] **Step 2: Update the Repository Structure section to reference parsl_backend.py**

In the existing `src/lightcone/engine/` listing in `CLAUDE.md`, add the new module:

```markdown
├── parsl_backend.py        # Pilot config + executor routing for SLURM (Parsl)
```

And remove the now-misleading line:
```markdown
├── runner.py               # Execution backends: Docker, local, SLURM
```

Replace with:

```markdown
├── runner.py               # Execution backends: Docker, local, venv; SLURM dispatches via parsl_backend
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document agent-target contract in CLAUDE.md"
```

---

## Task 14: Manual smoke test placeholder

**Files:**
- Create: `tests/manual/test_perlmutter_smoke.py`
- Create: `tests/manual/__init__.py` (empty)

- [ ] **Step 1: Create the directory and stub**

Run: `mkdir -p tests/manual && touch tests/manual/__init__.py`

Then create `tests/manual/test_perlmutter_smoke.py`:

```python
"""Manual smoke test — NOT run in CI.

Run on a real Perlmutter login node with a configured target. Asserts:
  1. A SLURM allocation is acquired exactly once
  2. Three trivial recipes run sequentially within it
  3. The allocation is released on exit (squeue shows it gone)

Usage:
    cd <test-project>
    pytest tests/manual/test_perlmutter_smoke.py -v -s
"""
from __future__ import annotations

import os
import subprocess
import time

import pytest

PERLMUTTER_TARGET = os.environ.get("LC_SMOKE_TARGET", "perlmutter")


def _squeue_user_count(user: str) -> int:
    """Return number of jobs the user has in the queue."""
    out = subprocess.run(
        ["squeue", "-u", user, "--noheader"],
        capture_output=True, text=True,
    )
    return len([line for line in out.stdout.splitlines() if line.strip()])


@pytest.mark.skipif(
    "SLURM_CONF" not in os.environ and not os.path.exists("/etc/slurm/slurm.conf"),
    reason="not running on a SLURM head node",
)
def test_one_pilot_three_recipes(tmp_path):
    """End-to-end smoke: one pilot, three recipes, allocation released."""
    user = os.environ["USER"]
    before = _squeue_user_count(user)

    # Caller is responsible for cd'ing into a test project before
    # running this test. We just invoke `lc run` and observe.
    result = subprocess.run(
        ["lc", "run", "--target", PERLMUTTER_TARGET],
        capture_output=True, text=True, timeout=1800,
    )
    assert result.returncode == 0, result.stderr

    # Give SLURM a moment to release the allocation
    time.sleep(15)
    after = _squeue_user_count(user)
    assert after == before, (
        f"Allocation not released: before={before}, after={after}"
    )
```

- [ ] **Step 2: Make sure pytest doesn't pick it up by default**

Add to `pyproject.toml` under `[tool.pytest.ini_options]` (create the section if it doesn't exist):

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
norecursedirs = ["tests/manual"]
```

- [ ] **Step 3: Verify CI tests don't include the smoke test**

Run: `uv run pytest --collect-only -q | grep manual || echo "OK — manual not collected"`
Expected: `OK — manual not collected`.

- [ ] **Step 4: Commit**

```bash
git add tests/manual/ pyproject.toml
git commit -m "test: add manual Perlmutter smoke test (excluded from CI)"
```

---

## Task 15: Final verification

**Files:**
- (none — verification only)

- [ ] **Step 1: Full test suite, clean run**

Run: `uv run pytest -v`
Expected: all PASS, no XFAILs other than documented WorkQueue skips.

- [ ] **Step 2: Lint and type-check**

```bash
uv run ruff check src/ tests/
uv run mypy src/
```
Expected: clean.

- [ ] **Step 3: Confirm net LOC change matches spec**

Run: `git diff --shortstat main`
Expected: roughly −400 net lines in `src/lightcone/engine/`.

- [ ] **Step 4: Confirm runner.py shrank as planned**

Run: `wc -l src/lightcone/engine/runner.py`
Expected: ~540 lines (was 1107 — a ~50% shrink).

- [ ] **Step 5: Confirm no dead imports leak**

Run: `uv run ruff check src/lightcone/engine/runner.py`
Expected: clean — no F401 unused imports.

- [ ] **Step 6: Verify Langfuse telemetry still fires**

The spec lists this as an open question. Telemetry hooks live in
`claude/lightcone/hooks/langfuse_hook.py` and fire per-recipe.
Nothing structural should have changed — assets still call
`runner.execute(...)` per asset. To confirm, manually trace the call
path:

Run: `uv run grep -n "langfuse\|trace\|telemetry" src/lightcone/engine/runner.py src/lightcone/engine/assets.py`
Expected: any existing telemetry call sites are unchanged. Document
the trace results in the PR description so a reviewer can confirm.

- [ ] **Step 7: Open the smoke-test PR description**

When opening the PR, the description **must** include the manual smoke checklist:

```markdown
## Manual smoke checklist (run before merge)

- [ ] `lc run --target perlmutter` on a 3-recipe analysis: one allocation acquired, all three run, allocation released within 30s of completion.
- [ ] Ctrl-C mid-run → allocation released within 30s (`squeue -u $USER` confirms).
- [ ] Recipe failure → other recipes continue, only the failed asset marked failed in Dagster UI.
- [ ] WorkQueue not installed → `lc run --target perlmutter` exits with the documented "install ndcctools" error.
- [ ] `lc run --target local` (non-SLURM backend) is unchanged — no Parsl involvement.
```
