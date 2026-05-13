# /ralph

Author a constitution — a markdown document describing a desired state
for autonomous iteration — and run a ralph loop against it. The loop
is a detached tmux session that respawns a fresh worker per iteration,
with the constitution injected as system prompt. Iterations terminate
when one of them, after a cold survey, flips the constitution's
frontmatter `status:` to `closed`.

Used by [`/lc-from-paper`](lc-from-paper.md) for the long middle of a
reproduction (ARCHITECT → SPECIFY → LITERATURE → IMPLEMENT → RUN →
COMPARE). Standalone for any other long-running work where adaptation
matters more than a fixed plan: refactors, exploratory analyses,
research narratives that keep growing.

Source: [`claude/lightcone/skills/ralph/SKILL.md`](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/ralph/SKILL.md).

## Three modes

One mode applies at a time.

- **Authoring** — drafting a constitution from scratch (Study → Draft
  → Refine → Launch). Reference depth in
  [`references/constitution.md`](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/ralph/references/constitution.md)
  and the careful-thinking rhythm in
  [`references/crafting.md`](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/ralph/references/crafting.md).
- **Launching** — outside any active loop, invoking the bundled script
  to start one on an existing constitution.
- **Inside a loop** — the constitution is in the system prompt; the
  worker follows the Loop protocol (Survey → Work → Update → Exit).

## Launching

After `lc init` copies the bundle into a project, the launcher lives at
`.claude/skills/ralph/scripts/ralph`:

```bash
.claude/skills/ralph/scripts/ralph <constitution.md> [--backend claude|codex] [-- extra-flags...]
```

The constitution must have `status: open` or `status: active` in YAML
frontmatter; the launcher refuses to start otherwise. Termination is
automatic when an iteration flips `status:` to `closed`.

The session detaches as `ralph-<dirname>-<basename>`. Attach with
`tmux attach -t <session>`. A second launch with the same constitution
detects the existing session and prints the attach command instead of
double-starting.

## What goes in a constitution

A constitution describes what the system looks like when it's right —
the desired state. It outlasts any single iteration; nothing in it
goes stale as the work progresses. The constitutional principle:
write what stays true until the work is done.

Common sections — use what fits, skip what doesn't:

- **Desired State** — what "done" looks like. Invariants, quality bar,
  done-conditions. Fence the scope.
- **Context** — file paths, existing patterns, architectural constraints.
- **Skills** — which skills to activate before working.
- **Evidence** — how to check progress (commands, test suites, grep
  patterns).
- **Open Questions** — uncertainties the user weighs in on between
  loops.

See the SKILL's *What goes in a constitution* and
[`references/constitution.md`](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/ralph/references/constitution.md)
for the discipline that keeps a constitution from sliding into a plan.

## Authoring principles

- **Constitution, not plan.** Say what the system looks like when it's
  right. Never describe the current state.
- **Pointers, not snapshots.** "Check `grep -r 'old_pattern'`", not
  "50 files remain." Snapshots go stale; pointers stay valid.
- **Reshape, don't accrete.** When the desired state evolves, rewrite
  the affected sections — don't tack on "Round 2" or "Amendments."
- **Constraints need reasons.** Bare constraints get circumvented.
- **Scope is a gift.** A clear fence frees iterations to work
  confidently inside it.

## Loop discipline

Each iteration: Survey → Work → Update → Exit (`kill $PPID`). The
survey is a fixed cost; exit when the next valuable move needs a
different mental workspace, not when one task ends. Exit before context
is half-full — the handoff matters more than the marginal step you'd
squeeze in.

**Closing the constitution is reserved for cold surveys that find
nothing left to do.** If an iteration made any changes, it may not flip
`status:` to `closed`; that decision waits for the next fresh-eyes
iteration. This adds at least one cold review pass on every closing
decision.

## Related

- [`/lc-from-paper`](lc-from-paper.md) — uses `/ralph` for the long
  middle of a reproduction.
- [Bundle README](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/README.md).
