"""Known site defaults.

When ``lc setup`` detects a known site, it pre-populates a target with
orthogonal ``options`` (qos, constraint, ...) and scheduler-neutral
guidance drawn from the entries below.  Users override any default
during the wizard.

To add a new site, append an entry to :data:`SITE_DEFAULTS`.
"""
from __future__ import annotations

from typing import Any

#: Per-site defaults.  ``suggested_options`` follows the same shape as the
#: target file's ``options`` section: an orthogonal map of axis →
#: ``{default, choices}`` (where ``choices`` is ``{value: guidance}``).
#: ``cache_key_overrides`` captures non-conventional sacctmgr naming (e.g.
#: Perlmutter's ``regular_1`` for the CPU ``regular`` queue).
SITE_DEFAULTS: dict[str, dict[str, Any]] = {
    "perlmutter": {
        "hostname_patterns": ["perlmutter", "saul"],
        "display_name": "NERSC Perlmutter",
        "backend": "slurm",
        "connection": {
            "hostname": "perlmutter.nersc.gov",
        },
        "container_runtime": "podman-hpc",
        "suggested_options": {
            "qos": {
                "default": "debug",
                "choices": {
                    "debug":   "quick iteration, testing",
                    "regular": "production runs, large jobs",
                    "preempt": "cheap batch, restartable after 2h",
                    "shared":  "fractional node (1–2 GPUs)",
                },
            },
            "constraint": {
                "default": "gpu",
                "choices": {
                    "gpu":          "A100 40 GB — 1,536 nodes, 4 GPUs/node",
                    "cpu":          "CPU only — 3,072 nodes, 128 cores/node",
                    "gpu&hbm80g":   "A100 80 GB — 256 nodes",
                },
            },
            "time_limit": {
                "default": "30m",
                "guidance": "debug caps at 30 min; regular allows up to 48 h",
            },
        },
        # Perlmutter's sacctmgr names prefix GPU QoS with `gpu_` and
        # suffix the CPU regular queue as `regular_1`.  The first is
        # handled by the default `{constraint}_{qos}` convention; the
        # second needs an explicit override.
        "cache_key_overrides": {
            "regular/cpu": "regular_1",
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
    """Detect a known site from a hostname or site name."""
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
    """Return ``(site_key, display_name)`` for all known sites."""
    return [
        (key, site.get("display_name", key))
        for key, site in SITE_DEFAULTS.items()
    ]


def get_site_scratch_deny_rules(site_key: str) -> list[str]:
    """Return Edit deny rules for a site's scratch/shared filesystems."""
    site = SITE_DEFAULTS.get(site_key)
    if not site:
        return []
    scratch_paths = site.get("scratch_paths", [])
    return [f"Edit({path})" for path in scratch_paths]
