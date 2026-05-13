# /lc-new

Scope a new ASTRA analysis from a research question, through
conversation. The output is a complete `astra.yaml` and (optionally) a
literature evidence trail. Implementation comes later — `/lc-new`
writes spec, not code.

Source: [`claude/lightcone/skills/lc-new/SKILL.md`](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/lc-new/SKILL.md).

## Allowed tools

```text
Read, Write(astra.yaml), Write(universes/*), Write(CLAUDE.md),
Edit(astra.yaml), Edit(universes/*), Edit(CLAUDE.md),
Glob, Grep, Bash(astra:*), Bash(lc:*),
WebSearch, WebFetch, AskUserQuestion, Agent
```

Writes are locked to the spec surface — no Python, no R, no arbitrary
files. The `lc-extractor` subagent is dispatched via `Agent`.

## Phases

1. **Research question.** Sharpen the question, then write `version`,
   `name`, and `description` to `astra.yaml` so the user has something
   visible to react to from the first turn.
2. **Analysis structure.** Walk through inputs, outputs, and any
   sub-analyses. One output per output: one metric, one plot, one
   artifact. `inputs:` and `outputs:` land in `astra.yaml` as they
   crystallize.
3. **Deep dive (per section).** An optional literature pass. Collect
   paper candidates with the user; for each approved paper, dispatch
   one `lc-extractor` subagent in parallel. Each subagent reads the
   PDF, pulls verbatim quotes, runs `astra paper verify-quotes` to
   machine-verify the quotes against the source, and returns prior
   insights. Decisions then fall out of the conversation and the
   literature together.
4. **Finalize.** `astra validate astra.yaml`; `astra validate
   --verify-evidence` if quotes exist; `astra universe generate -n
   baseline`. Populate the `narrative:` block (`summary`, `methods`,
   `inputs`, `outputs` — `findings` stays TODO until results exist),
   then fill the `## Working Notes` section of `CLAUDE.md` with
   conversational context the spec doesn't carry.

Writes happen at the end of each phase, not in bulk — the user always
has something visible to review.

## Hard restrictions (from the SKILL.md)

- Specification agent only. No Python, no R, no implementation code.
- Touchable files: `astra.yaml`, `universes/*.yaml`, and `CLAUDE.md`
  (Finalize only).
- Quotes are never fabricated; every evidence entry must pass
  `astra validate --verify-evidence`.
- PDFs stay inside `lc-extractor` subagents — the main agent never
  pulls one into its own context.

## Anti-patterns called out in the prompt

- Bulk-writing decisions at the end instead of after each crystallizes.
- Letting vague goals like "analyze this data" pass without sharpening.
- Method-only decisions. The prompt actively probes data exclusion,
  variable operationalization, and inference criteria.
- Reading PDFs in the main agent context.
- Skipping `astra validate --verify-evidence`.

## Related

- After `/lc-new`, ask the agent to implement the spec through the
  normal Claude Code workflow.
- [`/astra`](index.md#reference-skills-auto-primed-via-session-start) — `astra.yaml` schema, decision criteria, prior insights / findings, universe management.
- [`claude/lightcone/agents/lc-extractor.md`](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/agents/lc-extractor.md) — the literature extraction subagent definition.
