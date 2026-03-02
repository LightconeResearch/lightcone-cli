---
name: prism-build
description: >
  Build an ASP analysis from spec to materialized results. Plans interactively,
  then loops autonomously via ralph-wiggum until all outputs are verified.
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(asp:*), Bash(prism:*), Bash(python:*), Bash(git:*), Bash(pip:*), Bash(mkdir:*), Bash(setup-prism-build:*), Agent, AskUserQuestion
argument-hint: "[--universe NAME] [--max-iterations N]"
---

# /prism-build

Two-phase build: plan interactively with the user, then loop autonomously until done.

## Phase 1: Setup & Plan

### 1. Validate prerequisites

Run the setup script in validate mode:

```
bash <skill-scripts>/setup-prism-build.sh --validate --universe <UNIVERSE> --max-iterations <N>
```

Default universe is `baseline`, default max-iterations is `25`. Parse these from the user's arguments.

If validation fails, fix issues before proceeding (create `asp.yaml` via `/prism-new`, fix validation errors, etc.).

### 2. Create implementation plan

Spawn a Plan sub-agent to produce an ordered implementation plan:

```
Agent tool, subagent_type: Plan
Prompt: "Read asp.yaml, CLAUDE.md, and any existing scripts/ directory. Produce an ordered implementation plan for building this analysis in universe <UNIVERSE>. For each output in asp.yaml, determine: what script needs to be written, what decisions it must parameterize, what its dependencies are, and what order to build them in. Write the plan to .claude/build-plan-<UNIVERSE>.md as a markdown checklist."
```

### 3. Present plan for approval

Read `.claude/build-plan-<UNIVERSE>.md` and present it to the user via `AskUserQuestion`:

- Show the plan contents
- Ask: "Does this build plan look good? You can approve it, request changes, or edit `.claude/build-plan-<UNIVERSE>.md` directly."
- Options: "Approve and start building", "Let me edit the plan first"

If the user wants changes, iterate until they approve.

## Phase 2: Activate Loop

Once the user approves the plan, activate the autonomous loop:

```
bash <skill-scripts>/setup-prism-build.sh --activate --universe <UNIVERSE> --max-iterations <N>
```

This creates `.claude/ralph-loop.local.md` — the ralph-wiggum state file. The stop hook will now intercept exits and re-inject the build prompt.

**Begin the first iteration immediately.** Read `.claude/ralph-loop.local.md` — the rendered loop prompt is everything after the YAML frontmatter. Follow it: survey, decide what to do, work, commit, exit. On exit, the stop hook re-invokes you with the same prompt for subsequent iterations until you output `<promise>BUILD_COMPLETE</promise>` or hit max iterations.

## References

- [Loop Prompt](./assets/loop-prompt.md) — the invariant prompt for each iteration
- [Prism Verify](../prism-verify/SKILL.md) — verification checks

## Notes

- The setup script will attempt to install the ralph-loop plugin if missing (via marketplace update). If installation fails, it errors and cleans up — the loop cannot run without the stop hook.
- The build plan file (`.claude/build-plan-<UNIVERSE>.md`) persists across crashes for easy resumption. It's deleted on successful completion.
- To cancel mid-loop: `/cancel-ralph`
