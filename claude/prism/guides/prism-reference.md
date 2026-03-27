# Prism & ASTRA Reference

Comprehensive reference for astra.yaml structure, decision parameterization, recipes, CLI commands, and universe management. Consult this guide when working with ASTRA specs or Prism execution.

## astra.yaml Structure

The spec is **self-similar** -- every level (top or sub-analysis) has the same fields: `name`, `description`, `version`, `authors`, `tags`, `inputs`, `outputs`, `decisions`, `insights`, `analyses`, `container`, `success_criteria`.

### When to Create Sub-Analyses

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
inputs:
  - id: survey_catalog
    type: data
    source: "data/survey.parquet"
decisions:
  cosmology_model:               # Shared across stages
    label: "Cosmological Model"
    tags: [physics]
    default: flat_lcdm
    options:
      flat_lcdm: { label: "Flat LCDM" }
      wcdm: { label: "wCDM" }
outputs:
  - id: trained_model
    type: data
    from: train_network.trained_model   # Alias -- produced by sub-analysis
analyses:
  build_mocks:
    path: ./analyses/build_mocks        # External sub-analysis (loads its own astra.yaml)
  train_network:
    inputs:
      - { id: training_data, type: data, from: build_mocks.mock_catalog }  # Sibling output
    outputs:
      - id: trained_model
        type: data
        recipe: { command: python src/train.py, resources: { gpus: 1, memory: "32GB" } }
    decisions:
      cosmology_model:
        from: ../cosmology_model        # Inherit parent decision (value comes from universe)
      noise_model:
        label: "Noise Model"
        default: heteroscedastic
        options:
          homoscedastic: { label: "Homoscedastic" }
          heteroscedastic: { label: "Heteroscedastic" }
```

### Sub-Analysis Composition

- **`path:`** on a sub-analysis loads content from `<path>/astra.yaml`. Mutually exclusive with inline fields -- a `path:` entry is a stub only.
- **Input `from:`** wires sub-analysis inputs. Two patterns: `from: parent_input_id` (parent input) or `from: sibling_id.output_id` (another sub-analysis's output).
- **Decision `from: ../parent_id`** inherits a parent decision. The sub-analysis uses the parent's value -- do not set it in the sub-analysis universe.
- **Output `from: sub.output`** at root level creates an alias to a sub-analysis output.
- **`universe:` field** in universe files selects which sub-analysis universe to load: `build_mocks: { universe: baseline }` loads `./analyses/build_mocks/universes/baseline.yaml`.

## Decision Parameterization

**Every decision must be parameterized in code** -- never hardcode a decision value. Accept all decisions as CLI args.

**Underscore convention:** IDs use underscores in `astra.yaml` (`prior_range`). Prism passes `--prior_range wide`. Scripts must match: `parser.add_argument('--prior_range')`, **not** `--prior-range`.

## Decision Constraints

- `incompatible_with: ["decision.option"]` -- cannot coexist in a universe
- `requires: ["decision.option"]` -- must be selected together
- `excluded: true` + `excluded_reason: "..."` -- option considered but rejected (cannot be default or selected)

## Writing Results

Convention path: `results/<universe_id>/<output_id>.<ext>` -- no `path` field needed.

- `metric` -- JSON (`{"value": 0.95}`)
- `figure` -- PNG
- `table` -- CSV
- `data` -- Parquet/HDF5
- `report` -- Markdown

## Recipe Format

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

### Conditional Outputs

Outputs can have `when` conditions -- the output only exists when the condition is met for a given universe. Uses the same syntax as decision `when` (negation with `~`, lists AND'd).

```yaml
outputs:
  - id: faint_metrics
    type: metric
    when: "~training_sample.bright_only"          # Only when NOT bright_only
    recipe: { command: python scripts/evaluate.py }
  - id: combined_report
    type: report
    when: ["~training_sample.bright_only", model.svm]  # AND: both must be true
    recipe: { command: python scripts/combo.py }
```

## CLI Reference

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
prism init [DIR] --sub-analysis             # Scaffold sub-analysis under analyses/
prism run [OUTPUT] [--universe NAME]        # Execute recipes via Dagster (auto-builds)
prism run --partition gpu --qos shared      # Unknown flags passed through to SLURM
prism run --no-build                        # Skip automatic container builds
prism build [--force] [--runtime docker]    # Build container images from specs
prism status [--universe NAME]              # Materialization + container status
prism dev [--port 3000]                     # Dagster webserver UI
prism target [--set NAME] [--list]          # Manage execution targets
prism setup                                 # Interactive target setup wizard
```

## Universe Management

A universe selects one option per decision -- a defensible alternative analysis path. Bug fixes and refactors are normal commits, not universes.

```bash
astra universe generate -n experiment1 -d "Testing hypothesis X"
# Edit universes/experiment1.yaml, then: prism run --universe experiment1
```

**Adding a new decision:** (1) add to `astra.yaml` with options/default/rationale, (2) add parameter to code, (3) add to all existing universe files with default, (4) create new universe, (5) `astra validate astra.yaml`.

## Status Interpretation

`prism status` shows outputs vs universes. **Progression:** `no recipe` --> `pending` --> `ok`

- `ok` -- Recipe exists, results on disk. Done.
- `pending` -- Recipe exists, not materialized. Run `prism run`.
- `no recipe` -- No `recipe:` block yet. Still in Write & Debug phase.

Container status: `prebuilt: image`, `build: Containerfile (built)`, or `(not built)` (needs `prism build`).

## Insights Format

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

## Failure Diagnosis

- **Script arg not recognized** -- Use underscores in argparse to match decision IDs
- **Recipe input not found** -- Materialize upstream outputs first

After failure: fix, then `prism run <output_id> --universe <name>`.

## Validation

Run `astra validate astra.yaml` after **every** spec change. Additional checks:
- Universe files: `astra universe check universes/<name>.yaml`
- Evidence quotes: `astra validate astra.yaml --verify-evidence`
