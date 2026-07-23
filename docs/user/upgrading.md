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

## The project-scoped `astra`

The `astra` command is not global. `lc init` installs astra-tools into the
project's `.venv`, and the plugin's activation hook prepends `.venv/bin` to
PATH so `astra` resolves inside the project. Each project therefore pins its own
astra-tools version in its venv. To move a project to a newer astra-tools,
upgrade it inside that venv (for example
`uv pip install --python .venv/bin/python -U astra-tools`).

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
- The version scheme itself: one lockstep version for both artifacts,
  independent versions with a declared compatibility floor, or an exact
  marketplace ref pinned at `lc init` time. See the PR discussion.
