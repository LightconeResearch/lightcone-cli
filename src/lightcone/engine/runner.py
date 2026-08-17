"""Per-rule execution helper invoked from the generated Snakefile.

Each rule's ``run:`` block boils down to one call to :func:`run_rule`.
The helper:

* executes the rule's pre-rendered shell command through the exec
  boundary (:mod:`lightcone.engine.boundary` — template substitution
  happens at Snakefile-generation time, enforcement at exec time) with
  stdout and stderr captured,
* emits a ``▶ rule [universe]`` header, the recipe's output, and a
  ``✓ rule [universe]   <duration>`` (or ``✗ … exit=N``) trailer,
  each line framed with a sentinel prefix the executor extracts,
* writes the per-output manifest on success,
* runs the validation hook on the materialized output,
* raises :class:`subprocess.CalledProcessError` on non-zero exit so
  Snakemake records the job as failed and halts the DAG.

The sentinel prefix (:data:`SENTINEL`) is what the dask executor's
``_run_shell`` looks for when it filters worker subprocess output —
anything else (snakemake bootstrap, dask logs, stray prints) is dropped
on the floor. This is the entire mechanism by which lc run shows clean,
narrative output without ever filtering against a moving target of
upstream log strings.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

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


def run_rule(
    *,
    rule_key: str,
    universe: str,
    output_dir: Path,
    inputs: dict[str, Path],
    cfg: dict[str, Any],
) -> None:
    """Execute one rule's pre-rendered shell command and write its manifest.

    Called from the generated Snakefile's ``run:`` block. Recipe stdout
    and stderr are interleaved by capture order (stdout first, then
    stderr) — Snakemake's own output capture has the same property and
    most recipes are well-behaved enough that this is fine.

    On non-zero exit, the manifest is **not** written. Snakemake will
    treat the rule as failed; ``lc verify`` won't see a stale manifest
    pointing at incomplete data.
    """
    from lightcone.engine.attestation import capture_runtime_attestation
    from lightcone.engine.boundary import ExecScope, get_boundary
    from lightcone.engine.manifest import write_manifest
    from lightcone.engine.validation import validate_output

    t0 = time.monotonic()
    _emit(f"\033[2m▶\033[0m {rule_key} \033[2m[{universe}]\033[0m")

    scope = ExecScope(
        project_root=Path.cwd(),
        output_dir=Path(output_dir),
        read_paths=tuple(Path(p) for p in inputs.values()),
        writable_project=bool(cfg.get("writable_project")),
    )
    result = get_boundary().execute(
        cfg["shell_command"], scope, env=dict(os.environ)
    )

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
        raise subprocess.CalledProcessError(result.returncode, cfg["shell_command"])

    write_manifest(
        output_dir=output_dir,
        inputs=inputs,
        cfg=cfg,
        hermeticity=result.attestation.to_manifest(),
        attestation=capture_runtime_attestation(),
    )

    for warning in validate_output(
        output_dir, cfg.get("output_type"), cfg["output_id"]
    ):
        _emit(f"  \033[33m⚠\033[0m {warning}")

    _emit(
        f"\033[32m✓\033[0m {rule_key} \033[2m[{universe}]\033[0m   {dt:.1f}s"
    )


__all__ = ["SENTINEL", "run_rule"]
