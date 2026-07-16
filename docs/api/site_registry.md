# lightcone.engine.site_registry

Site detection and deterministic per-site execution defaults. The active
container, scratch, and asynchronous SLURM paths all consume this registry.

Source: `src/lightcone/engine/site_registry.py`.

## Public data and helpers

The module exposes:

- `SITE_DEFAULTS` — a dict mapping site keys (`"perlmutter"`, `"local"`)
  to a structured defaults dict (display name, hostname patterns,
  suggested QoS / constraint / time-limit options, scratch paths, container
  runtime, and `async_slurm` policies).
- `detect_site(hostname_or_name) → str | None`
- `get_site_defaults(site_key) → dict | None`
- `list_known_sites() → list[tuple[str, str]]`
- `get_site_scratch_deny_rules(site_key) → list[str]`

The functions still work on their own; they just don't have a caller
inside lightcone-cli right now.

## Async SLURM policy

Perlmutter declares CPU and GPU node shapes, fractional shared limits, and
the 48-hour `shared`/`regular` caps under `async_slurm`. The submission
engine selects the CPU profile when no GPU is declared and the GPU profile
otherwise. Adding another site is a table extension rather than a new branch
in the renderer.
