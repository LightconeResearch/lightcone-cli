#!/bin/bash
# SessionEnd hook: stop the project's session-scoped Dask scheduler.
#
# Best-effort and silent. The scheduler self-shuts on idle-timeout
# (see lightcone.engine.dask_daemon) so failure to fire here only
# delays cleanup; it does not leak resources indefinitely.

input=$(cat)
cwd=$(echo "$input" | jq -r '.cwd // empty')

[ -z "$cwd" ] && exit 0
cd "$cwd" 2>/dev/null || exit 0
[ -f "astra.yaml" ] || exit 0
command -v lc &>/dev/null || exit 0

lc dask stop >/dev/null 2>&1 || true
exit 0
