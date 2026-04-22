---
name: lc-build
description: >
  Build an ASTRA analysis from spec to materialized results. Plans interactively,
  then loops autonomously via ralph-wiggum until all outputs are verified.
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(astra:*), Bash(lc:*), Bash(python:*), Bash(git:*), Bash(pip:*), Bash(mkdir:*), Bash(setup-lc-build:*), Agent, AskUserQuestion
argument-hint: "[DESCRIPTION] [--universe NAME] [--max-iterations N]"
---

# /lc-build

Two-phase build: plan interactively with the user, then loop autonomously until done.

**Do NOT write or modify any code (scripts, astra.yaml, etc.) until the user approves the plan in Phase 1.** Phase 1 is read-only exploration and planning. Code changes only happen in Phase 2 (the loop).

## Phase 0: Check for Interrupted Loop

Before anything else, check if a previous loop was interrupted:

```
if .claude/ralph-loop.local.md exists:
```

If it exists, ask the user via `AskUserQuestion`:
- "A previous lc-build loop was interrupted. Would you like to resume where it left off or start fresh?"
- Options: "Resume the loop", "Start fresh (discard previous state)"

**If resume:** Run the setup script in resume mode to claim the loop for this session:
```
bash .claude/skills/lc-build/scripts/setup-lc-build.sh --resume
```
Then jump straight to reading `.claude/ralph-loop.local.md` and following the loop prompt.

**If start fresh:** Delete the old state file (`rm .claude/ralph-loop.local.md`) and continue to Phase 1.

## Phase 1: Setup & Plan

### 1. Validate prerequisites

Run the setup script in validate mode:

```
bash .claude/skills/lc-build/scripts/setup-lc-build.sh --validate --universe <UNIVERSE> --max-iterations <N>
```

Default universe is `baseline`, default max-iterations is `25`. Parse these from the user's arguments.

If validation fails, fix issues before proceeding (create `astra.yaml` via `/lc-new`, fix validation errors, etc.).

### 2. Create implementation plan

Read `astra.yaml`, `CLAUDE.md`, `.claude/guides/astra-reference.md`, `.claude/guides/lightcone-cli-reference.md`, `universes/<UNIVERSE>.yaml`, and any existing `scripts/` directory. If the user provided a description (e.g. `/lc-build focus on the fitting script first`), use it to guide the plan's priorities and ordering. Produce an ordered implementation plan and write it to `.lightcone/plans/build-plan-<UNIVERSE>.md`.

The plan must include:

1. **Analysis overview** — project name, universe, input data, container, execution target
2. **Dependency graph** — which outputs depend on which
3. **Decision selections** — table of decisions and their selected values for this universe
4. **Ordered build checklist** — for each output: script, decisions, dependencies, estimated cost
5. **Verification checklist** — spec validation, decision-code alignment

### 3. Present plan for approval

Print the plan contents and ask the user via `AskUserQuestion`:

- "Does this build plan look good?"
- Options: "Approve and start building", "Let me edit the plan first"

If the user wants changes, wait for them to edit `.lightcone/plans/build-plan-<UNIVERSE>.md` and re-present.

## Phase 2: Activate Loop

Once the user approves the plan, activate the autonomous loop:

```
bash .claude/skills/lc-build/scripts/setup-lc-build.sh --activate --universe <UNIVERSE> --max-iterations <N>
```

This creates `.claude/ralph-loop.local.md` — the ralph-wiggum state file. The stop hook will now intercept exits and re-inject the build prompt.

**Begin the first iteration immediately.** Read `.claude/ralph-loop.local.md` — the rendered loop prompt is everything after the YAML frontmatter. Follow it: survey, decide what to do, work, commit, exit. On exit, the stop hook re-invokes you with the same prompt for subsequent iterations until you output `<promise>BUILD_COMPLETE</promise>` or hit max iterations.

## References

- [Loop Prompt](./assets/loop-prompt.md) — the invariant prompt for each iteration
- [lightcone-cli Verify](../lc-verify/SKILL.md) — verification checks

## Notes

- The setup script will attempt to install the ralph-loop plugin if missing (via marketplace update). If installation fails, it errors and cleans up — the loop cannot run without the stop hook.
- The build plan file (`.lightcone/plans/build-plan-<UNIVERSE>.md`) persists across crashes for easy resumption. It's deleted on successful completion.
- To cancel mid-loop: `/cancel-ralph`
