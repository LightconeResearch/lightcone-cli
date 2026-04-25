"""Known site defaults.

When ``lc cluster add`` detects a known site, it pre-populates a cluster YAML
from the entry below.  Users override any default during the wizard or by
editing the cluster file later.

Each substrate that runs at a site has its own block (``slurm:`` today,
future ``k8s:``).  When a substrate is added, the matching block defines
the per-substrate defaults — the existing blocks don't change.

To add a new site, append an entry to :data:`SITE_DEFAULTS`.
"""
from __future__ import annotations

from typing import Any

#: Per-site defaults.  Each entry carries ``container_runtime`` (the CLI
#: invoked on compute nodes), per-substrate default blocks (e.g. ``slurm``
#: with ``scratch_root``/``default_qos``/``default_walltime``/
#: ``worker_init_template``), ``cache_key_overrides`` capturing non-
#: conventional sacctmgr naming (e.g. Perlmutter's ``regular_1`` for the
#: CPU ``regular`` queue), and ``scratch_paths`` used to seed Claude Code
#: edit-deny rules in ``lc init``.
SITE_DEFAULTS: dict[str, dict[str, Any]] = {
    "perlmutter": {
        "hostname_patterns": ["perlmutter", "saul"],
        "display_name": "NERSC Perlmutter",
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
                "default": "cpu",
                "choices": {
                    "cpu":        "CPU only — 3,072 nodes, 128 cores/node",
                    "gpu":        "A100 40 GB — 1,536 nodes, 4 GPUs/node",
                    "gpu&hbm80g": "A100 80 GB — 256 nodes",
                },
            },
        },
        # SLURM cluster defaults — fields the user almost never overrides.
        "slurm": {
            "scratch_root": "$PSCRATCH",
            "default_qos": "regular",
            "default_walltime": "24h",
            "worker_init_template": (
                "module load python\n"
                "source $HOME/.lightcone/envs/perlmutter/bin/activate\n"
            ),
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
}


def detect_site(hostname_or_name: str) -> str | None:
    """Detect a known site from a hostname or site name."""
    normalized = hostname_or_name.lower()
    for site_key, site in SITE_DEFAULTS.items():
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
