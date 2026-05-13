---
name: check-sentence-by-sentence
description: >
  Sentence-by-sentence audit of a paper against an ASTRA project's code. For
  every claim about implementation or results in the methodology, results,
  discussion, and appendices, locate the corresponding code (file:line) or
  mark NOT FOUND. Only the user can invoke this skill, though this skill can be suggested for the user to invoke during paper reproduction. Other skills may mention this skill as an optional follow-up, but should not invoke it themselves. Run from the project folder containing astra.yaml. In lc-from-paper projects, read paper sources from
  work/reference/: prefer arXiv TeX under work/reference/source/, fall back to
  Docling/Pandoc markdown at work/reference/document.md.
argument-hint: "[path to paper source, e.g. work/reference/source/main.tex or work/reference/document.md]"
---

# /check-sentence-by-sentence

Audit a paper against the code in this ASTRA project, sentence by sentence.
Every sentence that asserts an implementation detail or a numerical/empirical
result is located in the code (`file:line`) or marked NOT FOUND. The agent
does NOT run any code -- this is a static reading audit.

In lc-from-paper projects, the paper substrate comes from `work/reference/`.
Path A is arXiv source at `work/reference/source/`; Path B is the parsed
markdown fallback at `work/reference/document.md`, produced by Docling or
Pandoc.

## Setup

1. **Confirm project root.** Read `astra.yaml` in the current working
   directory. If it is missing, ask the user:

   > "I do not see an `astra.yaml` in the current directory. Please point me
   > to the ASTRA project folder, or `cd` there and re-invoke."

   Stop until resolved.

2. **Confirm paper source.** The user may have passed a path as an
   argument. Resolve it in this order:

   1. If the argument is a `.tex` file, use it in `tex` mode.
   2. If the argument is `work/reference/` or another directory, first look
      for TeX source under `<dir>/source/`, then for `<dir>/document.md`.
   3. If no argument was supplied, prefer the lc-from-paper layout:
      - `work/reference/source/<main>.tex` if TeX source exists. Identify the
        main file with `grep -l '\\documentclass' work/reference/source/*.tex`;
        if exactly one file matches, use it. If multiple files match, ask the
        user which one is the main paper file. After identifying the main
        file, expand its local `\input{...}` and `\include{...}` files before
        section enumeration; many arXiv papers keep most prose outside the
        main TeX wrapper.
      - `work/reference/document.md` if there is no TeX source. This is the
        Docling/Pandoc fallback and should be audited in `markdown` mode.
   4. Only after those lc-from-paper paths fail, look for an obvious legacy
      `.tex` source in cwd: a top-level `*.tex`, or one inside `paper/`,
      `tex/`, or a similarly named subdirectory. If exactly one obvious
      candidate is found, use it in `tex` mode.

   If no usable source is found, ask:

   > "Which paper source should I audit? Please give me a `.tex` path or
   > `work/reference/document.md`."

   If only `work/reference/paper.pdf` exists, ask the user to run the PARSE
   phase first so `work/reference/document.md` exists. Do not audit PDFs
   directly.

## Section enumeration

This is **your job in the main agent** -- do it carefully so each subagent
gets a precise line range. Do NOT read full section content; only enough to
identify boundaries.

1. Enumerate sections according to source mode:
   - In `tex` mode, first build the ordered audit source list. Start with the
     main TeX file, scan it for local `\input{...}` and `\include{...}` paths,
     normalize missing `.tex` suffixes, and include those files when they
     exist under the same source tree. Recurse one level deeper when an
     included file itself includes local TeX files. Ignore package/style
     imports (`\usepackage`, `.sty`, `.cls`) and remote/generated files. If
     the main file is mostly a wrapper, the leaf included files will carry
     most audit units.
   - For every file in the TeX audit source list, use `grep -n` for
     `^\\section`, `^\\subsection`, and `^\\appendix`. Record each match's
     file path, line number, and label.
   - In `markdown` mode, use `grep -n` for markdown headings
     (`^#`, `^##`, `^###`, etc.) in `work/reference/document.md`. Treat
     heading depth the way TeX treats section/subsection. If Docling emitted
     unnumbered headings, use their text labels.
2. Get the file's total line count with `wc -l`.
3. Compute each section's line range: **start = the section's own line
   number; end = (next section/subsection or same/lower heading-depth start
   minus 1 in the same source file), or that source file's last line for the
   final section in that file.** For a section that contains subsections,
   each subsection's range runs from its own line to (next subsection
   start − 1), and the section's pre-subsection prose (if any) becomes its
   own audit unit covering (section line + 1) to (first subsection − 1) if
   that span is non-trivial.
4. Mark sections appearing after `\appendix` (TeX) or after an `Appendix` /
   `Appendices` heading (markdown) as appendices regardless of label.

Identify the audit-relevant sections:

- Methodology (often `Methods`, `Analysis`, `Data`, `Sample selection`)
- Results
- Discussion (often `Discussion and Conclusions`)
- Appendices (every section after `\appendix`)

Skip Abstract, Introduction, Acknowledgements, References, author lists.

For each retained section, check whether it has subsections. **Spin up one
subagent per leaf (sub)section** -- a section with subsections becomes one
subagent per subsection (plus optionally one for any pre-subsection prose
span); a section without subsections becomes one subagent for the whole
section. Spawn them all in a single message so they run in parallel.

## Subagent prompt

Use `Agent(subagent_type="general-purpose", ...)`. Pass each subagent:

- The absolute path to the paper source file for this section
- The paper source mode: `tex` or `markdown`
- The exact section/subsection label and the line range in the source file
  it covers (so it knows where to read)
- The absolute path to the project root (which contains `astra.yaml`)
- The instructions below, verbatim

```
You are auditing one (sub)section of a paper against an ASTRA project's
code. Your job is mechanical and exhaustive.

INPUTS
- Paper source file: <path>
- Source mode: <tex|markdown>
- Section: <name>, lines <start>-<end>
- Project root: <path>

PROCEDURE
1. Read the assigned section of the paper. Split it into sentences using
   common sense, not naive period-splitting. In `tex` mode, use TeX-aware
   splitting; in `markdown` mode, preserve Docling/Pandoc math blocks,
   captions, and headings as source text. Treat `e.g.`, `i.e.`, `et al.`,
   `Fig.`, `Eq.`, `Sec.`, `Dr.`, decimals (`0.5`), inline math `$...$`,
   and citation commands (`\citep{...}`, `\citet{...}`) as part of the
   surrounding sentence, not boundaries. Display equations belong to
   whichever sentence introduces them.
2. For each sentence, decide using common sense: does it make a concrete
   claim about an IMPLEMENTATION DETAIL (a method, parameter, threshold,
   formula, data cut, model choice, sample definition, algorithmic step)
   or a RESULTS DETAIL (a numerical value, plot, fitted parameter,
   statistical outcome)? If neither -- pure motivation, citation prose,
   or generic framing -- skip it.
3. Before searching, **read `astra.yaml` once** -- it is a pre-built
   paper↔code map maintained by the project. Harvest specifically:
     - `narrative.methods` — links paper methodology concepts to decision
       IDs (e.g. paper prose "the chosen <method>" → `#decisions.<id>`)
     - `narrative.findings` — links paper claims/values to result anchors
     - `prior_insights` (if present) — extracted paper quotes already tied
       to decisions
     - per-decision `evidence` quotes and `description` fields
   Treat these as your translation table: paper prose → decision/output
   IDs → script files. Do not re-derive what the spec already encodes.

   For everything not covered by the spec, use common sense to translate
   concepts. In general:
     - A quality cut stated as a ratio or threshold may appear in code
       under an inverted form or a different variable name -- map by
       meaning, not by symbol.
     - A named model or distribution will usually appear as a function
       whose name describes its shape or role, not as the paper's prose
       phrasing.
     - A cited constant from a referenced paper will usually appear as a
       module-level constant or as an option value in a decision.
   Grep for the underlying concept, not just the paper's wording.
4. For every claim-bearing sentence, search the project code (`scripts/`,
   source files, `universes/`, `astra.yaml`, `results/`) for where the
   claim is implemented or computed. Use Grep, Glob, and Read.
5. Record one of:
   - (quote, path/file.py:LINE, optional <10-word note)
     when the sentence's claim is implemented or computed at that location
   - (quote, NOT FOUND, optional <10-word note)
     when no implementation or matching computation is present

CONSTRAINTS
- Do NOT run any code. No Bash beyond ls/grep/find/wc for searching.
- Do NOT read the paper outside the assigned line range.
- Quote the sentence verbatim, trimmed to a single sentence. If the
  sentence is long, you may include just the claim-bearing clause but
  preserve enough text to identify it.
- file:line should point to the most specific line that implements or
  states the claim (the function call, parameter assignment, or computed
  value -- not just the file).
- Notes must be under 10 words. Use them for nuance like "approximate
  match", "different constant", "implemented but commented out",
  "value computed at runtime, not statically comparable", "produced as
  figure but printed value not stored".
- For numerical results that the paper states as a final number, point
  at the line that computes the value and use a note like "value
  computed at runtime" -- you cannot verify numerical agreement without
  executing code, and that is fine.

OUTPUT
Return a JSON-ish list, one entry per sentence, in paper order:

[
  {"quote": "...", "location": "scripts/foo.py:142", "note": "..."},
  {"quote": "...", "location": "NOT FOUND", "note": "..."},
  ...
]

Return nothing else.
```

## Aggregation

When all subagents return, you receive raw entries from every claim-bearing
sentence each subagent kept. **Do not just concatenate and print them.**
Two filtering passes happen here, in this order:

### Pass 1 — drop non-computational sentences

Subagents are deliberately generous about what they keep, so the raw list
contains a long tail of sentences that quote the paper but do not actually
correspond to anything you would expect to find in code. **Drop any entry
whose sentence is:**

- **Framing / motivation** — sentences whose job is to set up the next
  step, e.g. "the first step is...", "to investigate this...", "we want
  to look at...", "for this reason..."
- **Citation prose / literature comparison** — sentences that compare to
  or quote prior literature, e.g. "agrees with values typical of previous
  measurements...", "much like Author+YYYY they show...", "in particular,
  Author found <value>..."
- **Theoretical framing or derivations** — sentences asserting a property
  expected from theory rather than implemented in code, and restatements
  of textbook identities used only to introduce the next equation
- **Rhetorical / interpretive claims** — qualitative readings of a
  figure or trend, e.g. "the trend clearly has an oscillatory
  behaviour", "the trend seems to be independent of <variable>", "this
  supports that..."
- **Conclusions / justifications / qualitative observations** —
  "thus we conclude that...", "we choose not to include this
  because...", "by and large the trends are similar"
- **Future work / speculation** — "this could be improved by...", "the
  discrepancy could be explained by..."
- **Forward/backward references with no claim** — "we discuss this in
  Sec X below", "as described in Sec Y above"
- **NOT FOUND entries that fall in any of the above categories** — most
  framing/motivation sentences will land as NOT FOUND because there is
  nothing to find. Drop them silently; they are noise, not gaps.

Keep an entry only if it asserts something a reader would expect to be
implemented or computed: a parameter value, a cut, a formula, an
algorithmic step, a fitted/measured value, a figure that the project
should produce, a sample size after a specific cut.

When in doubt about a NOT FOUND, ask: "if this sentence is not in the
code, is that a real gap?" If no, drop it.

### Pass 2 — deduplicate / merge near-duplicates

Subagents do not see each other, and the same claim is often restated
across sentences within a (sub)section -- e.g. a prose statement of a
cut followed by a sentence asserting "this is the only cut we make", or
two sub-equations of one larger formula that map to the same line.
Collapse these:

- If two adjacent sentences make the same claim and resolve to the same
  `file:line`, keep one entry whose quote is the more specific or
  formula-bearing of the two, and append the other in a short
  parenthetical only if it adds information.
- If a paper-text claim and an explicit equation/quoted code map to the
  same line, prefer the equation/quoted-code form.
- Do not merge across (sub)sections.
- Do not merge if the two sentences resolve to different `file:line`
  locations -- they may look similar but are doing different things.

### Pass 3 — render

After filtering and deduplication, present the result to the user as
markdown, organized by section -> subsection -> sentence, in paper order:

```
# Sentence-by-sentence reproduction audit

Paper: <path>
Project: <path>

## <Section>

### <Subsection>            (omit if no subsections)

- "<sentence quote>"
  → ✅ `scripts/foo.py:142` -- <note if any>

- "<sentence quote>"
  → ❌ NOT FOUND -- <note if any>
- ...
```

Use `→ ✅ \`file:line\`` for found entries and `→ ❌ NOT FOUND` for
missing ones. Notes are optional; only include the trailing `-- <note>`
when the subagent supplied one.

End with a one-line summary:

> N sentences audited across M sections. K implemented, J not found.

### Follow-up suggestion (conditional)

After the summary, scan the NOT FOUND entries and **cluster them**. A
cluster is a group of NOT FOUND sentences that all relate to the same
missing piece of work (a missing analysis, a missing diagnostic, an
unimplemented model variant) -- usually a few consecutive sentences in
one (sub)section, or sentences that all reference the same concept across
sections.

**Only emit the follow-up block if there is at least one major
unimplemented cluster** -- a cluster of genuine missing computation
substantial enough to be worth offering to add (rule of thumb: ≥3
sentences of related missing-computation claims, or a single
heavyweight missing artifact like an entire missing analysis or
figure). If every NOT FOUND is isolated framing, motivation, or
qualitative interpretation -- or if the only clusters are tiny -- stop
after the one-line summary. Do not pad with a follow-up just to have
one.

When the threshold is met, write a short follow-up block in this shape:

> Major unimplemented clusters: (1) `<short description of cluster 1>`
> (`<§section>`, ~`<N>` sentences), and (2) `<short description of
> cluster 2>` (`<§section>`, ~`<N>` sentences). The rest of the NOT
> FOUND entries are pure framing/motivation/qualitative interpretation,
> not computational claims. Worth considering as a follow-up if you
> want full coverage — want me to add `<concrete artifact 1>` and
> `<concrete artifact 2>`?

Rules for this block:
- Only call out clusters that look like genuine missing computation, not
  rhetoric.
- Keep it to 1–3 clusters. Do not enumerate every NOT FOUND entry.
- The closing offer must name **concrete artifacts** the user could add
  (a new output ID, a new script filename, a new decision option, a new
  figure) -- not vague promises like "fill in the gaps".
- Cite the section reference in the project's own notation (`§2.1`,
  `Appendix B`, etc.) and an approximate sentence count.
- One short paragraph; do not pad.

## Restrictions

- You MUST NOT run project code, recipes, or `lc run`. This is static.
- You MUST NOT read the paper source wholesale into the main context;
  delegate to subagents.
- You MUST NOT modify any project file. Read-only.
- You MUST NOT fabricate `file:line` locations -- if a subagent's location
  looks suspicious, ask it to re-verify rather than guessing.
- You MUST spawn one subagent per leaf (sub)section, in parallel.

## Anti-patterns

- **Auditing intro/abstract** -- skip narrative-only sections; only
  methodology, results, discussion, and appendices.
- **Bundling sentences** -- one entry per sentence. Do not collapse
  multiple claims into one row even if they share a citation or location.
- **Vague locations** -- a bare filename (`scripts/foo.py`) is not
  enough; a line number is required for found entries.
- **Long notes** -- the 10-word cap is a hard limit; reserve notes for
  signal, not commentary.
- **Running code to verify** -- this skill is a reading audit. If a claim
  cannot be verified by reading code alone, mark it found at the
  computing line and note "value computed at runtime" rather than
  executing anything.
