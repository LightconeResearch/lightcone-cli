# lightcone.engine.site_registry

> **Status: orphaned.** Nothing in the active code path imports this
> module. It's residue from the (now-removed) target system. Keep
> reading if you want to know what's still there; otherwise skip to
> [api/container](container.md) and [api/dask_cluster](dask_cluster.md)
> for what actually drives execution today.

Source: `src/lightcone/engine/site_registry.py`.

## What's still in the file

The module exposes:

- `SITE_DEFAULTS` — a dict mapping site keys (`"perlmutter"`, `"local"`)
  to a structured defaults dict (display name, hostname patterns,
  suggested QoS / constraint / time-limit options, scratch deny paths,
  container runtime).
- `detect_site(hostname_or_name) → str | None`
- `get_site_defaults(site_key) → dict | None`
- `list_known_sites() → list[tuple[str, str]]`
- `get_site_scratch_deny_rules(site_key) → list[str]`

The functions still work on their own; they just don't have a caller
inside lightcone-cli right now.

## What's actually used today

The Perlmutter scratch deny rules used to be merged into
`.claude/settings.json` automatically when a non-local target was
configured. That is gone. `lc init` now writes no permission policy at all —
permissions belong to the harness. The
[Troubleshooting](../user/troubleshooting.md#recommended-permissions-for-cluster-work)
page offers a copy-paste scratch ruleset that users add themselves.

If you want richer per-site rules, `lc init` could read this registry and print
a suggested ruleset for the detected site. That would keep the CLI advisory —
it suggests, the user decides — instead of imposing a policy.

## Recommendation

Either delete this module (no callers) or revive it for the advisory use case
above. Leaving it as-is encourages the drift this audit is fighting.
