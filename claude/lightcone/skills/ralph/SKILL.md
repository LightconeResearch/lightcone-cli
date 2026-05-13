---
name: ralph
description: >
  Author a constitution — a markdown document describing a desired state for
  autonomous iteration — and run a ralph loop against it. The skill covers
  three modes: drafting a constitution (Study → Draft → Refine → Launch),
  launching a loop via the bundled tmux runner, and executing a single
  iteration from inside an active loop (survey → work → update → exit).
  Use for any work where adaptation matters more than a fixed plan: science,
  refactoring, exploration, long-running reproductions.
  Triggers: "ralph", "ralph loop", "constitution", "constitute", "draft a
  constitution", "launch ralph", "run ralph on <constitution>", "set up a
  ralph loop".
---

# Ralph

Long-running iteration toward a desired state. The substrate is a **constitution** — a markdown file describing what "done" looks like. The runner is a **ralph loop** — a tmux session that spawns a fresh worker per iteration with the constitution as system prompt.

Three modes; one applies at a time:

- **Authoring** — drafting a constitution from scratch. See **Authoring** below.
- **Launching** — outside any active loop, invoking the bundled script to start one on an existing constitution. See **Launching**.
- **Inside a loop** — the constitution is in the system prompt above; follow the **Loop** protocol. Ignore the other sections; a loop is already running.

**Separation of context: if you author, you do not iterate. If you iterate, you do not author.** Authoring designs the desired state from outside; iterations close the gap from inside. The constitution stays editable across iterations, but the role is set per session.

---

## What a constitution is

A design document with trust built in. Like a governmental constitution, it lays out principles and aspirations — not specific laws, not the current state of affairs. It is designed to outlast any single iteration and remain valid as the world changes around it. **A good constitution never says "50 files remain"** — that's a snapshot that goes stale. It says `check "grep -r 'old_pattern'"` — that's a principle that stays true until the work is done.

Constitutions don't prescribe steps. They describe what the system looks like when it's right — the desired state, in both senses. Whoever works from it surveys reality, reasons about the gap, and decides what's highest value. Each iteration does this with fresh context.

For deeper voice / section guidance and the discipline that keeps a constitution from sliding into a plan, see [`references/constitution.md`](references/constitution.md). For the careful-thinking rhythm that authoring usually wants (two diamonds, six stances, the funnel, the qualitative ambiguity self-check), see [`references/crafting.md`](references/crafting.md).

---

## Authoring

1. **Study** — Read relevant files, understand existing patterns. This informs the *constitution*, not the implementation. The goal is pointers iterations will follow.

2. **Draft** — Create the constitution as a markdown file. Some workflows expect it at a specific path so a runner picks it up (e.g. `/lc-from-paper` writes `constitution.md` at the reproduction workdir root); otherwise put it wherever the work lives. Frontmatter the file with:

   ```yaml
   ---
   status: active
   ---
   ```

   That's what the launcher checks; it refuses to start otherwise.

3. **Refine** — Show the draft, get feedback, revise. Use `AskUserQuestion` for structured choices. Apply the qualitative ambiguity self-check from [`references/crafting.md`](references/crafting.md) — goal, constraints, success — before launching. Reach for the crafting rhythm and stances when the conversation has careful-thinking character; skip when it doesn't.

4. **Launch** — Hand the constitution to the runner (see **Launching** below). The constitution stays editable while iterations run; each cycle re-reads it, so refinements between iterations are normal.

### What goes in a constitution

A constitution needs enough structure that an iteration landing cold can orient itself, and enough freedom that it can adapt. Common sections — use what fits, skip what doesn't, add what's missing:

```markdown
## Desired State
What the system looks like when it's done. Invariants, quality bar,
done-conditions. Fence the scope — what to aim for AND what to leave alone.

## Context
File paths, existing patterns, architectural constraints. Things iterations
need to *find* but not *achieve*.

## Skills
Which skills to activate before working.

## Evidence
How to check progress — commands, test suites, grep patterns. Pointers to
the ground truth that iterations measure themselves against.

## Open Questions
Uncertainties the user should weigh in on. Iterations add to this; the user
resolves between loops.
```

### Authoring principles

- **Constitution, not plan.** Say what the system looks like when it's right. Never describe the current state — anything that becomes false or irrelevant as work progresses doesn't belong. If a section would be outdated after one iteration, it's a snapshot — replace it with a pointer.
- **Pointers, not snapshots.** "Check `grep -r 'old_pattern'`" not "50 files remain." Snapshots go stale; pointers stay valid across iterations.
- **Reshape, don't accrete.** When the desired state evolves, rewrite the affected sections so the body still reads as today's desired state. Don't tack on "Round 2" or an "Amendments" appendix. The chronology lives in commits and sibling notes; the body lives in *now*.
- **Constraints need reasons.** Bare constraints get creatively circumvented. Include enough *why* that an iteration knows when it applies.
- **Scope is a gift.** A clear fence — "only rename, don't refactor" — saves iterations from well-intentioned drift.

### Authoring anti-patterns

- **Checklists.** "1. Add X, 2. Add Y" — iterations race through without judgment.
- **Vague done.** "Make it better" — when does iteration stop?
- **Over-specification.** Prescribing *how* instead of *what*. Trust the agent's taste.
- **Decision logs / amendment scaffolding.** "Resolved choices", "Round 2", "v2 deltas". Turns the constitution into a process journal. Fold answers into the narrative; let commits carry the chronology.

---

## Launching

The launcher is a shell script bundled with this skill. Inside a project (after `lc init` copies the bundle), its path is:

```
.claude/skills/ralph/scripts/ralph
```

Usage:

```
.claude/skills/ralph/scripts/ralph <constitution.md> [--backend claude|codex] [-- extra-flags...]
```

- `<constitution.md>` is the constitution file. YAML frontmatter must carry `status: open` or `status: active`; the launcher refuses to start otherwise. Termination is automatic when an iteration flips `status:` to `closed`.
- The launcher detaches into a tmux session named `ralph-<dirname>-<basename>` and returns immediately. Attach with `tmux attach -t <session>`. A second launch with the same constitution detects the existing session and prints the attach command instead of double-starting.

### Backends

- `claude` (default) — each iteration runs `claude --dangerously-skip-permissions --append-system-prompt <constitution>` with the constitution injected as the system prompt.
- `codex` — runs `codex --dangerously-bypass-approvals-and-sandbox --config developer_instructions=<constitution>`.

Set with `--backend codex` or `RALPH_BACKEND=codex`.

### Extra flags

Anything after a literal `--` separator forwards to the backend unchanged. Common Claude-backend flags:

- `--chrome` — Claude-in-Chrome integration for iterations that need live browser access.
- `--model <id>` — override the backend model.

### Examples

```bash
# Launch on a per-paper reproduction constitution
.claude/skills/ralph/scripts/ralph constitution.md

# Codex backend
.claude/skills/ralph/scripts/ralph constitution.md --backend codex

# Claude backend with Chrome integration and a model override
.claude/skills/ralph/scripts/ralph constitution.md -- --chrome --model claude-opus-4-6
```

---

## Loop

1. **Survey** — Fresh eyes. Read the constitution and the workdir's `CLAUDE.md`. Check `git log`, glance at sub-fibers or notes the prior iteration left, look at what's actually in the workdir.
2. **Work** — Stay and work from the vantage point the survey built. Make 1–3 substantial contributions; don't try to clear the queue in one iteration.
3. **Update** — Before exiting: commit your work; update `CLAUDE.md`'s accumulators (Paper-vs-code disagreements, Open opportunities — whichever the project carries) if anything sharpened; sharpen the constitution body itself if a fact stable enough to belong in *Context* or *Desired State* landed.
4. **Exit** — `kill $PPID`.

### Earn the vantage point

The survey is a fixed cost; exploit the warm world-model rather than rebuilding it next iteration. Exit when the next valuable move needs a different mental workspace — not when one task ends. If changes so far have been small and runway is plentiful, expand the workspace rather than exit.

**Exit before context is half-full.** Don't wait for "filling" to feel pressing — the right moment is the next sub-task boundary after you cross half. Write the handoff (commits, accumulator updates, constitution sharpening) from full attention and exit; don't try to cram one more thing in. The marginal step you'd squeeze in costs the next iteration more than it saves you, because it pays for the degraded handoff.

### Iteration rules

**State, not checklist.** The constitution describes what "done" looks like. Survey reality, decide what's highest value, work on that.

**Discoverable updates.** Commits, files in the workdir, `CLAUDE.md` accumulators — not progress notes scattered in the body. The next iteration finds what changed by inspecting the system.

**Pointers, not snapshots.** If you learn something stable, update the constitution's *Context* or *Desired State*. Don't leave drive-by notes in the body.

**You have authority.** Trust the constitution. Don't ask permission. Make substantial contributions. Don't avoid ambitious solutions just because they span multiple iterations — the loop continues; tweaks on the next iter are cheap.

**File uncertain decisions** somewhere the user will see them. The convention varies by project: an `open-questions.md` file the constitution points at, an `Open Questions` section in the constitution itself, a `-t question` felt fiber when felt is in use. Don't sediment them in invisible places.

### Long-running jobs

If an iteration kicks off computation (snakemake, cluster jobs, container builds, dev servers), use the `Monitor` tool to stream events from the background process — each stdout line surfaces as a notification, so you'll get pinged when something happens without polling-with-sleep. For one-shot "wait until done," use Bash with `run_in_background` and you'll be notified on completion. Either way, shepherd computation to completion before exiting. Don't fire-and-forget.

### Exit

Closing the constitution (`status: closed` in frontmatter) stops the loop — no further iterations will run. So the closing decision is reserved for a cold survey that finds nothing left to do.

**If you made any changes this iteration, you may not close the constitution.** Commit, update the workdir, `kill $PPID` — let the next iteration survey with fresh eyes and decide whether to close. This is the only hard rule on exit.

Making changes does NOT mean you should exit early. Keep working while the context is warm — make as many changes as belong in this iteration. The rule only constrains *closing the constitution*, not the length of the iteration. See **Earn the vantage point** above.

- **Made changes this iteration** → `kill $PPID` when the warm context is spent. Do not close the constitution.
- **Survey found zero remaining work AND you made zero changes** → flip the constitution's frontmatter `status:` to `closed`, append a closing summary to the body or a sibling notes file recording what landed, then `kill $PPID`. The launcher's next check fails and the loop terminates.

---

## References

- [`references/constitution.md`](references/constitution.md) — depth on drafting voice, sections, and the discipline that keeps a constitution from drifting into a plan.
- [`references/crafting.md`](references/crafting.md) — two-diamonds rhythm, six stances, the funnel ledger, and the qualitative ambiguity self-check. Use this when the conversation has careful-thinking character — not every authoring session needs it, but the ones that do are the ones that benefit most.

---

Loop pattern adapted from [Ralph Wiggum](https://ghuntley.com/ralph/).
