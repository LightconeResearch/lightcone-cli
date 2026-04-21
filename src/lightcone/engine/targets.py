"""Target configuration management for Dagster execution backends."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


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
