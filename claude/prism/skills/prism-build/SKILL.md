---
name: prism-build
description: >
  Autonomous build loop for ASTRA analyses. You are inside a loop -- survey,
  contribute, commit, exit. Activated automatically inside prism-build loops.
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(astra:*), Bash(prism:*), Bash(python:*), Bash(git:*), Bash(pip:*), Bash(mkdir:*)
---

# /prism-build

You are inside a loop. `CLAUDE.md` is your spec -- it's in the system prompt above. Each iteration: survey freely, work substantially, update state discoverably, exit.

## References

- [Prism Verify](./../prism-verify/SKILL.md) -- verification checks

## Loop

1. **Survey** -- Fresh eyes. Read `astra.yaml`, check `git log`, explore. You decide what to check.
2. **Contribute** -- Work on 1-3 substantial pieces. Do NOT try to clear the whole queue in one iteration.
3. **Update** -- Commit your work. Update `CLAUDE.md` if warranted.
4. **Exit** -- Stop. The outer loop re-invokes you with fresh context.

## Rules

**Exit before compaction.** After each substantial piece of work, introspect: how much context have I used? If past 50%, wrap up and exit. The next iteration starts fresh.

**State, not checklist.** The spec describes what "done" looks like. Survey reality, decide what's highest value, work on that.

**Discoverable updates.** Commits, test results, documentation -- not progress files. The next iteration finds what changed by inspecting the system.

**You have authority.** Trust the spec, don't ask permission. Make substantial contributions.

## Closing

If you cannot find remaining work, check success criteria from `astra.yaml`. Each criterion has a `claim` and optionally `output` and `condition`. For criteria with an output and condition (e.g. `output: accuracy`, `condition: "value > 0.95"`), read the result file and evaluate. For claim-only criteria, use your judgment. If all are met, set `build: closed` in the `CLAUDE.md` YAML frontmatter.

If you're stuck, add to the Open Questions section of `CLAUDE.md` so the user can resolve it after the loop.
