# CLAUDE.md

## Project: {{name}}

ASTRA (Agentic Schema for Transparent Research Analysis) analysis project, built with Prism.

### Skill Commands

| Command | Purpose |
|---------|---------|
| `/prism-new` | Scope question, structure decisions, integrate literature |
| `/prism-migrate` | Migrate an existing project into ASTRA/Prism framework |
| `/prism-build` | Build loop -- spec to materialized results |
| `/prism-verify` | Verify results, decision-code alignment, success criteria |
| `/prism-feedback` | File a bug report from the current session |

**Workflow:** `/prism-new` or `/prism-migrate` --> `/prism-build` --> `/prism-verify`

### Source of Truth

- `astra.yaml` -- The analysis specification. Read this first.
- `universes/` -- Decision selections (one YAML per universe).

### Project Layout

```
astra.yaml              # Specification: decisions, inputs, outputs
prism.yaml            # Prism config (default target, etc.)
CLAUDE.md             # This file
Containerfile         # Container image for execution
requirements.txt      # Python deps (keep in sync with scripts)
universes/
  baseline.yaml       # Default decision selections
scripts/              # Implementation scripts
results/<universe>/   # Outputs by universe (produced by prism run)
```

### Development Workflow

Three overlapping phases:

1. **Write & Debug** -- Run scripts directly (`python scripts/compute.py`) to iterate. Write them recipe-ready from the start: parameterize decisions, write to convention paths, one script per output.
2. **Integrate** -- Add `recipe:` blocks to outputs in `astra.yaml`. Track with `prism status` (`no recipe` / `pending` / `ok`). Container build specs (Containerfile or image string) can be set at the analysis level or per-recipe.
3. **Materialize** -- `prism run` executes via Dagster in containers (Docker or SLURM). Falls back to local execution if Docker is unavailable. Done when `prism status` shows all `ok`.

**An output is not done until `prism run` produces it.** Running scripts directly is for debugging only — final results must always come from `prism run` so they are reproducible inside containers.

### Spec-Code Invariant

**`astra.yaml` must always reflect the code and vice versa.** When you change one, update the other immediately:
- Add a decision to code? Add it to `astra.yaml` and all universe files.
- Add an output or change a script? Update the `recipe:` block in `astra.yaml`.
- Remove or rename something? Update both sides and run `astra validate astra.yaml`.

### astra.yaml Structure

The spec is **self-similar** -- every level (top or sub-analysis) has the same fields: `name`, `description`, `version`, `authors`, `tags`, `inputs`, `outputs`, `decisions`, `insights`, `analyses`, `container`, `success_criteria`.

#### When to Create Sub-Analyses

Default to a **single analysis**. Split into sub-analyses only when a stage is a true standalone unit with:

1. its own objective (can be evaluated on its own),
2. its own output artifact (a meaningful data product, not just an internal intermediate),
3. decisions that are meaningfully scoped to that stage and can vary independently.

Do **not** split just because the workflow has multiple implementation steps. If all steps jointly serve one objective and one end product, keep it as one analysis.

If boundaries are unclear, start single-analysis and split later only when boundaries become explicit in `astra.yaml`:
- separate stage outputs,
- explicit `from` links between stages,
- clear decision ownership (shared at top-level vs stage-specific under each sub-analysis).

```yaml
# Simple analysis -- everything at top level
version: "1.0"
name: "My Analysis"
description: "What this analysis investigates."
success_criteria:
  - claim: "Achieve >95% accuracy on held-out test set"
    output: accuracy
    condition: "value > 0.95"
inputs:
  - id: training_data
    type: data
    source: "data/train.csv"
decisions:
  scaling:
    label: "Feature Scaling"
    tags: [preprocessing]        # optional freeform tags for grouping
    rationale: "Affects convergence"
    default: standard
    options:
      standard: { label: "StandardScaler" }
      minmax: { label: "MinMaxScaler" }
  use_pca:
    label: "Use PCA"
    default: "no"
    options:
      "yes": { label: "Yes" }
      "no": { label: "No" }
  n_components:
    label: "PCA Components"
    when: use_pca.yes             # only exists when use_pca=yes
    default: "50"
    options:
      "50": { label: "50 components" }
      "100": { label: "100 components" }
outputs:
  - id: accuracy
    type: metric
    recipe:
      command: python scripts/evaluate.py
container:
  build: Containerfile
```

```yaml
# Multi-stage -- sub-analyses with cross-references
decisions:
  cosmology_model:               # Shared across stages
    label: "Cosmological Model"
    tags: [physics]
    default: flat_lcdm
    options:
      flat_lcdm: { label: "Flat LCDM" }
      wcdm: { label: "wCDM" }
analyses:
  build_mocks:
    inputs:
      - { id: survey_data, type: data, from: survey_catalog }   # Parent input
    outputs:
      - id: mock_catalog
        type: data
        recipe: { command: python src/generate_mocks.py }
    decisions:
      noise_model:
        label: "Noise Model"
        tags: [simulation]
        default: heteroscedastic
        options:
          homoscedastic: { label: "Homoscedastic" }
          heteroscedastic: { label: "Heteroscedastic" }
  train_network:
    inputs:
      - { id: training_data, type: data, from: build_mocks.mock_catalog }  # Sibling output
    outputs:
      - id: trained_model
        type: data
        recipe: { command: python src/train.py, resources: { gpus: 1, memory: "32GB" } }
```

### Decision Parameterization

**Every decision must be parameterized in code** -- never hardcode a decision value. Accept all decisions as CLI args.

**Underscore convention:** IDs use underscores in `astra.yaml` (`prior_range`). Prism passes `--prior_range wide`. Scripts must match: `parser.add_argument('--prior_range')`, **not** `--prior-range`.

### Decision Constraints

- `incompatible_with: ["decision.option"]` -- cannot coexist in a universe
- `requires: ["decision.option"]` -- must be selected together
- `when: decision.option` -- conditional decision, only exists when that option is selected (see `n_components` example above)
- `excluded: true` + `excluded_reason: "..."` -- option considered but rejected (cannot be default or selected)

### Writing Results

Convention path: `results/<universe_id>/<output_id>.<ext>` -- no `path` field needed.

- `metric` -- JSON (`{"value": 0.95}`)
- `figure` -- PNG
- `table` -- CSV
- `data` -- Parquet/HDF5
- `report` -- Markdown

### Recipe Format

Inline on outputs. Fields: `command` (required), `inputs`, `container`, `resources`.

```yaml
outputs:
  - id: accuracy
    type: metric
    recipe:
      command: python scripts/evaluate.py
      inputs: [trained_model]               # Dependency on other output
      container: ghcr.io/proj/ml:latest     # Overrides analysis-level default
      resources: { cpus: 4, memory: "32GB", gpus: 1, time_limit: "2h" }
```

Set `container:` at analysis level (all recipes inherit); per-recipe `container:` overrides. Accepts a build spec (`{ build: Containerfile }`) or image string (`"python:3.12-slim"`).

### CLI Reference

```bash
# astra -- spec operations
astra validate astra.yaml                       # Validate (run after every change)
astra validate astra.yaml --verify-evidence     # + verify insight quotes against PDFs
astra info [--decisions]                      # Analysis summary / decision details
astra universe generate -n NAME [-d "desc"]   # Generate universe from defaults
astra universe check universes/x.yaml         # Check universe constraints
astra viz                                     # Visualize decision space
astra schema show analysis                    # Show JSON schema

# prism -- execution operations
prism run [OUTPUT] [--universe NAME]        # Execute recipes via Dagster (auto-builds)
prism run --target perlmutter-gpu           # Execute on a specific SLURM target
prism run --no-build                        # Skip automatic container builds
prism build [--force] [--runtime docker]    # Build container images from specs
prism status [--universe NAME]              # Materialization + container status
prism dev [--port 3000]                     # Dagster webserver UI
prism target [--set NAME] [--list]          # Manage execution targets
prism setup                                 # Interactive target setup wizard
```

### Universe Management

A universe selects one option per decision -- a defensible alternative analysis path. Bug fixes and refactors are normal commits, not universes.

```bash
astra universe generate -n experiment1 -d "Testing hypothesis X"
# Edit universes/experiment1.yaml, then: prism run --universe experiment1
```

**Adding a new decision:** (1) add to `astra.yaml` with options/default/rationale, (2) add parameter to code, (3) add to all existing universe files with default, (4) create new universe, (5) `astra validate astra.yaml`.

### Status Interpretation

`prism status` shows outputs vs universes. **Progression:** `no recipe` --> `pending` --> `ok`

- `ok` -- Recipe exists, results on disk. Done.
- `pending` -- Recipe exists, not materialized. Run `prism run`.
- `no recipe` -- No `recipe:` block yet. Still in Write & Debug phase.

Container status: `prebuilt: image`, `build: Containerfile (built)`, or `(not built)` (needs `prism build`).

### Insights Format

Insights link evidence to decisions using W3C selectors:

```yaml
insights:
  layer_norm_stability:
    id: layer_norm_stability
    claim: "Layer normalization improves training stability"
    created_at: "2025-01-15T10:30:00"
    evidence:
      - id: e1
        doi: "10.48550/arXiv.1607.06450"
        quote: { type: TextQuoteSelector, exact: "Exact text", prefix: "~20-100 chars before", suffix: "~20-100 chars after" }
        location: { type: FragmentSelector, page: 5 }
      - id: e2
        doi: "10.48550/arXiv.1607.06450"
        figure: { type: FigureSelector, label: "Figure 3a", caption: "..." }
      - id: e3
        doi: "10.48550/arXiv.1607.06450"
        table: { type: TableSelector, label: "Table 1", region: "row 3, col 2" }
    scope: "Context where this applies (optional)"
```

Link to decisions: `options: { layer_norm: { insights: [layer_norm_stability] } }`

Literature is integrated into `/prism-new` during scoping.

Artifacts (computed outputs) use `artifact:` instead of `doi:`:

```yaml
evidence:
  - id: e1
    artifact: "accuracy"
    quote:
      exact: "StandardScaler achieved 97% accuracy vs 91% for MinMaxScaler"
```

### Failure Diagnosis

- **Script arg not recognized** -- Use underscores in argparse to match decision IDs
- **Recipe input not found** -- Materialize upstream outputs first

After failure: fix, then `prism run <output_id> --universe <name>`.

### Validation

Run `astra validate astra.yaml` after **every** spec change. Additional checks:
- Universe files: `astra universe check universes/<name>.yaml`
- Evidence quotes: `astra validate astra.yaml --verify-evidence`

---

<!-- AUTOGENERATED: /prism-new populates below during specification -->
## Analysis Context

_Run `/prism-new` to scope the research question and populate this section with domain context and implementation notes not captured in astra.yaml._
