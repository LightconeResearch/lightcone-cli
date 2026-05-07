"""Known site defaults.

When ``lc init`` runs on a known site, the matching entry below provides
the scratch root surfaced to the user and any deny rules used to keep
edits off shared filesystems.

To add a new site, append an entry to :data:`SITE_DEFAULTS`.

The high-level entry point for the rest of the codebase is
:func:`detect_current_site`, which returns a :class:`HostSite` bundling
the matched site key with its declared defaults — keeping the
``socket.gethostname() + detect_site + get_site_defaults`` chain in one
place.
"""
from __future__ import annotations

import socket
from collections.abc import Mapping
from dataclasses import dataclass, field
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
        # Where lightcone keeps its operational state (snakemake metadata,
        # dask spill, cross-node stdout locks). NERSC's $HOME and CFS are
        # mounted on compute via DVS, which silently swallows ``flock`` and
        # is slow for small-file I/O — Lustre ($SCRATCH) is the only sane
        # choice. Stored as a shell expression so it expands to each user's
        # private scratch path at run time.
        "scratch_root": "$SCRATCH",
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


@dataclass(frozen=True)
class HostSite:
    """The site (if any) the local host belongs to.

    Returned by :func:`detect_current_site`. Use ``if site:`` to test
    whether a known site was matched; use :meth:`get` (or
    :attr:`defaults`) to read declared fields.

    Adding a new "site asks for X" feature should not require a fourth
    copy of the ``detect_site(socket.gethostname()) → get_site_defaults``
    boilerplate — extend this class (or its consumers) instead.
    """

    key: str | None
    defaults: Mapping[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.key is not None

    @property
    def display_name(self) -> str:
        return self.defaults.get("display_name") or self.key or "unknown"

    def get(self, name: str, default: Any = None) -> Any:
        """Look up a field declared in the site's defaults."""
        return self.defaults.get(name, default)


_UNKNOWN_HOST_SITE = HostSite(key=None, defaults={})


def detect_current_site() -> HostSite:
    """Return the :class:`HostSite` for the local host.

    Single source of truth for "which site are we on?" — everything else
    in the codebase should call this rather than re-deriving it from
    :func:`socket.gethostname` and :func:`detect_site`. Returns a falsy
    :class:`HostSite` (``key is None``) when the hostname matches no
    known site.
    """
    key = detect_site(socket.gethostname())
    if key is None:
        return _UNKNOWN_HOST_SITE
    return HostSite(key=key, defaults=get_site_defaults(key) or {})
