# lightcone.engine.site_registry

Known-site defaults. When lightcone-cli runs on a recognized site
(NERSC Perlmutter, a lightcone JupyterHub deployment), the matching
entry here supplies site-specific defaults — most importantly the
scratch root and the preferred container runtime.

Source: `src/lightcone/engine/site_registry.py`.

## What the module exposes

- `SITE_DEFAULTS` — a dict mapping site keys (`"perlmutter"`,
  `"jupyterhub"`, `"local"`) to a structured defaults dict (display
  name, hostname patterns or env markers, backend, container runtime,
  `scratch_root`, suggested QoS / constraint / time-limit options).
- `detect_current_site() → HostSite` — the high-level entry point.
  Single source of truth for "which site are we on?": environment
  markers win over hostname patterns (a pod's hostname is noise; the
  injected env is the signal). Returns a falsy `HostSite` when nothing
  matches.
- `HostSite` — frozen dataclass bundling the matched site key with its
  defaults; `site.get("scratch_root")` etc.
- Lower-level pieces: `detect_site(hostname_or_name)`,
  `detect_site_from_env()`, `get_site_defaults(site_key)`,
  `list_known_sites()`, `get_site_scratch_deny_rules(site_key)`.

## Who calls it

- `lc init` (`lightcone.cli.commands`) — detects the site to surface
  the resolved scratch root the run layer will use.
- `lightcone.engine.scratch` — `resolve_scratch_root()` falls back to
  the site's declared `scratch_root` (e.g. `$SCRATCH` on Perlmutter,
  `$HOME` on a JupyterHub pod) when the project config doesn't pin one.
- `lightcone.engine.container` — `auto` runtime resolution prefers the
  site's declared `container_runtime` (`podman-hpc` on Perlmutter,
  `kubernetes` on a hub).

Everything should go through `detect_current_site()` rather than
re-deriving `socket.gethostname() + detect_site + get_site_defaults`.

## Vestigial pieces

`get_site_scratch_deny_rules()` and `list_known_sites()` currently
have no callers — they are residue from the removed target system.
The `suggested_options` blocks (QoS/constraint/time-limit guidance)
are likewise declared but not consumed yet.

## Adding a site

Append an entry to `SITE_DEFAULTS`. HPC sites match by
`hostname_patterns`; deployment-style sites (pods with arbitrary
hostnames) match by `env_markers`. Declare `scratch_root` for any site
where the default tempdir is wrong — see the `jupyterhub` entry's
comment for why a shared filesystem matters there.
