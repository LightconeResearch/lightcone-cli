---
name: ralph-loops
description: >
  Autonomous loop iteration toward a desired state. You are inside a ralph
  loop — your constitution is in the system prompt. Survey, contribute,
  update state discoverably, exit. Activated automatically inside ralph
  loops, or when launching one against an existing constitution via
  scripts/ralph; for drafting the constitution itself, use /constitution.
  Triggers: "ralph-loops", "launch ralph", "run ralph", "ralph loop on <constitution>".
---

# Ralph Loops

The autonomous iteration loop a constitution dispatches against. The skill has two entry points, and only one applies at a time:

- **Launching a loop** — outside any active loop, invoke the bundled launcher script to start an iteration sequence on a constitution file. See **Launching** below.
- **Inside a loop** — the constitution is in the system prompt above; follow the **Loop** protocol. Ignore the Launching section; a loop is already running.

## Launching

The launcher is a shell script bundled with this skill. Its runtime path inside a project (after `lc init` copies the bundle) is:

```
.claude/skills/ralph-loops/scripts/ralph
```

Usage:

```
.claude/skills/ralph-loops/scripts/ralph <constitution.md> [--backend claude|codex] [-- extra-flags...]
```

- `<constitution.md>` is the constitution file. Its YAML frontmatter must carry `status: open` or `status: active`; the launcher refuses to start otherwise. The loop terminates automatically when an iteration flips `status:` to `closed` after a cold survey.
- The launcher starts a detached tmux session and returns immediately. Attach with `tmux attach -t <session>`; the printed session name is `ralph-<dirname>-<basename>`.
- A second launch with the same constitution detects the existing tmux session and prints the attach command instead of double-starting.

### Backends

- `claude` (default) — each iteration runs `claude --dangerously-skip-permissions --append-system-prompt <constitution>` with the constitution injected as the system prompt.
- `codex` — runs `codex --dangerously-bypass-approvals-and-sandbox --config developer_instructions=<constitution>`.

Set via `--backend codex` or `RALPH_BACKEND=codex`.

### Extra flags

Anything after a literal `--` separator forwards to the backend unchanged. Common flags for the Claude backend:

- `--chrome` — enable the Claude-in-Chrome integration for iterations that need live browser access.
- `--model <id>` — override the backend model.

### Examples

```bash
# Launch on a per-paper reproduction constitution
.claude/skills/ralph-loops/scripts/ralph constitution.md

# Codex backend
.claude/skills/ralph-loops/scripts/ralph constitution.md --backend codex

# Claude backend with Chrome integration and a model override
.claude/skills/ralph-loops/scripts/ralph constitution.md -- --chrome --model claude-opus-4-6
```

## Loop

1. **Survey** — Fresh eyes. Read the constitution and the workdir's `CLAUDE.md`. Check `git log`, glance at sub-fibers or notes the prior iteration left, look at what's actually in the workdir. You decide what to check.
2. **Work** — Stay and work from the vantage point the survey built. Make 1–3 substantial contributions; don't try to clear the whole queue in one iteration.
3. **Update** — Before exiting: commit your work, update `CLAUDE.md`'s accumulators (Rigor *Current state*, Paper-vs-code disagreements, open opportunities) if anything sharpened, sharpen the constitution body if a fact stable enough to belong in *Context* or *Desired State* landed.
4. **Exit** — `kill $PPID`.

### Earn the vantage point

The survey is a fixed cost; exploit the warm world-model rather than rebuilding it next iteration. Exit when the next valuable move needs a different mental workspace — not when one task ends. If changes so far have been small and runway is plentiful, expand the workspace rather than exit.

**Exit before context is half-full.** Don't wait for "filling" to feel pressing — the right moment is the next sub-task boundary after you cross half. Write the handoff (commits, `CLAUDE.md` accumulators, constitution sharpening) from full attention and exit; don't try to cram one more thing in. The marginal step you'd squeeze in costs the next iteration more than it saves you, because it pays for the degraded handoff.

## Rules

**State, not checklist.** The constitution describes what "done" looks like. Survey reality, decide what's highest value, work on that.

**Discoverable updates.** Commits, files in the workdir, `CLAUDE.md` accumulators — not progress notes scattered in the body. The next iteration finds what changed by inspecting the system.

**Pointers, not snapshots.** If you learn something stable, update the constitution's *Context* or *Desired State*. Don't leave drive-by notes in the body.

**You have authority.** Trust the constitution. Don't ask permission. Make substantial contributions. Don't avoid ambitious solutions just because they span multiple iterations — the loop continues, tweaks on the next iter are cheap.

**File uncertain decisions** somewhere the user will see them. The convention varies by project: an `open-questions.md` file the constitution points at, an `Open Questions` section in the constitution itself, a `-t question` felt fiber when felt is in use. Don't sediment them in invisible places.

### Long-running jobs

If an iteration kicks off computation (snakemake, cluster jobs, container builds, dev servers), use the `Monitor` tool to stream events from the background process — each stdout line surfaces as a notification, so you'll get pinged when something happens without polling-with-sleep. For one-shot "wait until done," use Bash with `run_in_background` and you'll be notified on completion. Either way, shepherd computation to completion before exiting. Don't fire-and-forget.

## Exit

Closing the constitution (`status: closed` in frontmatter) stops the loop — no further iterations will run. So the closing decision is reserved for a cold survey that finds nothing left to do.

**If you made any changes this iteration, you may not close the constitution.** Commit, update the workdir, `kill $PPID` — let the next iteration survey with fresh eyes and decide whether to close. This is the only hard rule on exit.

Making changes does NOT mean you should exit early. Keep working while the context is warm — make as many changes as belong in this iteration. The rule only constrains *closing the constitution*, not the length of the iteration. See **Earn the vantage point** above for when to actually exit.

- **Made changes this iteration** → `kill $PPID` when the warm context is spent. Do not close the constitution.
- **Survey found zero remaining work AND you made zero changes** → flip the constitution's frontmatter `status:` to `closed`, append a closing line to the body or to a sibling summary file recording what landed, then `kill $PPID`. The launcher's next check fails and the loop terminates.

---

Pattern adapted from [Ralph Wiggum](https://ghuntley.com/ralph/).
