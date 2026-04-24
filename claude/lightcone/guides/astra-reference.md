# ASTRA Reference

## What an ASTRA Analysis Is

An ASTRA analysis is a structured layer between the code and the paper. It surfaces the inputs a computation depends on, the outputs it produces, and -- critically -- every methodological decision that could plausibly affect the results. The goal is to make the full decision space explicit and machine-readable, so that alternative defensible choices can be systematically explored rather than silently baked in.

An `astra.yaml` spec captures this for a single unit of work. The structure is **self-similar**: a top-level analysis and a nested sub-analysis have exactly the same shape. Everything in this reference applies equally to both.

## astra.yaml Structure

Fields: `name`, `description`, `version`, `authors`, `tags`, `inputs`, `outputs`, `decisions`, `prior_insights`, `findings`, `analyses`, `container`.

```yaml
# Simple analysis -- everything at top level
version: "1.0"
name: "My Analysis"
description: "What this analysis investigates."
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
container: Containerfile
```

## Decisions

A decision is a methodological choice where a different defensible option could plausibly produce a different numerical result. Include it if changing the choice could shift a quantitative outcome -- even modestly. Many small decisions can compound. When in doubt, include it.

**Not decisions -- skip these:**

- **Tooling choices** that produce identical numerical results: programming language, library/framework (PyTorch vs TensorFlow), file format, parallelization strategy, plotting style.
- **Fixed constraints** with no degrees of freedom: "use the data that exists," "satisfy the grant requirements."
- **What to produce** -- decisions control *how* something is computed, not *what* outputs exist. Outputs are fixed by the analysis structure.

**These ARE decisions -- do not skip:**

- Algorithmic choices (MCMC vs optimization, KDE vs histogram, smoothing method)
- Numerical parameters and thresholds (sigma clipping level, bin width, convergence criterion, iteration count)
- Statistical method choices (bootstrap vs analytic errors, Bayesian vs frequentist)
- Data selection criteria (quality cuts, magnitude limits, spatial boundaries)
- Correction and calibration choices (which reddening law, which zero-point, which prior)

### Parameterization

**Every decision must be parameterized in code** -- never hardcode a decision value. Accept all decisions as CLI args.

**Underscore convention:** IDs use underscores in `astra.yaml` (`prior_range`). lightcone-cli passes `--prior_range wide`. Scripts must match: `parser.add_argument('--prior_range')`, **not** `--prior-range`.

### Constraints

- `when: "decision.option"` -- decision only exists given an upstream choice (e.g., `svm_kernel` only exists `when: model.svm`)
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
      # `gpus` is per-node. Multi-node recipes get nodes × gpus total GPUs.
```

Set `container:` at analysis level (all recipes inherit); per-recipe `container:` overrides. Pass either a container image name (e.g., `python:3.12-slim`, `ghcr.io/org/img:latest`) or a path to a Containerfile (e.g., `Containerfile`, `containers/Dockerfile`). The runtime figures out whether to pull or build.

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

## Universe Management

A universe selects one option per decision -- a defensible alternative analysis path. Bug fixes and refactors are normal commits, not universes.

```bash
astra universe generate -n experiment1 -d "Testing hypothesis X"
# Edit universes/experiment1.yaml, then: lc run --universe experiment1
```

**Adding a new decision:** (1) add to `astra.yaml` with options/default/rationale, (2) add parameter to code, (3) add to all existing universe files with default, (4) create new universe, (5) `astra validate astra.yaml`.

## Prior Insights and Findings

Two kinds of insight, distinguished by direction:

- **Prior insights** (`prior_insights:`) — knowledge from outside the analysis that informs decisions. From literature (by DOI) or artifacts from a prior/parent analysis.
- **Findings** (`findings:`) — conclusions from the analysis itself, backed by its own output artifacts.

Both use the same model (id, claim, created_at, evidence). Placement determines direction.

```yaml
prior_insights:
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
    scope: "Context where this applies (optional)"

findings:
  scaling_result:
    id: scaling_result
    claim: "StandardScaler achieves 97% accuracy vs 91% for MinMaxScaler"
    created_at: "2025-02-01T14:00:00"
    evidence:
      - id: e1
        artifact: "accuracy"            # Content selectors optional for artifacts
      - id: e2
        artifact: "model_comparison"
        quote: { type: TextQuoteSelector, exact: "StandardScaler achieved 97% accuracy vs 91% for MinMaxScaler" }
```

Link prior insights to decisions: `options: { layer_norm: { insights: [layer_norm_stability] } }`

Artifact references are validated against declared outputs — `astra validate` flags any `artifact:` that doesn't match an output ID. Literature evidence requires at least one content selector (quote, figure, or table); artifact evidence does not.

**Sub-analysis findings as prior insights:** When a sub-analysis explores a specific question (calibration study, simulation validation, sensitivity test), its findings can inform decisions elsewhere. The parent or sibling references the sub-analysis output as artifact evidence in its own `prior_insights`, e.g. `artifact: "build_mocks.noise_diagnostics"`. This creates a traceable chain from sub-analysis conclusion to downstream decision.

## Sub-Analyses

### What a Sub-Analysis Is

Each `astra.yaml` -- root or nested -- represents a **unit of work**: meaningful inputs, methodological decisions, meaningful outputs. A sub-analysis is one of these units nested inside a larger analysis. It can be understood, executed, and evaluated on its own terms.

### When to Split

Default to a **single analysis**. Split into sub-analyses only when:

- **Decision ownership** -- the stage has its own decisions that could meaningfully vary, clearly scoped to that stage rather than the broader analysis. Shared decisions live at the parent (`from: ../`); stage-specific decisions live in the sub-analysis. If you can't cleanly assign decisions to levels, the split is probably wrong.
- **Reusability** -- someone working on a different paper could use this stage's output as-is (a cleaned catalog, a trained emulator, a set of mocks).
- **Side quests** -- independent investigations (diagnostics, calibrations, simulation studies) that have different inputs/outputs/code from the main analysis are sub-analyses, not universes. Universes are different parameter choices on the same pipeline.
- **If boundaries are unclear**, start flat and split later when they become explicit: separate stage outputs, explicit `from` links, clear decision ownership per level.

### Worked Examples

#### Two-Stage Pipeline (DAG Split)

A paper builds mock galaxy catalogs, then trains a neural network on them for photometric redshift estimation. Natural split:
- **`build_mocks`**: simulation inputs + survey properties, decisions about noise model and selection function. Produces mock catalogs.
- **`photo_z`**: mocks (from sibling) + real survey data, decisions about network architecture and training. Produces redshift estimates.

The mock-building decisions are independent from training decisions. Someone could reuse the mocks for a different estimator.

#### When NOT to Split

A paper downloads galaxies, applies quality cuts, corrects for extinction, computes luminosity functions, fits a Schechter function. Five steps -- but one objective, shared decisions, one end product. 

### Anti-Patterns

- **Splitting by script** rather than by analytical unit.
- **Zero-decision sub-analyses** that just pass data through -- make these output recipes in the parent.
- **Premature splitting.** Start flat, split when boundaries become explicit. Easier to split a working flat analysis than merge a broken hierarchical one.
- **Forcing a linear DAG.** Independent stages don't need to be wired in sequence just because the paper presents them that way.

### Composition Mechanics

Each sub-analysis lives in its own directory with its own `astra.yaml`. The parent lists them with `path:` references:

```yaml
# Root astra.yaml
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
    path: ./analyses/build_mocks
  train_network:
    path: ./analyses/train_network
```

Inside each sub-analysis's own `astra.yaml`, `from:` wires inputs and decisions to the parent or siblings:

```yaml
# analyses/train_network/astra.yaml
inputs:
  - id: training_data
    type: data
    from: build_mocks.mock_catalog       # Sibling output
outputs:
  - id: trained_model
    type: data
    recipe: { command: python src/train.py, resources: { gpus: 1, memory: "32GB" } }
decisions:
  cosmology_model:
    from: ../cosmology_model             # Inherit parent decision
  noise_model:
    label: "Noise Model"
    default: heteroscedastic
    options:
      homoscedastic: { label: "Homoscedastic" }
      heteroscedastic: { label: "Heteroscedastic" }
```

**Wiring patterns:**
- **Input `from:`** -- `from: parent_input_id` (parent input) or `from: sibling_id.output_id` (sibling output).
- **Decision `from: ../parent_id`** -- inherits a parent decision. The sub-analysis uses the parent's value; do not set it in the sub-analysis universe.
- **Output `from: sub.output`** at root level creates an alias to a sub-analysis output.
- **`universe:` field** in universe files selects which sub-analysis universe to load: `build_mocks: { universe: baseline }` loads `./analyses/build_mocks/universes/baseline.yaml`.

## CLI Reference (astra)

```bash
astra validate astra.yaml                       # Validate (run after every change)
astra validate astra.yaml --verify-evidence     # + verify insight quotes against PDFs
astra info [--decisions]                      # Analysis summary / decision details
astra universe generate -n NAME [-d "desc"]   # Generate universe from defaults
astra universe check universes/x.yaml         # Check universe constraints
astra viz                                     # Visualize decision space
astra schema show analysis                    # Show JSON schema
```

## Validation

Run `astra validate astra.yaml` after **every** spec change. Additional checks:
- Universe files: `astra universe check universes/<name>.yaml`
- Evidence quotes: `astra validate astra.yaml --verify-evidence`
