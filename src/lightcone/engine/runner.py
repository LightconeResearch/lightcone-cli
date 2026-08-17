"""Per-rule execution helper invoked from the generated Snakefile.

Each rule's ``run:`` block boils down to one call to :func:`run_rule`,
which implements the worker sequence (spec §6):

1. **pre-gate** — recompute ``env_version`` from the project tree and
   compare against the value baked into the job at generation time; a
   mid-run relock aborts loudly instead of materializing under a
   different environment than the manifest will claim.
2. **env check** — direct mode: the env prefix exists and
   ``uv sync --locked --exact --check`` passes (a true no-write
   env-vs-lock verification); containerized mode: the baked image
   identity and the driver-resolved digest match the job's pins.
3. **boundary exec** — the recipe runs through the exec boundary
   (:mod:`lightcone.engine.boundary`) with the offline overlay
   (converge once, then never write to the environment) and the
   ambient ``UV_*`` scrub; ``--require-sandbox`` is enforced here,
   worker-side, against the *probed* enforcement level.
4. **post-gate** — ``env_version`` re-checked before
   :func:`~lightcone.engine.manifest.write_manifest` commits, so the
   double gate brackets the recipe.

Output is emitted as sentinel-prefixed lines (:data:`SENTINEL`) the
dask executor extracts; on non-zero exit the manifest is **not**
written and :class:`subprocess.CalledProcessError` propagates so
Snakemake records the job as failed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Literal

from lightcone.engine.contract import (
    IMAGE_DIGEST_ENV,
    NO_SANDBOX_ENV,
    REQUIRE_SANDBOX_ENV,
)

#: Lines from the runner are prefixed with this so ``_run_shell`` in the
#: dask executor can distinguish them from snakemake/dask noise. Chosen
#: to be vanishingly unlikely in real recipe output (printable ASCII,
#: column-0 anchored, distinctive). Kept short to minimise capture cost.
SENTINEL = "__LCSTREAM__::"



def _emit(line: str = "") -> None:
    """Write one sentinel-prefixed line to stdout and flush.

    The flush matters: we run inside a child snakemake subprocess whose
    stdout is captured by the worker's ``_run_shell``; without flushing
    the recipe output would arrive after ``rule_end`` if Python decides
    to block-buffer.
    """
    sys.stdout.write(f"{SENTINEL}{line}\n")
    sys.stdout.flush()


class RuleGateError(RuntimeError):
    """A worker-side integrity gate refused to run (or commit) the rule."""


def _current_env_version(root: Path) -> str:
    from lightcone.engine.environment import load_environment

    return load_environment(root).env_version


def _gate_env(root: Path, expected: str, *, when: str) -> None:
    actual = _current_env_version(root)
    if actual != expected:
        raise RuleGateError(
            f"environment changed mid-run ({when}): the lock/pyproject "
            "no longer matches the environment this run started with — "
            "re-run `lc materialize`."
        )


def _env_check(root: Path, job: Any) -> None:
    """Step 2: verify the execution environment matches the job's pins."""
    if job.worker_runtime == "container":
        from lightcone.engine.image.constants import IDENTITY_PATH

        try:
            baked = json.loads(Path(IDENTITY_PATH).read_text())
        except (OSError, json.JSONDecodeError) as e:
            raise RuleGateError(
                f"containerized job outside an lc image ({IDENTITY_PATH} "
                f"unreadable: {e})"
            ) from e
        if baked.get("env_version") != job.env_version:
            raise RuleGateError(
                "image/environment mismatch: the running image was baked "
                f"for env_version {baked.get('env_version')}, the job "
                f"expects {job.env_version} — run `lc build`."
            )
        running = os.environ.get(IMAGE_DIGEST_ENV)
        if job.image_digest and running != job.image_digest:
            raise RuleGateError(
                f"image digest mismatch: driver pinned {job.image_digest}, "
                f"running container reports {running!r}."
            )
        return

    venv = root / ".venv"
    if not venv.is_dir():
        raise RuleGateError(
            f"{venv} does not exist — the environment was never converged."
        )
    proc = subprocess.run(
        [
            "uv", "sync", "--locked", "--exact", "--check",
            "--project", str(root),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuleGateError(
            "the environment does not match the lock "
            f"(`uv sync --check` failed):\n{proc.stderr.strip()}"
        )


def _exec_env() -> dict[str, str]:
    """The recipe's process environment: ambient minus the UV_* steering
    surface, plus the offline overlay — converge once, then never write
    to the environment."""
    from lightcone.engine import uv_env

    env = dict(os.environ)
    uv_env.scrub(env)
    env.update(uv_env.OFFLINE_OVERLAY)
    return env


def run_rule(
    *,
    rule_key: str,
    universe: str,
    output_dir: Path,
    inputs: dict[str, Path],
    cfg: dict[str, Any],
) -> None:
    """Execute one rule through the worker sequence; write its manifest.

    Called from the generated Snakefile's ``run:`` block with
    ``cwd == project root`` (snakemake ``-d``). Recipe stdout and
    stderr are interleaved by capture order.

    On non-zero exit, the manifest is **not** written. Snakemake will
    treat the rule as failed; ``lc verify`` won't see a stale manifest
    pointing at incomplete data.
    """
    from lightcone.engine.attestation import capture_runtime_attestation
    from lightcone.engine.boundary import ExecScope, get_boundary
    from lightcone.engine.job import RuleJob
    from lightcone.engine.manifest import write_manifest
    from lightcone.engine.validation import validate_output

    job = RuleJob.from_cfg(cfg)
    root = Path.cwd()

    t0 = time.monotonic()
    _emit(f"\033[2m▶\033[0m {rule_key} \033[2m[{universe}]\033[0m")

    try:
        _gate_env(root, job.env_version, when="pre-recipe")
        _env_check(root, job)
    except RuleGateError as e:
        _emit(f"  \033[31m{e}\033[0m")
        raise

    sandbox_mode: Literal["on", "off"] = (
        "off" if os.environ.get(NO_SANDBOX_ENV) == "1" else "on"
    )
    scope = ExecScope(
        project_root=root,
        output_dir=Path(output_dir),
        read_paths=tuple(Path(p) for p in inputs.values()),
        writable_project=job.writable_project,
        sandbox=sandbox_mode,
    )
    boundary = get_boundary()

    # --require-sandbox is enforced worker-side against the probed
    # enforcement level — the driver's kernel is not the worker's.
    if requirement := os.environ.get(REQUIRE_SANDBOX_ENV):
        probed = boundary.probe(scope)
        if probed.mechanism == "none":
            raise RuleGateError(
                "--require-sandbox: no sandbox mechanism is available on "
                f"this worker (probed: {probed.mechanism})."
            )
        if requirement == "declared-fs" and probed.fs != "declared":
            raise RuleGateError(
                "--require-sandbox=declared-fs: this worker can only "
                f"provide fs: {probed.fs}."
            )

    result = boundary.execute(job.shell_command, scope, env=_exec_env())

    for line in result.stdout.splitlines():
        _emit(f"  {line}")
    for line in result.stderr.splitlines():
        _emit(f"  {line}")
    for note in result.notes:
        _emit(f"  {note}")

    dt = time.monotonic() - t0
    if result.returncode != 0:
        _emit(
            f"\033[31m✗\033[0m {rule_key} \033[2m[{universe}]\033[0m   "
            f"exit={result.returncode}   {dt:.1f}s"
        )
        raise subprocess.CalledProcessError(result.returncode, job.shell_command)

    try:
        _gate_env(root, job.env_version, when="post-recipe")
    except RuleGateError as e:
        _emit(f"  \033[31m{e}\033[0m")
        raise

    write_manifest(
        output_dir=output_dir,
        inputs=inputs,
        cfg=job.to_cfg(),
        hermeticity=result.attestation.to_manifest(),
        attestation=capture_runtime_attestation(),
    )

    for warning in validate_output(
        output_dir, job.output_type, job.output_id
    ):
        _emit(f"  \033[33m⚠\033[0m {warning}")

    _emit(
        f"\033[32m✓\033[0m {rule_key} \033[2m[{universe}]\033[0m   {dt:.1f}s"
    )


__all__ = [
    "IMAGE_DIGEST_ENV",
    "NO_SANDBOX_ENV",
    "REQUIRE_SANDBOX_ENV",
    "SENTINEL",
    "RuleGateError",
    "run_rule",
]
