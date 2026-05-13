# /check-sentence-by-sentence

Sentence-by-sentence audit of a paper against an ASTRA project's code.
For every claim about implementation or results in the methodology,
results, discussion, and appendices, locate the corresponding code
(`file:line`) or mark `NOT FOUND`. The agent does **not** run any
code — this is a static reading audit.

Source: [`claude/lightcone/skills/check-sentence-by-sentence/SKILL.md`](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/check-sentence-by-sentence/SKILL.md).

Argument hint: `[path to paper source, e.g. work/reference/source/main.tex or work/reference/document.md]`.

## Allowed tools

```
Read, Glob, Grep,
Bash(ls:*), Bash(wc:*), Bash(grep:*), Bash(find:*),
AskUserQuestion, Agent
```

Read-only over both the paper source and the project code. No
execution.

## Setup

1. **Confirm project root.** `astra.yaml` in cwd, or ask the user to
   `cd` to the ASTRA project.
2. **Confirm paper source.** Resolve in order:
   - A `.tex` argument → `tex` mode.
   - A directory argument → look for `<dir>/source/` (TeX), then
     `<dir>/document.md` (markdown).
   - No argument → prefer the lc-from-paper layout:
     `work/reference/source/<main>.tex` (Path A) or
     `work/reference/document.md` (Path B, Docling/Pandoc fallback).
   - Legacy `.tex` locations in cwd as a last resort.

Don't audit PDFs directly — if only `work/reference/paper.pdf` exists,
ask the user to run paper extraction first.

## Section enumeration

The main agent walks the source carefully to enumerate sections.

- **`tex` mode** — build an ordered audit source list by following
  local `\input{...}` / `\include{...}` from the main TeX file (one
  level deep). For each file, `grep -n` for `^\\section`,
  `^\\subsection`, and `^\\appendix`. Many arXiv papers keep prose
  outside the main wrapper, so the included files carry most audit
  units.
- **`markdown` mode** — `grep -n` for `^#`, `^##`, etc. in
  `document.md`. Heading depth maps to TeX section/subsection.

Audit-relevant sections: methodology, results, discussion,
appendices. Skip abstract, introduction, acknowledgements,
references, author lists.

Each leaf (sub)section becomes one sub-agent. A section with
subsections spawns one sub-agent per subsection, plus optionally one
more for any pre-subsection prose span. Issue them in a single
tool-use block so they run in parallel.

## Per-sub-agent output

Each sub-agent reads its assigned line range, splits into sentences,
keeps the claim-bearing ones, and returns:

```
[
  {"quote": "...", "location": "scripts/foo.py:142", "note": "..."},
  {"quote": "...", "location": "NOT FOUND", "note": "..."},
  ...
]
```

`note` is optional, under 10 words, used for nuance like "approximate
match", "different constant", "value computed at runtime".

## Aggregation: two filtering passes

Sub-agents are deliberately generous about what they keep. The main
agent then:

1. **Drops non-computational sentences** — framing / motivation
   ("the first step is..."), pure prose that doesn't correspond to
   anything you'd expect in code.
2. **Merges duplicates** — when the same claim is asserted in multiple
   places, collapse to a single entry pointing at the canonical
   location.

The final report is paper-order: methodology → results → discussion →
appendices, with each entry's `quote`, `location`, and `note`.

## Hard rules

- **No execution.** Numerical results can be located at the line that
  computes them, but agreement isn't verifiable here. Use a note like
  "value computed at runtime".
- **Quote verbatim.** Trim to one sentence; long sentences may keep
  just the claim-bearing clause.
- **`file:line` is specific.** The function call, parameter assignment,
  or computed value — not just a file.
- **Read only the assigned line range.** Each sub-agent stays inside
  its window.

## When to invoke

- From `/lc-from-paper`'s REVIEW close-out (opt-in).
- Standalone, any time, to spot-check fidelity claim by claim.

## Related

- [`/lc-from-paper`](lc-from-paper.md) — invokes
  `/check-sentence-by-sentence` during REVIEW (opt-in).
- [`/figure-comparison`](figure-comparison.md) — the other REVIEW
  close-out, artifact-vs-artifact rather than paper-vs-code.
- [`/paper-extraction`](paper-extraction.md) — produces the paper
  substrate this skill reads.
