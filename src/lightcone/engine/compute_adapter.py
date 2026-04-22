"""Bridge between lightcone-cli's asset factory and the ``dagster-slurm`` library.

This module is the seam introduced by ADR-0001. It owns three responsibilities:

1. ``build_compute_resource`` — construct a ``dagster_slurm.ComputeResource``
   from a normalized lightcone-cli target YAML.
2. ``prepare_payload`` — write the per-materialization Python wrapper script
   (§4.3) that translates an ASTRA shell-command recipe into a dagster-pipes
   payload.
3. ``stage_artifacts`` — post-execution, copy the sbatch script and stdout/
   stderr back from the remote host into ``results/.slurm/`` so the existing
   on-disk layout is preserved.

``dagster-slurm`` is an optional dependency (``pip install lightcone-cli[slurm-next]``);
the import is deferred to the functions that need it so this module can be
imported in environments where it is not installed (for payload-wrapper unit
tests in particular).
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from dagster_slurm import ComputeResource  # type: ignore[import-not-found]

__all__ = [
    "PayloadCtx",
    "build_compute_resource",
    "prepare_payload",
    "stage_artifacts",
    "has_dagster_slurm",
]


@dataclass(frozen=True)
class PayloadCtx:
    """Post-generation context needed by ``stage_artifacts``."""

    universe_id: str
    output_id: str
    payload_path: Path
    project_root: Path
    target_config: dict[str, Any] | None = None


def has_dagster_slurm() -> bool:
    """Return whether ``dagster-slurm`` is importable in the current env."""
    try:
        import dagster_slurm  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        return False
    return True


def _require_dagster_slurm() -> None:
    if not has_dagster_slurm():
        raise ImportError(
            "dagster-slurm is not installed. Install with "
            "`pip install lightcone-cli[slurm-next]` (requires Python 3.12). "
            "See ADR-0001 §4.7."
        )


def build_compute_resource(target_config: dict[str, Any]) -> ComputeResource:
    """Construct a ``ComputeResource`` from a normalized target config.

    ``target_config`` must be in the post-ADR-0001 shape (§4.4).
    Call :func:`lightcone.engine.targets.normalize_target` first when the
    input may still be in the legacy ``backend:`` shape.

    Only ``mode=local`` and ``mode=slurm`` are fully wired.  ``slurm-session``
    falls back to ``slurm``; ``docker`` raises ``ValueError`` (Docker targets
    use the pre-existing runner, not dagster-slurm).
    """
    _require_dagster_slurm()

    import sys  # noqa: PLC0415

    from dagster_slurm import (
        BashLauncher,  # type: ignore[import-not-found]  # noqa: PLC0415
        ComputeResource,  # type: ignore[import-not-found]  # noqa: PLC0415
        SlurmQueueConfig,  # type: ignore[import-not-found]  # noqa: PLC0415
        SlurmResource,  # type: ignore[import-not-found]  # noqa: PLC0415
        SSHConnectionResource,  # type: ignore[import-not-found]  # noqa: PLC0415
    )
    from dagster_slurm.config.environment import (
        ExecutionMode,  # type: ignore[import-not-found]  # noqa: PLC0415
    )

    mode_str: str = target_config.get("mode", "local")

    if mode_str == "docker":
        raise ValueError(
            "Docker targets use the existing ASTRAContainerRunner, not dagster-slurm. "
            "Pass target_config only for 'local' or 'slurm' mode."
        )

    if mode_str == "local":
        return ComputeResource(
            mode=ExecutionMode.LOCAL,
            default_launcher=BashLauncher(),
            # Point at the active venv so no pixi-pack is attempted.
            pre_deployed_env_path=sys.prefix,
        )

    if mode_str in ("slurm", "slurm-session"):
        dagster_mode = ExecutionMode.SLURM if mode_str == "slurm" else ExecutionMode.SLURM_SESSION

        ssh_cfg = target_config.get("ssh") or {}
        queue_cfg = target_config.get("queue") or {}

        ssh_kwargs: dict[str, Any] = {
            "host": ssh_cfg["host"],
            "user": ssh_cfg.get("user", ""),
        }
        if ssh_cfg.get("port"):
            ssh_kwargs["port"] = int(ssh_cfg["port"])
        if ssh_cfg.get("key_path"):
            ssh_kwargs["key_path"] = ssh_cfg["key_path"]
        elif ssh_cfg.get("password"):
            ssh_kwargs["password"] = ssh_cfg["password"]
        ssh = SSHConnectionResource(**ssh_kwargs)

        slurm_queue = SlurmQueueConfig(
            **{k: v for k, v in {
                "partition": queue_cfg.get("partition"),
                "account": queue_cfg.get("account"),
                "qos": queue_cfg.get("qos"),
                "time_limit": queue_cfg.get("time_limit"),
                "cpus": queue_cfg.get("cpus"),
                "mem": queue_cfg.get("mem"),
                "mem_per_cpu": queue_cfg.get("mem_per_cpu"),
                "gpus_per_node": queue_cfg.get("gpus_per_node"),
                "num_nodes": queue_cfg.get("nodes"),
            }.items() if v is not None},
        )

        slurm_res = SlurmResource(
            ssh=ssh,
            queue=slurm_queue,
            remote_base=target_config.get("remote_base") or None,
        )

        return ComputeResource(
            mode=dagster_mode,
            slurm=slurm_res,
            default_launcher=BashLauncher(),
            pre_deployed_env_path=target_config.get("pre_deployed_env_path") or sys.prefix,
        )

    raise ValueError(f"Unsupported target mode for dagster-slurm: {mode_str!r}")


def prepare_payload(
    *,
    command: str,
    universe_id: str,
    output_id: str,
    project_root: Path,
    params: dict[str, Any] | None = None,
    external_inputs: dict[str, str] | None = None,
    cwd_override: str | None = None,
    container_wrap: list[str] | None = None,
    extra_sbatch_directives: list[str] | None = None,
    payload_override: Path | None = None,
    target_config: dict[str, Any] | None = None,
) -> tuple[Path, PayloadCtx]:
    """Write a per-materialization payload wrapper to ``results/.payloads/``.

    If ``payload_override`` is provided, the script at that path is copied
    verbatim (honours the ``recipe.payload:`` escape hatch of ADR-0001 §4.3).
    Otherwise, an auto-generated wrapper invokes ``command`` with CLI args
    flattened from ``params``, wrapped in ``container_wrap`` if supplied.

    Returns the path to the written file plus a :class:`PayloadCtx` used by
    :func:`stage_artifacts` once the run completes.
    """
    payloads_dir = project_root / "results" / ".payloads"
    payloads_dir.mkdir(parents=True, exist_ok=True)
    payload_path = payloads_dir / f"{universe_id}__{output_id}.py"

    if payload_override is not None:
        shutil.copyfile(payload_override, payload_path)
    else:
        payload_path.write_text(
            _render_wrapper(
                command=command,
                universe_id=universe_id,
                output_id=output_id,
                params=params or {},
                external_inputs=external_inputs,
                cwd_override=cwd_override,
                container_wrap=container_wrap,
                extra_sbatch_directives=extra_sbatch_directives,
            )
        )

    return payload_path, PayloadCtx(
        universe_id=universe_id,
        output_id=output_id,
        payload_path=payload_path,
        project_root=project_root,
        target_config=target_config,
    )


def _flatten_params_to_cli_args(params: dict[str, Any]) -> list[str]:
    """Turn a decision-param dict into a ``--key value`` CLI-arg list.

    Bool ``True`` becomes a bare ``--flag`` (no value); ``False`` is skipped.
    Other scalar values are stringified. Non-scalar values (dict/list) are
    JSON-encoded.
    """
    args: list[str] = []
    for key, val in params.items():
        flag = f"--{key.replace('_', '-')}"
        if val is True:
            args.append(flag)
        elif val is False or val is None:
            continue
        elif isinstance(val, (dict, list)):
            args.extend([flag, json.dumps(val)])
        else:
            args.extend([flag, str(val)])
    return args


def _render_wrapper(
    *,
    command: str,
    universe_id: str,
    output_id: str,
    params: dict[str, Any],
    external_inputs: dict[str, str] | None,
    cwd_override: str | None,
    container_wrap: list[str] | None,
    extra_sbatch_directives: list[str] | None,
) -> str:
    """Render the payload wrapper source (ADR-0001 §4.3)."""
    cli_args = _flatten_params_to_cli_args(params)
    sbatch_lines = [f"#SBATCH {d}" for d in (extra_sbatch_directives or [])]
    sbatch_block = "\n".join(sbatch_lines)
    if sbatch_block:
        sbatch_block += "\n"

    ext = external_inputs or {}
    # json.dumps produces valid Python literals for str, list[str], None, dict.
    return f'''\
#!/usr/bin/env python3
{sbatch_block}# Auto-generated by lightcone.engine.compute_adapter — do not edit.
# universe={universe_id} output={output_id}
from __future__ import annotations

import os
import shlex
import subprocess
import sys

CMD = {json.dumps(command)}
CLI_ARGS = {json.dumps(cli_args)}
CWD = {json.dumps(cwd_override)}
CONTAINER_WRAP = {json.dumps(container_wrap)}
EXTERNAL_INPUTS = {json.dumps(ext)}
UNIVERSE_ID = {json.dumps(universe_id)}
OUTPUT_ID = {json.dumps(output_id)}

try:
    from dagster_pipes import open_dagster_pipes
except ImportError:  # parity / legacy local runs without dagster-pipes
    class _NoopPipes:
        class log:
            @staticmethod
            def info(msg: str) -> None: print(msg, flush=True)
        def report_asset_materialization(self, **_: object) -> None: ...
    def open_dagster_pipes():  # type: ignore[no-redef]
        class _Ctx:
            def __enter__(self): return _NoopPipes()
            def __exit__(self, *_): return False
        return _Ctx()


def _main() -> int:
    env = dict(os.environ)
    env["ASTRA_UNIVERSE"] = UNIVERSE_ID
    env["ASTRA_OUTPUT"] = OUTPUT_ID
    for name, path in EXTERNAL_INPUTS.items():
        env[f"ASTRA_INPUT_{{name.upper()}}"] = path

    full = shlex.split(CMD) + list(CLI_ARGS)
    if CONTAINER_WRAP:
        full = list(CONTAINER_WRAP) + full

    with open_dagster_pipes() as pipes:
        pipes.log.info(f"astra recipe: {{shlex.join(full)}}")
        proc = subprocess.run(full, cwd=CWD or None, env=env, check=False)
        pipes.report_asset_materialization(
            metadata={{
                "exit_code": proc.returncode,
                "command": shlex.join(full),
                "universe": UNIVERSE_ID,
                "output": OUTPUT_ID,
            }}
        )
    return proc.returncode


if __name__ == "__main__":
    sys.exit(_main())
'''


def stage_artifacts(
    completed: Any,
    ctx: PayloadCtx,
    *,
    run_id: str | None = None,
) -> None:
    """Copy remote sbatch script and logs into ``results/.slurm/``.

    In local mode this is a no-op — there are no remote files to fetch.

    In SLURM mode, reads ``{remote_base}/runs/{run_id}/job.sh`` and
    ``slurm-*.{out,err}`` via SSH and writes them to
    ``results/.slurm/{output_id}_{universe_id}.*`` to preserve the existing
    on-disk layout used by ``lc status`` and tail-style debugging.

    Requires ``ctx.target_config`` (set by :func:`prepare_payload` when called
    from :func:`~lightcone.engine.assets.build_definitions`) and ``run_id``
    (the Dagster run ID, available as ``context.run_id`` inside an asset).
    """
    if ctx.target_config is None or run_id is None:
        return

    mode = ctx.target_config.get("mode", "local")
    if mode not in ("slurm", "slurm-session"):
        return

    ssh_cfg = ctx.target_config.get("ssh") or {}
    remote_base = ctx.target_config.get("remote_base")
    if not ssh_cfg.get("host") or not remote_base:
        return

    _stage_slurm_artifacts(
        ssh_cfg=ssh_cfg,
        remote_base=remote_base,
        run_id=run_id,
        ctx=ctx,
    )


def _stage_slurm_artifacts(
    *,
    ssh_cfg: dict[str, Any],
    remote_base: str,
    run_id: str,
    ctx: PayloadCtx,
) -> None:
    """Fetch job.sh + slurm-*.{out,err} from the SLURM node and write locally."""
    from dagster_slurm import (
        SSHConnectionResource,  # type: ignore[import-not-found]  # noqa: PLC0415
    )
    from dagster_slurm.helpers.ssh_pool import (
        SSHConnectionPool,  # type: ignore[import-not-found]  # noqa: PLC0415
    )

    ssh_kwargs: dict[str, Any] = {
        "host": ssh_cfg["host"],
        "user": ssh_cfg.get("user", ""),
    }
    if ssh_cfg.get("port"):
        ssh_kwargs["port"] = int(ssh_cfg["port"])
    if ssh_cfg.get("key_path"):
        ssh_kwargs["key_path"] = ssh_cfg["key_path"]
    elif ssh_cfg.get("password"):
        ssh_kwargs["password"] = ssh_cfg["password"]
    ssh_resource = SSHConnectionResource(**ssh_kwargs)

    run_dir = f"{remote_base}/runs/{run_id}"
    slurm_dir = ctx.project_root / "results" / ".slurm"
    slurm_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{ctx.output_id}_{ctx.universe_id}"

    with SSHConnectionPool(ssh_resource) as pool:
        # Copy the sbatch script
        content = pool.run(f"cat {run_dir}/job.sh 2>/dev/null || true").strip()
        if content:
            (slurm_dir / f"{stem}.sh").write_text(content + "\n")

        # Copy stdout / stderr (filename includes the SLURM job ID as a number)
        for ext in ("out", "err"):
            listing = pool.run(
                f"ls {run_dir}/slurm-*.{ext} 2>/dev/null || true"
            ).strip()
            for remote_path in listing.splitlines():
                remote_path = remote_path.strip()
                if remote_path:
                    body = pool.run(f"cat {remote_path} 2>/dev/null || true")
                    (slurm_dir / f"{stem}.{ext}").write_text(body)
                    break
