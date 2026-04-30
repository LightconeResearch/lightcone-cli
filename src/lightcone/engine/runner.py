"""Per-rule execution helper invoked from the generated Snakefile.

Each rule's ``run:`` block boils down to one call to :func:`run_rule`.
The helper:

* runs the rule's pre-rendered shell command (template substitution and
  container wrapping happen at Snakefile-generation time — see
  :func:`lightcone.engine.snakefile.render_recipe`) with stdout and
  stderr captured,
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
    from lightcone.engine.manifest import write_manifest
    from lightcone.engine.validation import validate_output

    t0 = time.monotonic()
    _emit(f"\033[2m▶\033[0m {rule_key} \033[2m[{universe}]\033[0m")

    proc = subprocess.run(
        cfg["shell_command"],
        shell=True,
        capture_output=True,
        text=True,
        check=False,
    )

    for line in proc.stdout.splitlines():
        _emit(f"  {line}")
    for line in proc.stderr.splitlines():
        _emit(f"  {line}")

    dt = time.monotonic() - t0
    if proc.returncode != 0:
        _emit(
            f"\033[31m✗\033[0m {rule_key} \033[2m[{universe}]\033[0m   "
            f"exit={proc.returncode}   {dt:.1f}s"
        )
        raise subprocess.CalledProcessError(proc.returncode, cfg["shell_command"])

    write_manifest(output_dir=output_dir, inputs=inputs, cfg=cfg)

    for warning in validate_output(
        output_dir, cfg.get("output_type"), cfg["output_id"]
    ):
        _emit(f"  \033[33m⚠\033[0m {warning}")

    _emit(
        f"\033[32m✓\033[0m {rule_key} \033[2m[{universe}]\033[0m   {dt:.1f}s"
    )


__all__ = ["SENTINEL", "run_rule"]
