# The upgrade model

`lightcone-cli` ships in two parts. They upgrade on their own schedules.
This page explains how they stay compatible and what to do when they drift.

## Two artifacts, two versions

The toolchain has two moving parts. Each travels on its own channel.

- **The `lc` CLI** ships through `uv`. Run `uv tool upgrade lightcone-cli`
  to get a newer CLI.
- **The `lightcone` plugin** ships through the agent-skills marketplace. The
  plugin carries the skills, the hooks, and the `lc-extractor` subagent. Your
  harness refreshes it on its own.

The two versions move independently. A skill edit ships from the marketplace
without a CLI release. A CLI change that alters the plugin contract requires a
plugin version bump. Neither channel assumes the other has moved.

## The global `astra`

The `astra` command is global, alongside `lc`. Both ship from the one
`lightcone-cli` wheel (`astra-tools` is a dependency), so `uv tool upgrade
lightcone-cli` moves them together — neither one lives in the project venv,
so there's no per-project copy to keep in sync. `lc init` does create a
project `.venv`, but leaves it empty for analysis dependencies, not `lc` or
`astra`.

When a single project needs a spec-exact `astra` — pinned to the astra-tools and
astra-spec versions its `astra.yaml` was authored against — invoke it per-call
rather than freezing a venv: `uvx --from astra-tools==X --with astra-spec==Y
astra`. This gives an exact pin at the moment of use instead of a stale one
frozen at init time.

## Compatibility is carried by versioning

The two channels will sometimes drift. The version numbers carry the contract:
when a CLI change alters what the plugin depends on, the plugin version bumps
with it. There is no runtime handshake — nothing probes the other side
mid-session. Compatibility is a property of which versions you have, not a check
that runs.

## Open questions

This model is not settled. A few points are still open.

- Whether the first marketplace release counts as breaking.
- How projects created before the plugin existed migrate onto it. That path is
  deferred to a later PR.
- The version scheme itself: one lockstep version for both artifacts, or
  independent versions with a declared compatibility floor. Per-project version
  pinning is not an option — the plugin installs globally with the harness, so a
  single project cannot pin its own plugin version. See the PR discussion.
