"""Known HPC site defaults for site configuration.

When ``prism setup`` detects a known site, it auto-populates scheduler
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
        "container_runtime": "podman-hpc",
        # Hardware descriptions for the agent (can't be discovered from SLURM)
        "constraint_guidance": {
            "gpu": "A100 40 GB — 1,536 nodes, 4 GPUs/node",
            "gpu&hbm80g": "A100 80 GB — 256 nodes, 4 GPUs/node",
            "cpu": "CPU only — 3,072 nodes, 128 cores/node",
        },
        # Suggested QoS for setup wizard (pre-filled use_for descriptions).
        # QoS names are the base names used for submission — the constraint
        # determines GPU vs CPU, not a name prefix.  Same QoS name can
        # appear with different constraints.
        "suggested_qos": [
            {"name": "debug", "constraint": "gpu",
             "use_for": "quick GPU iteration, testing"},
            {"name": "regular", "constraint": "gpu",
             "use_for": "production GPU runs, large jobs"},
            {"name": "preempt", "constraint": "gpu",
             "use_for": "cheap GPU batch jobs, restartable"},
            {"name": "debug", "constraint": "cpu",
             "use_for": "quick CPU-only tests"},
            {"name": "regular", "constraint": "cpu",
             "use_for": "large CPU-only jobs"},
        ],
        "safe_defaults": {
            "qos": "debug",
            "constraint": "gpu",
            "nodes": 1,
            "time_limit": "00:30:00",
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
