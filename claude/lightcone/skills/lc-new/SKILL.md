---
name: lc-new
description: Create a new ASTRA analysis project with integrated literature support. Scope the research question through conversation, structure outputs and decisions, search for and extract evidence from scientific papers, and build a complete astra.yaml specification. Use when starting a new analysis, when the user says "new project", "new analysis", or "scope". Triggers on "new", "scope", "research question", "start analysis".
allowed-tools: Read, Write(astra.yaml), Write(universes/*), Write(CLAUDE.md), Edit(astra.yaml), Edit(universes/*), Edit(CLAUDE.md), Glob, Grep, Bash(astra:*), Bash(lc:*), WebSearch, WebFetch, AskUserQuestion, Agent
---

# /lc-new

Create a new ASTRA analysis project through conversation. Build the spec iteratively -- write to `astra.yaml` after each phase so the user sees progress. Literature search and decision identification happen in distinct phases -- talk first, then extract papers, then identify decisions informed by both conversation and literature.

## References

- [ASTRA Reference](../../guides/astra-reference.md) -- spec structure, decision identification, recipes, universes
- [lightcone-cli Reference](../../guides/lightcone-cli-reference.md) -- `lc` workflow for the implementation phase that follows scoping

## Setup

1. Read `astra.yaml` if it exists (to understand context or avoid overwriting)
2. Note the analysis directory for later

---

## Phase 1: Research Question

Stage banner: RESEARCH QUESTION

> "What are you trying to learn? Describe the question in your own words."

Then sharpen:
- "What would a clear answer look like?" (sharpens the description)
- "Why does this matter?" (context for decisions)

**Update astra.yaml** — set `name`. (`astra init` scaffolded a placeholder example decision/input/output plus a TODO `narrative` skeleton; the placeholder structure is replaced in Phases 2–3, and narrative prose is filled in Finalize once structure has settled — written too early it goes stale.)

---

## Phase 2: Analysis Structure

Stage banner: ANALYSIS STRUCTURE

> "Walk me through your analysis step by step. What goes in, what comes out at the end?"

**Guidance on sub-analyses:** Analyses should only be split into multiple sub-analyses if each sub analysis genuinely has materially different inputs and outputs, and if the scope may be too broad if there is just one analysis; we overall want a sub-analysis to feel like it should genuinely be a self-contained product. For example, training + evaluation would typically be one analysis, because the product would be the trained and validated neural network estimator. When in doubt, opt for a single analysis at this stage. If it does need to be multi-stage, ask the user for confirmation and how to split it. For multi-stage analyses, make sure you confirm stage boundaries. See `.claude/guides/astra-reference.md` for YAML structure and sub-analysis guidance.

**One output per output.** Each output should be a single metric, a single plot, or a single artifact. Do not bundle multiple metrics into one output (e.g., "performance_metrics" containing accuracy, F1, and AUC). Each of those is its own output. Same for plots -- one figure per output.

**Update astra.yaml** with `inputs` and `outputs` (extending the spec from Phase 1).

---

## Phase 3: Deep Dive

Stage banner: DEEP DIVE -- [SECTION NAME]

Ask the user if they want to do a literature deep dive for this section. If not, skip straight to decision identification.

### Paper Collection

Ask if the user has specific papers they want to look into. Also search with WebSearch for highly relevant papers -- keep it limited, only papers that directly bear on the analysis. Use AskUserQuestion to present the list with a one-line description of each paper and why it's relevant. The user can check off which ones to extract and add any others.

### Extraction

For each approved paper: `astra paper add <doi>`, `astra paper path <doi>`, then spawn one `lc-extractor` agent per paper. The agent definition already contains extraction instructions, output format, and verification logic -- you just fill in the paper-specific context.

**Spawning each agent:** Use `Agent(subagent_type="lc-extractor", prompt="...")`. In the prompt, provide:
- **Analysis context**: the analysis description and decisions this paper might inform
- **Paper details**: DOI, version (arXiv only), PDF path (from `astra paper path`)
- **Target decisions**: each decision ID, label, and options with descriptions
- **Timestamp**: current time in ISO 8601

Spawn all in a single message (parallel). Show progress as results come in:

```
  ✓ Ba et al. 2016 -- 3 prior insights
  ○ Wu & He 2018 (reading...)
```

Write extracted prior insights to astra.yaml immediately. Synthesize them by topic for the user.

### Decision Identification

Use the conversation and literature to identify decisions. Apply the decision criteria from [astra-reference.md](../../guides/astra-reference.md):

- What could be done differently and still be defensible?
- Where did papers disagree or compare alternatives?
- Where did the user express uncertainty?

Write candidate decisions to astra.yaml as a batch for user review. Keep chat output concise (summary + decision IDs), and avoid dumping full decision details in chat.

**Probe for blind spots** -- analysts over-focus on methods and neglect data handling. Probe 1-3 areas: data exclusion, variable operationalization, inference criteria.

### Decision Review

During review, confirm or set each decision's `default`, keep option structure and evidence links, and remove any decisions the user rejects.

---

## Checkpoint

> "Anything else that should inform this analysis?"

Review the spec with the user. Update astra.yaml with any additions.

---

## Finalize

Stage banner: FINALIZING

### Validate

1. `astra validate astra.yaml` -- fix errors, iterate until clean
2. If prior insights exist: `astra validate astra.yaml --verify-evidence`

### Generate Baseline Universe

```bash
astra universe generate -n baseline
```

### Populate Narrative

Replace the TODO entries in `astra.yaml`'s `narrative:` block now that structure is stable: `summary` (one-paragraph framing), `methods` (decisions and sub-analyses), `inputs`, `outputs`. Use `#path.to.element` anchors for cross-references. Leave `findings` as TODO until results exist.

### Populate CLAUDE.md

Read the existing `CLAUDE.md` (created by `lc init`). Fill the `## Project Notes` section per the inline guidance there — context from the conversation that's not in `astra.yaml` and would be lost after `/clear`. The spec is the source of truth for structure, decisions, and evidence.

### Review with User

> "Anything you'd like to change? Otherwise the specification is ready."

If edits requested, apply, re-validate, and update CLAUDE.md.

---

## Done

Stage banner: SPECIFICATION COMPLETE

Show summary table:

```
| Section       | Decisions | Outputs | Prior Insights |
|---------------|-----------|---------|----------|
| (top-level)   | 3         | 2       | 5        |
| sub_analysis  | ...       | ...     | ...      |
```

Then tell the user the spec is ready and they can begin implementation. Recommend running `/clear` first — the scoping conversation consumes significant context, and everything needed to continue is captured in `astra.yaml` and `CLAUDE.md`.

---

## Restrictions

**You are a specification agent, not an implementation agent.**

You MUST NOT write Python, R, or other implementation code.

You MUST ONLY create/modify: `astra.yaml`, `universes/*.yaml`, `CLAUDE.md` (Finalize only).

You MUST NOT fabricate quotes -- all evidence must pass `astra validate --verify-evidence`.

You MUST spawn `lc-extractor` agents for paper processing. One paper per agent. Never read a PDF in the main agent context.

---

## Anti-Patterns

- **Waiting to write** -- Update astra.yaml after each decision crystallizes, not in bulk at the end
- **Accepting vague goals** -- "Analyze this data" is not a research question; push back
- **Method-only decisions** -- Actively probe for data handling and exclusion criteria, not just method choices
- **Literature as afterthought** -- Do not defer all literature to the end. Collect paper candidates during conversation (Phases 1-2) and extract them before identifying decisions (Extraction before Decision Identification in Phase 3)
- **Too many papers** -- ~2 papers per topic area, max 10 per section; do not try to be exhaustive
- **Background interruptions** -- Never spawn search or extraction subagents during conversation phases. Collect candidates first, then process them during Phase 3 Extraction
- **Reading PDFs in main context** -- Always delegate to subagents; PDFs consume too much context
- **Chat dump of decisions** -- Do not dump full candidate decision content in chat; write decisions to astra.yaml for review
- **Skipping verification** -- If quotes were extracted, always run `astra validate --verify-evidence`
