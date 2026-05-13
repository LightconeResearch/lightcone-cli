# /lc-build

Build an ASTRA analysis from spec to materialized results. Plans
interactively, then loops autonomously via the ralph-wiggum stop hook
until all outputs are materialized or `--max-iterations` is reached.

Source: [`claude/lightcone/skills/lc-build/SKILL.md`](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/lc-build/SKILL.md).

Argument hint: `[DESCRIPTION] [--universe NAME] [--max-iterations N]`.
Defaults: universe `baseline`, max-iterations `25`.

## Allowed tools

```text
Read, Write, Edit, Glob, Grep,
Bash(astra:*), Bash(lc:*), Bash(python:*), Bash(git:*), Bash(pip:*), Bash(mkdir:*),
Bash(setup-lc-build:*),
Agent, AskUserQuestion
```

## Phases

### Phase 0 — Resume an interrupted loop

If `.claude/ralph-loop.local.md` exists, ask the user via
`AskUserQuestion` whether to resume or start fresh. Resume runs
`setup-lc-build.sh --resume`; fresh deletes the state file.

### Phase 1 — Plan (interactive)

1. **Validate prerequisites** via `setup-lc-build.sh --validate
   --universe <U> --max-iterations <N>`. Bails out with actionable
   error messages if `astra.yaml`, the universe file, or required
   tools are missing.
2. **Read context** — `astra.yaml`, `CLAUDE.md`,
   `.claude/guides/astra-reference.md`,
   `.claude/guides/lightcone-cli-reference.md`,
   `universes/<U>.yaml`, any existing `scripts/`.
3. **Produce a plan** at `.lightcone/plans/build-plan-<U>.md` with:
   analysis overview; dependency graph; decision selections; ordered
   build checklist with per-output script / decisions / dependencies /
   estimated cost; verification checklist.
4. **Get approval** via `AskUserQuestion`: "Approve and start building"
   vs "Let me edit the plan first."

**Rule:** Phase 1 is read-only exploration. No code, no spec edits
until the user approves.

### Phase 2 — Loop (autonomous)

Once approved, `setup-lc-build.sh --activate` writes
`.claude/ralph-loop.local.md`. The Claude Code stop hook intercepts
session exits and re-injects the loop prompt
([`assets/loop-prompt.md`](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/lc-build/assets/loop-prompt.md))
until the agent emits `<promise>BUILD_COMPLETE</promise>` or
max-iterations is hit.

Each iteration: survey state, decide what to do next, work, commit,
exit. The plan file persists across crashes for easy resumption and
is deleted on successful completion.

## State files

| File | Purpose |
|------|---------|
| `.lightcone/plans/build-plan-<universe>.md` | The user-approved plan. Persists across crashes. Deleted on completion. |
| `.claude/ralph-loop.local.md` | Loop state: iteration count, max iterations, session id, universe. Used by the session-start hook to detect interruptions. |

## Cancellation

Mid-loop: `/cancel-ralph` (provided by the ralph-loop plugin).

## Dependency on the ralph-loop plugin

The loop machinery (the stop hook, `/cancel-ralph`) ships in a
separate Claude Code plugin. `setup-lc-build.sh` will attempt to
install it on demand from the marketplace; if installation fails it
errors out and cleans up.

## Related

- [`/lc-verify`](lc-verify.md) — read-only audit, run after a successful build.
- [`claude/lightcone/guides/lightcone-cli-reference.md`](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/guides/lightcone-cli-reference.md) — CLI and execution reference loaded by the skill.
