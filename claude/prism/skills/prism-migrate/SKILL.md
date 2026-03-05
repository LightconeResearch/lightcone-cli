---
name: prism-migrate
description: Migrate an existing project into the ASTRA/Prism framework. Walks through a guided migration that identifies existing scripts, data, configs, and decisions, then scaffolds the ASTRA structure around them. Use when the user has existing analysis code they want to bring into ASTRA. Triggers on "migrate", "convert", "port", "import project", "existing project".
allowed-tools: Read, Write(astra.yaml), Write(universes/*), Write(CLAUDE.md), Write(.claude/migration-plan.md), Edit(astra.yaml), Edit(universes/*), Edit(CLAUDE.md), Glob, Grep, Bash(astra:*), Bash(prism:*), Bash(mkdir:*), Bash(echo:*), Bash(ls:*), Bash(cp:*), Bash(mv:*), Bash(git:*), Bash(python:*), WebSearch, WebFetch, AskUserQuestion, Task
argument-hint: "[path | github-url]"
---

# /prism-migrate

Discover what exists in an existing project and wrap it in ASTRA structure -- preserving the user's work while adding specification, decision tracking, and reproducibility.

## References

- [Decision Guide](../prism-new/decision-guide.md) -- decision identification, prioritization, blind-spot checklist
- [CLAUDE.md Template](../../templates/CLAUDE.md) -- project CLAUDE.md structure

## Setup

1. Read `astra.yaml` if it exists. Abort if it already has outputs with recipes and decisions with options -- suggest editing `astra.yaml` directly or running `/prism-build`.
2. Note the working directory.

---

## Phase 1: Locate the Project

Stage banner: PROJECT DISCOVERY

Ask where the project lives: local path, GitHub URL, paper with code, or already in this directory. Clone or copy as needed.

Scan and classify the project type (Python, R, notebook, pipeline, paper-with-code, mixed). Report what you found and ask the user to confirm.

---

## Phase 2: Inventory

Stage banner: INVENTORY

Systematically scan the project. Read scripts to understand what they do -- do not guess.

### 2a. Scripts & Entry Points

For each script: what it does, what it reads, what it writes, what parameters it uses. Present as a table with ASTRA output types:

```
| Script          | Purpose         | Inputs         | Output (ASTRA type) | Parameters      |
|-----------------|-----------------|----------------|---------------------|-----------------|
| train.py        | Train model     | data/train.csv | best.pt (data)      | lr, epochs, ... |
| evaluate.py     | Evaluate model  | models/best.pt | acc.json (metric)   | threshold       |
| plot_results.py | Generate figures | results/*.json | fig1.png (figure)   | --              |
```

### 2b. Data, Outputs, Dependencies

- **Inputs**: data files, configs, external sources. Classify as `data`, `parameter`, or `reference`.
- **Outputs**: files produced by scripts. Classify as `metric`, `figure`, `table`, `data`, or `report`. **One output per output** -- don't bundle multiple metrics or plots.
- **Dependencies**: `requirements.txt`, `pyproject.toml`, `environment.yml`, `Dockerfile`, etc.

### 2c. Hardcoded Decisions

Scan for hardcoded analytical choices: magic numbers, commented alternatives, config dicts, method-selecting branches. Note file, line, current value, what it controls.

### Review

Present inventory to user. Ask what's missing, miscategorized, or should be ignored.

---

## Phase 3: Research Question & Structure

Stage banner: ANALYSIS STRUCTURE

The project already exists -- derive the research question and structure from what the code does.

### 3a. Research Question

Propose a research question, description, and success criteria. Ask the user to confirm or refine.

### 3b. Analysis Boundaries

Single analysis by default. Split only if stages are genuinely standalone units with own objectives, artifacts, and decisions. Ask the user to confirm.

### 3c. Decision Identification

Present hardcoded values from Phase 2c as candidate decisions. For each, propose an ID, label, options, and default. Apply the [Decision Guide](../prism-new/decision-guide.md) -- the key question is whether each value was a deliberate analytical choice or just the first thing that worked. Filter aggressively: hardcoded values are not decisions unless changing them changes the conclusion.

**Underscore convention:** Decision IDs use underscores (`learning_rate`). Prism passes `--learning_rate`. Name them correctly now.

**Write initial astra.yaml** with `version`, `name`, `description`, `success_criteria`, `inputs`, `outputs`, and `decisions`. No recipes yet.

---

## Phase 4: Scaffold

Stage banner: SCAFFOLDING

### 4a. Initialize

Run `prism init .`. If scripts are in a non-standard location, ask the user: move to `scripts/` or keep in place and reference from recipes.

### 4b. Add Recipes

Extend astra.yaml with `recipe:` blocks on outputs. Use existing script paths. Note (but don't implement) where scripts need parameterization.

### 4c. Validate

```bash
astra universe generate -n baseline -d "Default selections from existing codebase"
astra validate astra.yaml
```

Fix validation errors. Iterate until clean.

### 4d. Populate CLAUDE.md

Read the `CLAUDE.md` created by `prism init`. Replace `## Analysis Context` with:

- **Domain Context**: what the user explained, data characteristics, constraints
- **Key Decisions**: what each controls, its default, where the hardcoded value lives in code
- **Migration Notes**: what scripts need parameterization, file moves made, original structure
- **Implementation Notes**: libraries, data formats, gotchas

---

## Phase 5: Migration Plan

Stage banner: MIGRATION PLAN

For each decision, list which scripts need modification:

```
| Decision       | Script          | Current Code               | Needed Change              |
|----------------|-----------------|----------------------------|----------------------------|
| learning_rate  | train.py:23     | lr = 0.001                 | Accept --learning_rate arg |
| scaling_method | preprocess.py:8 | scaler = StandardScaler()  | Accept --scaling_method    |
```

Also note file moves and container setup needs.

Write the plan to `.claude/migration-plan.md` as a checklist. Add parameterization summary to CLAUDE.md's Implementation Notes so `/prism-build` can discover it.

---

## Done

Stage banner: MIGRATION COMPLETE

Show summary table, then Next Up block:

- **Parameterize your scripts** -- each decision becomes a CLI arg (`--learning_rate`). See `.claude/migration-plan.md`.
- `/clear` then `/prism-build`
- Also available: `/prism-verify`, `/prism-feedback`

Prompt user to `/clear` before implementation. Everything needed is in `astra.yaml`, `CLAUDE.md`, and `.claude/migration-plan.md`.

---

## Restrictions

**You are a migration and specification agent, not an implementation agent.**

- Do NOT modify implementation scripts (beyond moving/copying with user approval)
- ONLY create/modify: `astra.yaml`, `universes/*.yaml`, `CLAUDE.md`, `.claude/migration-plan.md`, directory structure
- Do NOT fabricate or guess what scripts do -- always read and verify
- Preserve all existing files. Never delete without explicit confirmation.

---

## Anti-Patterns

- **Rewriting scripts** -- discover and document; `/prism-build` implements
- **Inventing decisions** -- every candidate must trace to actual code or user input
- **Moving files without asking** -- always get confirmation
- **Bulk decisions** -- filter aggressively; not a decision unless changing it changes the conclusion
- **Skipping inventory** -- thorough scanning prevents missed decisions and outputs
