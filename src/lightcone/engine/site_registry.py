"""Known HPC site defaults for site configuration.

When ``lc setup`` detects a known site, it auto-populates scheduler
settings with site-specific defaults (node types, QOS options, container
runtimes, etc.).  Users can override any value during the wizard.

To add a new site, append an entry to ``SITE_DEFAULTS``.
"""
from __future__ import annotations

from typing import Any

# Each entry maps a site key to its defaults.  The ``hostname_patterns``
# list is used to auto-detect the site from user-provided hostnames.
SITE_DEFAULTS: dict[str, dict[str, Any]] = {
    "perlmutter": {
        "hostname_patterns": ["perlmutter", "saul"],
        "display_name": "NERSC Perlmutter",
        "backend": "slurm",
        "connection": {
            "hostname": "perlmutter.nersc.gov",
        },
        "scheduler": {
            "container_runtime": "podman-hpc",
        },
        "node_types": {
            "gpu": {
                "description": "GPU (A100 40GB) — 1,536 nodes, 4 GPUs/node",
                "constraint": "gpu",
                "container_flags": ["--gpu"],
            },
            "gpu_hbm80": {
                "description": "GPU (A100 80GB) — 256 nodes, 4 GPUs/node",
                "constraint": "gpu&hbm80g",
                "container_flags": ["--gpu"],
            },
            "cpu": {
                "description": "CPU only — 3,072 nodes, 128 cores/node",
                "constraint": "cpu",
                "container_flags": [],
            },
        },
        "qos_options": {
            "regular": {"description": "Standard priority, max 48h", "default": True},
            "debug": {"description": "Quick tests, max 30min, 8 nodes max"},
            "shared": {"description": "Fractional GPU (1-2 GPUs), max 48h"},
            "preempt": {"description": "0.25x cost, can be preempted after 2h"},
        },
        "container_runtimes": ["podman-hpc"],
        "resource_limits": {
            "max_nodes": 4,
            "max_walltime_minutes": 360,
            "max_concurrent_jobs": 8,
        },
        "safe_defaults": {
            "node_type": "gpu",
            "constraint": "gpu",
            "qos": "debug",
            "nodes": 1,
            "time_limit": "30m",
        },
        "account_suffixes": {
            "gpu": "_g",
            "gpu&hbm80g": "_g",
        },
        "scratch_paths": [
            "//pscratch/**",
            "//global/cscratch1/**",
            "//global/cfs/cdirs/**",
        ],
    },
    "local": {
        "hostname_patterns": [],
        "display_name": "Local",
        "backend": "local",
        "connection": {},
        "scheduler": {},
        "node_types": {},
        "qos_options": {},
        "container_runtimes": [],
        "resource_limits": {},
    },
}


def detect_site(hostname_or_name: str) -> str | None:
    """Detect a known HPC site from a hostname or site name.

    Returns the site key (e.g. ``"perlmutter"``) or ``None`` if no match.
    """
    normalized = hostname_or_name.lower()
    for site_key, site in SITE_DEFAULTS.items():
        if site.get("backend") == "local":
            continue
        if site_key in normalized:
            return site_key
        for pattern in site.get("hostname_patterns", []):
            if pattern in normalized:
                return site_key
    return None


def get_site_defaults(site_key: str) -> dict[str, Any] | None:
    """Return defaults for a known site, or ``None``."""
    return SITE_DEFAULTS.get(site_key)


def list_known_sites() -> list[tuple[str, str]]:
    """Return list of (site_key, display_name) for all known sites."""
    return [
        (key, site.get("display_name", key))
        for key, site in SITE_DEFAULTS.items()
    ]


def resolve_account(site_key: str, account: str, constraint: str | None) -> str:
    """Apply site-specific account suffix based on constraint.

    For example, on Perlmutter GPU jobs require ``m4031_g`` instead of
    ``m4031``.  If the account already has the suffix, it is not added again.
    """
    site = SITE_DEFAULTS.get(site_key)
    if not site or not constraint:
        return account
    suffixes = site.get("account_suffixes", {})
    suffix = suffixes.get(constraint)
    if suffix and not account.endswith(suffix):
        return account + suffix
    return account


def get_site_scratch_deny_rules(site_key: str) -> list[str]:
    """Return Edit deny rules for a site's scratch/shared filesystem paths.

    These are used in Claude Code permissions to prevent accidental writes
    to shared HPC filesystems.
    """
    site = SITE_DEFAULTS.get(site_key)
    if not site:
        return []
    scratch_paths = site.get("scratch_paths", [])
    return [f"Edit({path})" for path in scratch_paths]
