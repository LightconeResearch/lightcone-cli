# /lc-new

Scope a new ASTRA analysis through conversation. Produces a complete
`astra.yaml` (and optionally a literature evidence trail) with no code
written.

Source: [`claude/lightcone/skills/lc-new/SKILL.md`](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/lc-new/SKILL.md).

## Allowed tools

```
Read, Write(astra.yaml), Write(universes/*), Write(CLAUDE.md),
Edit(astra.yaml), Edit(universes/*), Edit(CLAUDE.md),
Glob, Grep, Bash(astra:*), Bash(lc:*), Bash(mkdir:*), Bash(echo:*),
WebSearch, WebFetch, AskUserQuestion, Task
```

The skill is locked to spec-only writes — it cannot write Python, R, or
arbitrary files. The lc-extractor subagent is invoked via `Task`.

## Phases

1. **Research question** — sharpen the question, write `version`, `name`,
   `description` to `astra.yaml` immediately so the user sees progress.
2. **Analysis structure** — walk through inputs, outputs, sub-analyses.
   One output per output: a single metric, a single plot, a single
   artifact. Updates `astra.yaml` with `inputs:` and `outputs:`.
3. **Deep dive** (per section) — optional literature pass. Collect paper
   candidates; for each approved paper, spawn one `lc-extractor`
   subagent (parallel, via `Task`). Each subagent reads the PDF, pulls
   verbatim quotes, runs `astra paper verify-quotes` to machine-verify
   the quotes against the source, and returns extracted prior insights.
   Then identify decisions informed by the conversation + literature
   and write them to `astra.yaml`.
4. **Finalize** — `astra validate astra.yaml`, `astra validate
   --verify-evidence` if quotes exist, `astra universe generate -n
   baseline`, populate the `## Working Notes` section of `CLAUDE.md`
   with conversational context not captured in the spec.

The skill writes to `astra.yaml` after each phase rather than in bulk
at the end so the user has something visible to review at every step.

## Hard restrictions (from the SKILL.md)

- Specification agent only — cannot write Python, R, or other
  implementation code.
- Files it may touch: `astra.yaml`, `universes/*.yaml`, `CLAUDE.md`
  (Finalize only).
- Never fabricates quotes — all evidence must pass
  `astra validate --verify-evidence`.
- PDFs are read by lc-extractor subagents only; the main agent never
  pulls a PDF into its own context.

## Anti-patterns called out in the prompt

- Bulk-writing decisions at the end instead of after each crystallizes.
- Accepting vague goals like "analyze this data" without sharpening.
- Method-only decisions; the prompt actively probes for data
  exclusion, variable operationalization, inference criteria.
- Reading PDFs in the main agent context.
- Skipping `astra validate --verify-evidence`.

## Related

- [`/lc-build`](lc-build.md) — the next step after `/lc-new`.
- [`claude/lightcone/guides/astra-reference.md`](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/guides/astra-reference.md) — `astra.yaml` schema, decision criteria, prior insights / findings, universe management.
- [`claude/lightcone/agents/lc-extractor.md`](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/agents/lc-extractor.md) — the literature extraction subagent definition.
