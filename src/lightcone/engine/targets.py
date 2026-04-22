"""Target configuration management for Dagster execution backends."""
from __future__ import annotations

import warnings
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml


class TargetKind(StrEnum):
    """Execution backends a target config can declare.

    Aligned with `dagster-slurm`'s `ComputeResource` modes (ADR-0001 §4.4),
    plus `docker` for lightcone-cli's hermetic local runs.
    """

    DOCKER = "docker"
    LOCAL = "local"
    SLURM = "slurm"
    SLURM_SESSION = "slurm-session"


def detect_target_shape(cfg: dict[str, Any]) -> str:
    """Return "new" if the config uses the post-ADR-0001 ``mode:`` schema,
    "legacy" if it uses the pre-ADR-0001 ``backend:`` schema.
    """
    if "mode" in cfg:
        return "new"
    if "backend" in cfg:
        return "legacy"
    raise ValueError(
        "Target config has neither 'mode' nor 'backend' key — cannot determine shape."
    )


def normalize_target(cfg: dict[str, Any]) -> dict[str, Any]:
    """Return ``cfg`` in the post-ADR-0001 target schema (§4.4).

    Accepts both shapes. Emits ``DeprecationWarning`` on the legacy shape and
    translates it to the new one in-memory; the file on disk is not touched
    (use ``lc target migrate <name>`` for that).
    """
    shape = detect_target_shape(cfg)
    if shape == "new":
        return cfg
    warnings.warn(
        "Target YAML uses the pre-ADR-0001 'backend:' schema. "
        "Run 'lc target migrate <name>' to convert to the new 'mode:' schema. "
        "The legacy shape will be removed after Phase E.",
        DeprecationWarning,
        stacklevel=2,
    )
    return _legacy_to_new(cfg)


def _legacy_to_new(cfg: dict[str, Any]) -> dict[str, Any]:
    """Translate a legacy ``backend:``-shaped target dict to the new schema."""
    scheduler = cfg.get("scheduler") or {}
    connection = cfg.get("connection") or {}
    poll = cfg.get("poll") or {}

    # Some wizard-produced configs stash scheduler fields flat at the top level
    # rather than under `scheduler:` — accept both.
    def s(key: str) -> Any:
        return scheduler.get(key, cfg.get(key))

    out: dict[str, Any] = {
        "name": cfg.get("name") or cfg.get("site"),
        "mode": cfg.get("backend", "docker"),
    }
    if "site" in cfg:
        out["site"] = cfg["site"]

    ssh: dict[str, Any] = {}
    if connection.get("hostname"):
        ssh["host"] = connection["hostname"]
    for src_key, dst_key in (("user", "user"), ("key_path", "key_path"), ("port", "port")):
        if connection.get(src_key):
            ssh[dst_key] = connection[src_key]
    if ssh:
        out["ssh"] = ssh

    queue: dict[str, Any] = {}
    for src_key, dst_key in (
        ("partition", "partition"),
        ("account", "account"),
        ("qos", "qos"),
        ("time_limit", "time_limit"),
        ("cpus", "cpus"),
        ("mem_per_cpu", "mem_per_cpu"),
        ("gpus_per_node", "gpus_per_node"),
        ("nodes", "nodes"),
    ):
        val = s(src_key)
        if val is not None:
            queue[dst_key] = val
    if queue:
        out["queue"] = queue

    container: dict[str, Any] = {}
    if s("container_runtime"):
        container["runtime"] = s("container_runtime")
    if s("container_flags"):
        container["flags"] = s("container_flags")
    if container:
        out["container"] = container

    extra: list[str] = []
    constraint = s("constraint")
    if constraint:
        extra.append(f"--constraint={constraint}")
    extra_args = s("extra_slurm_args") or []
    extra.extend(extra_args)
    if extra:
        out["extra_sbatch_directives"] = extra

    if poll.get("timeout_seconds") is not None:
        out["poll"] = {"timeout_seconds": poll["timeout_seconds"]}

    remote_base = cfg.get("remote_base")
    if remote_base:
        out["remote_base"] = remote_base

    return out


def get_targets_dir() -> Path:
    """Return the user-level targets directory (~/.lightcone/targets/)."""
    return Path.home() / ".lightcone" / "targets"


def list_targets() -> list[str]:
    """Return names of saved target configurations."""
    targets_dir = get_targets_dir()
    if not targets_dir.exists():
        return []
    return sorted(p.stem for p in targets_dir.glob("*.yaml"))


def load_target(name: str) -> dict[str, Any] | None:
    """Load a saved target configuration by name. Returns None if missing."""
    config_path = get_targets_dir() / f"{name}.yaml"
    if not config_path.exists():
        return None
    with open(config_path) as f:
        return yaml.safe_load(f)


def save_target(name: str, config: dict[str, Any]) -> Path:
    """Save a target configuration to ~/.lightcone/targets/{name}.yaml."""
    targets_dir = get_targets_dir()
    targets_dir.mkdir(parents=True, exist_ok=True)
    config_path = targets_dir / f"{name}.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    return config_path


def get_config_path() -> Path:
    """Return the user-level config file path (~/.lightcone/config.yaml)."""
    return Path.home() / ".lightcone" / "config.yaml"


def load_user_config() -> dict[str, Any]:
    """Load the user-level lightcone-cli configuration.

    Returns an empty dict if the config file doesn't exist.
    """
    config_path = get_config_path()
    if not config_path.exists():
        return {}
    with open(config_path) as f:
        return yaml.safe_load(f) or {}


def save_user_config(config: dict[str, Any]) -> Path:
    """Save user-level lightcone-cli configuration to ~/.lightcone/config.yaml.

    Returns the path where it was saved.
    """
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    return config_path
