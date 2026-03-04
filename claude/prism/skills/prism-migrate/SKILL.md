---
name: prism-migrate
description: Migrate an existing project into the ASTRA/Prism framework. Walks through a guided migration that identifies existing scripts, data, configs, and decisions, then scaffolds the ASTRA structure around them. Use when the user has existing analysis code they want to bring into ASTRA. Triggers on "migrate", "convert", "port", "import project", "existing project".
allowed-tools: Read, Write(astra.yaml), Write(universes/*), Write(CLAUDE.md), Write(.claude/migration-plan.md), Edit(astra.yaml), Edit(universes/*), Edit(CLAUDE.md), Glob, Grep, Bash(astra:*), Bash(prism:*), Bash(mkdir:*), Bash(echo:*), Bash(ls:*), Bash(cp:*), Bash(mv:*), Bash(git:*), Bash(python:*), WebSearch, WebFetch, AskUserQuestion, Task
argument-hint: "[path | github-url]"
---

# /prism-migrate

Migrate an existing project into the ASTRA/Prism framework. Unlike `/prism-new` which starts from scratch, this skill discovers what already exists and wraps it in ASTRA structure -- preserving the user's work while adding specification, decision tracking, and reproducibility.

## References

- [Prism Reference](./../prism/SKILL.md) -- core concepts, CLI, validation
- [Decision Guide](../prism-new/decision-guide.md) -- decision identification, prioritization, blind-spot checklist
- [UI Brand](../ui-brand.md) -- visual formatting patterns
- [CLAUDE.md Template](../../templates/CLAUDE.md) -- project CLAUDE.md structure

## Setup

1. Read `astra.yaml` if it exists (abort if already a fully-formed ASTRA project)
2. Note the working directory

---

## Phase 1: Locate the Project

Stage banner: PROJECT DISCOVERY

**Ask the user where their existing project lives.**

**What would you like to migrate? I can work with:**
- A local directory (path)
- A GitHub repo URL
- A paper with associated code
- Files already in this directory

If the user provides a GitHub URL, clone it into the current directory (or a subdirectory if the current directory is not empty). If they point to a local directory elsewhere, ask whether to migrate in-place or copy.

**Determine project type.** Scan the provided location and classify:

| Signal | Project Type |
|--------|-------------|
| `*.py` files, `setup.py`/`pyproject.toml` | Python project |
| `*.R`/`*.Rmd` files, `DESCRIPTION` | R project |
| `*.ipynb` files | Notebook-based project |
| `Makefile`, `Snakefile`, `nextflow.config` | Pipeline project |
| Paper PDF + code directory | Paper-with-code |
| Mixed or unclear | Ask the user |

Report what you found:

```
Found: Python project
  12 Python scripts in src/
  3 Jupyter notebooks in notebooks/
  requirements.txt (47 dependencies)
  data/ directory (4 CSV files)
  No existing config management
```

Ask the user to confirm or correct the classification.

---

## Phase 2: Inventory

Stage banner: INVENTORY

Systematically scan the project. For each category, list what you find and what it maps to in ASTRA.

### 2a. Scripts & Entry Points

Find executable scripts, main functions, and notebook cells that produce outputs. For each, identify:
- What it does (brief description)
- What it reads (inputs)
- What it writes (outputs)
- What parameters/constants it uses (candidate decisions)

Present as a table:

```
| Script              | Purpose           | Inputs         | Outputs          | Parameters      |
|---------------------|-------------------|----------------|------------------|-----------------|
| train.py            | Train model       | data/train.csv | models/best.pt   | lr, epochs, ... |
| evaluate.py         | Evaluate model    | models/best.pt | results/acc.json | threshold       |
| plot_results.py     | Generate figures  | results/*.json | figs/fig1.png    | --              |
```

### 2b. Data & Inputs

Find data files, external data sources, and configuration files that serve as inputs. Classify each:
- `type: data` -- datasets, model weights, external resources
- `type: parameter` -- config files, hyperparameter files
- `type: reference` -- papers, documentation

### 2c. Outputs & Results

Find files that are produced by the scripts. Classify each:
- `type: metric` -- JSON files with numeric values
- `type: figure` -- PNG/PDF plots
- `type: table` -- CSV/Excel result tables
- `type: data` -- processed datasets, model artifacts
- `type: report` -- markdown/text summaries

### 2d. Hardcoded Decisions

Scan scripts for hardcoded values that represent analytical choices. Look for:
- Magic numbers (thresholds, hyperparameters, constants)
- Commented alternatives ("# could also use...")
- Config dictionaries or argument parsers
- Conditional branches that select methods

For each candidate, note the file, line, current value, and what it controls.

### 2e. Dependencies

Find dependency files:
- `requirements.txt`, `setup.py`, `pyproject.toml` (Python)
- `DESCRIPTION`, `renv.lock` (R)
- `environment.yml` (Conda)
- `Dockerfile`, `Containerfile`

### Review

Present the full inventory to the user via `AskUserQuestion`:

**Here's what I found in your project. Please review:**
[inventory summary]
**Anything missing or miscategorized? Any scripts I should ignore?**

Incorporate corrections before proceeding.

---

## Phase 3: Research Question & Structure

Stage banner: ANALYSIS STRUCTURE

Unlike `/prism-new`, the project already exists -- so the research question and structure are derived from what the code does, not from conversation alone.

### 3a. Research Question

Propose a research question, description, and success criteria based on the inventory:

**Based on your code, it looks like this analysis is about:** [proposed description]
**Does this capture it? What would you refine?**

### 3b. Analysis Boundaries

Determine whether this is a single analysis or multi-stage. Apply the same criteria as `/prism-new`:
- Split only if stages are genuinely standalone units with own objectives, artifacts, and decisions.
- Default to single analysis.

Ask the user to confirm.

### 3c. Decision Identification

Present the hardcoded values discovered in Phase 2d as candidate decisions. For each:
- Propose an ID, label, and options
- Note the current hardcoded value as the default
- Ask if there are other defensible alternatives

Apply the [Decision Guide](../prism-new/decision-guide.md). For migrated code, the key question is whether each hardcoded value was a deliberate analytical choice or simply the first thing that worked. Filter out non-decisions (fixed requirements, implementation details, obvious best practices).

**Write initial astra.yaml** with `version`, `name`, `description`, `success_criteria`, `inputs`, `outputs`, and `decisions`. Insights are not populated during migration -- add them manually or via `/prism-new` deep dive later.

---

## Phase 4: Scaffold

Stage banner: SCAFFOLDING

Create the ASTRA/Prism directory structure around the existing project.

### 4a. Initialize Structure

Run `prism init .` if not already initialized, or manually create missing directories:

```
universes/
scripts/        (may already exist)
results/
```

If the project has scripts in a non-standard location (e.g., `src/`, `code/`, `notebooks/`), ask the user whether to:
1. Move/copy them to `scripts/`
2. Keep them in place and reference them from recipes

### 4b. Write astra.yaml

Create the full `astra.yaml` from the information gathered. Include:
- All identified inputs with sources
- All identified outputs with types
- All confirmed decisions with options and defaults
- Recipe stubs pointing to existing scripts

For recipe commands, use the existing script paths. If scripts need modification to accept decision parameters, note this but do not modify them yet.

```yaml
outputs:
  - id: accuracy
    type: metric
    recipe:
      command: python scripts/evaluate.py   # existing script
```

### 4c. Generate Baseline Universe

```bash
astra universe generate -n baseline -d "Default selections from existing codebase"
```

### 4d. Validate

```bash
astra validate astra.yaml
```

Fix any validation errors. Iterate until clean.

### 4e. Populate CLAUDE.md

Read the existing `CLAUDE.md` (or create from template if missing). Replace the `## Analysis Details` section with project-specific content:

- **Description**: from astra.yaml
- **Structure**: for each section, list decision IDs with labels and output IDs
- **Key Decisions**: what each controls, its default, and where the hardcoded value currently lives in code
- **Migration Notes**: what scripts need parameterization, what file moves were made, what the original project structure looked like
- **Implementation Notes**: domain-specific guidance -- libraries, data formats, existing tests

---

## Phase 5: Migration Plan

Stage banner: MIGRATION PLAN

Create a concrete plan for completing the migration. This addresses the gap between "ASTRA structure exists" and "fully parameterized, reproducible analysis."

### 5a. Parameterization Needed

For each decision, list which scripts need modification and what the change looks like:

```
| Decision        | Script          | Current Code                    | Needed Change              |
|-----------------|-----------------|--------------------------------|----------------------------|
| learning_rate   | train.py:23     | lr = 0.001                     | Accept --learning_rate arg |
| scaling_method  | preprocess.py:8 | scaler = StandardScaler()      | Accept --scaling_method    |
```

### 5b. File Organization

If any files need to be moved or renamed to match conventions, list them.

### 5c. Container Setup

If the project has a Dockerfile, note it. If not, recommend creating a `Containerfile` based on the dependency files found.

### 5d. Write Plan

Write the migration plan to `.claude/migration-plan.md` as a markdown checklist. Also add a summary of parameterization work to CLAUDE.md's Implementation Notes section so `/prism-build` can discover it.

---

## Done

Stage banner: MIGRATION COMPLETE

Show summary table:

```
| Category         | Count | Status         |
|------------------|-------|----------------|
| Inputs           | 3     | Declared       |
| Outputs          | 5     | Declared       |
| Decisions        | 4     | Need parameterization |
| Scripts          | 6     | Mapped to recipes |
| Universes        | 1     | baseline       |
| Validation       | ✓     | Passing        |
```

Then show a Next Up block (see ui-brand.md):

---
**Next up**

**Parameterize your scripts** -- Each decision must become a CLI argument (underscore convention: `--learning_rate`). See `.claude/migration-plan.md` for the full checklist.

`/clear` then `/prism-build`

Also available: `/prism-verify`

---

Prompt the user to `/clear` before starting implementation. The migration conversation consumes significant context. Everything needed to continue is captured in `astra.yaml`, `CLAUDE.md`, and `.claude/migration-plan.md`.

---

## Restrictions

**You are a migration and specification agent, not an implementation agent.**

You MUST NOT modify existing Python, R, or other implementation scripts (beyond moving/copying files with user approval).

You MUST ONLY create/modify: `astra.yaml`, `universes/*.yaml`, `CLAUDE.md` (Phase 4 only), `.claude/migration-plan.md`, and directory structure.

You MUST NOT fabricate or guess what scripts do -- always read and verify.

You MUST preserve all existing project files. Never delete or overwrite the user's code without explicit confirmation.

---

## Anti-Patterns

- **Rewriting scripts** -- This skill discovers and documents; `/prism-build` implements changes
- **Inventing decisions** -- Every candidate decision must trace back to actual code or user input
- **Moving files without asking** -- Always get user confirmation before reorganizing
- **Ignoring existing structure** -- Work with the project's layout, not against it
- **Skipping inventory** -- Thorough scanning prevents missed decisions and outputs
- **Assuming single language** -- Projects may mix Python, R, shell scripts, notebooks
- **Bulk decisions** -- Filter aggressively. Hardcoded values are not decisions unless changing them changes the conclusion
