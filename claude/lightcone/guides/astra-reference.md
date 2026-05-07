# ASTRA Reference

## What an ASTRA Analysis Is

An ASTRA analysis is a structured layer between the code and the paper. It surfaces the inputs a computation depends on, the outputs it produces, and -- critically -- every methodological decision that could plausibly affect the results. The goal is to make the full decision space explicit and machine-readable, so that alternative defensible choices can be systematically explored rather than silently baked in.

An `astra.yaml` spec captures this for a single unit of work. The structure is **self-similar**: a top-level analysis and a nested sub-analysis have exactly the same shape. Everything in this reference applies equally to both.

## astra.yaml Structure

Fields: `id`, `version`, `name`, `narrative`, `authors`, `tags`, `inputs`, `outputs`, `decisions`, `prior_insights`, `findings`, `analyses`, `container`. `narrative` is the analysis-level prose field -- see [Narrative](#narrative) (typically filled in later, once the structural pieces have settled).

**Reserved IDs.** No analysis entity (input, output, decision, option, finding, prior insight, evidence, sub-analysis) may use any of these names as its `id` -- they collide with the narrative anchor grammar:

```
inputs   outputs   decisions   findings   prior_insights
analyses options   content     narrative
```

**`label` field.** Inputs, Outputs, Decisions, Options, and Insights all accept an optional `label:` -- a short human-readable name for compact rendering (margin glyphs, breadcrumbs, card titles). Tooling falls back to `id` when absent. `label` is required only on Options.

```yaml
# Simple analysis -- everything at top level
version: "1.0"
name: "My Analysis"
# narrative: { ... }  # see Narrative section; typically added later
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
    inputs: [training_data]                                  # provenance lives on the Output
    decisions: [scaling, use_pca, n_components]              # not on the recipe
    recipe:
      command: >-
        python src/evaluate.py
        --train {inputs.training_data}
        --scaling {decisions.scaling}
        --out {output}
container: Containerfile
```

### Cross-Analysis Inputs

To consume the outputs of a separate ASTRA analysis as a whole-cloth dependency, declare an Input of `type: analysis` with `ref:` (and optionally `ref_version:` and `use_outputs:`):

```yaml
inputs:
  - id: prior_study
    type: analysis
    ref: analyses/preprocessing_comparison
    ref_version: "v1.2"
    use_outputs: [best_method, performance_table]
```

This is distinct from `from:` -- `ref` points to an external analysis by reference; `from:` aliases an element within the current analysis tree (see [Composition Mechanics](#composition-mechanics)).

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

### Tags

Decisions may carry an optional `tags:` list for grouping (e.g. `[preprocessing]`, `[physics]`, `[stats]`). Keep the tag vocabulary **small and consolidated** -- reuse existing tags rather than minting new ones, since tags are mostly useful for cross-cutting views over a shared decision space, and that view fragments quickly when every decision invents its own label.

### Parameterization

**Every decision must be parameterized in code** -- never hardcode a decision value. The recipe's `command:` template references it via `{decisions.<id>}` (see [Command Template Substitution](#command-template-substitution)).

### Constraints

- `when: "decision.option"` -- decision only exists given an upstream choice (e.g., `svm_kernel` only exists `when: model.svm`)
- `incompatible_with: ["decision.option"]` -- cannot coexist in a universe
- `requires: ["decision.option"]` -- must be selected together
- `excluded: true` + `excluded_reason: "..."` -- option considered but rejected (cannot be default or selected)

## Recipe Format

ASTRA is asset-centric: the **Output** declares its provenance (`inputs`, `decisions`) and when it's active (`when`); the recipe is pure *how*. Recipe fields: `command` (required), `container`, `resources`.

```yaml
outputs:
  - id: accuracy
    type: metric
    inputs: [trained_model]                 # Dependencies live on the Output
    decisions: [scaling, classifier]        # Decisions that parameterize this output
    recipe:
      command: >-
        python src/evaluate.py
        --model {inputs.trained_model}
        --scaling {decisions.scaling}
        --out {output}
      container: ghcr.io/proj/ml:latest     # Overrides analysis-level default
      resources: { cpus: 4, memory: "32GB", gpus: 1, time_limit: "2h" }
```

Set `container:` at analysis level (all recipes inherit); per-recipe `container:` overrides. Pass either a container image name (e.g., `python:3.12-slim`, `ghcr.io/org/img:latest`) or a path to a Containerfile (e.g., `Containerfile`, `containers/Dockerfile`). The runtime figures out whether to pull or build.

### Command Template Substitution

Runners expand `{...}` placeholders in `command:` before invoking it: `{inputs.<id>}` (input path), `{inputs}` (all input paths, declared order), `{decisions.<id>}` (active option ID), `{output}` (artifact path), `{{`/`}}` (literal braces). Every `{inputs.<id>}` and `{decisions.<id>}` must name something declared in the parent Output's `inputs:`/`decisions:` lists -- always **local IDs** (no `../`; bridging is declared once at the Input/Decision via `from:`).

Text outside `{...}` is literal command text and isn't validated. Static constants (`--max-iter 1000`), per-output specialisations when fan-out is unrolled into one Output per value (`--tracer lrg1`), and shell features (`${VAR}`, pipes, redirects) all live as plain text. Only values that vary across the multiverse need to be `{decisions.<id>}` placeholders -- there is no separate `params` channel, and Snakemake-style wildcards (`{chunk_id}`, `{block_i}`) have no spec-level analogue: either inline the value, unroll the fan-out into one Output per value, or describe only the aggregated artifact.

### Conditional Outputs

Outputs can have `when` conditions -- the output only exists when the condition is met for a given universe. Uses the same syntax as decision `when` (negation with `~`, lists AND'd).

```yaml
outputs:
  - id: faint_metrics
    type: metric
    when: "~training_sample.bright_only"          # Only when NOT bright_only
    recipe: { command: python src/evaluate.py }
  - id: combined_report
    type: report
    when: ["~training_sample.bright_only", model.svm]  # AND: both must be true
    recipe: { command: python src/combo.py }
```

## Universe Management

A universe selects one option per decision -- a defensible alternative analysis path. Bug fixes and refactors are normal commits, not universes. Universe IDs use the pattern `^[a-z][a-z0-9_-]*$` (hyphens allowed, unlike other ASTRA IDs).

```bash
astra universe generate -n experiment1 -d "Testing hypothesis X"
# Edit universes/experiment1.yaml, then run with the runner of your choice.
```

**Adding a new decision:** (1) add to `astra.yaml` with options/default/rationale, (2) add parameter to code, (3) add to all existing universe files with default, (4) create new universe, (5) `astra validate astra.yaml`.

## Prior Insights and Findings

Two kinds of insight, distinguished by direction:

- **Prior insights** (`prior_insights:`) — knowledge from outside the analysis that informs decisions. From literature (by DOI) or artifacts from a prior/parent analysis.
- **Findings** (`findings:`) — conclusions from the analysis itself, backed by its own output artifacts.

Both use the same Insight model: `id`, `label` (optional), `claim`, `created_at`, `evidence`, plus optional `derived` (true if synthesized/inferred from multiple sources), `scope` (applicability conditions), `tags`, `notes`. Placement determines direction.

Each evidence item has its own fields: `id`, exactly one of `doi` (literature) or `artifact` (output ID), and either a `quote` (TextQuoteSelector with required `exact`, optional `prefix`/`suffix`) or `location` (FragmentSelector with `value` like `"page=6"` and/or 1-indexed `page`). DOI evidence may add `version` (arXiv version). Artifact evidence may add `snapshot` (path to an immutable artifact copy) and `source_commit` (git commit that produced it).

```yaml
prior_insights:
  layer_norm_stability:
    id: layer_norm_stability
    label: "LN stability"
    claim: "Layer normalization improves training stability"
    created_at: "2025-01-15T10:30:00"
    derived: false
    scope: "Transformer training with batch sizes < 64"
    tags: [optimization]
    evidence:
      - id: e1
        doi: "10.48550/arXiv.1607.06450"
        version: 1
        quote: { exact: "Exact text", prefix: "~20-100 chars before", suffix: "~20-100 chars after" }
        location: { value: "page=5", page: 5 }

findings:
  scaling_result:
    id: scaling_result
    claim: "StandardScaler achieves 97% accuracy vs 91% for MinMaxScaler"
    created_at: "2025-02-01T14:00:00"
    derived: true
    evidence:
      - id: e1
        artifact: accuracy                       # references a declared output ID
        snapshot: "snapshots/run_2025-02-01.json"
        source_commit: "a3f9c12"
      - id: e2
        artifact: model_comparison
        quote: { exact: "StandardScaler achieved 97% accuracy vs 91% for MinMaxScaler" }
```

Link prior insights to decisions: `options: { layer_norm: { insights: [layer_norm_stability] } }`

Artifact references are validated against declared outputs — `astra validate` flags any `artifact:` that doesn't match an output ID. Literature evidence (DOI) requires a `quote` (a `TextQuoteSelector` with required `exact` plus optional `prefix`/`suffix`); artifact evidence does not. Each evidence item must set exactly one of `doi` or `artifact`.

**Sub-analysis findings as prior insights:** When a sub-analysis explores a specific question (calibration study, simulation validation, sensitivity test), its findings can inform decisions elsewhere. The parent or sibling references the sub-analysis output as artifact evidence in its own `prior_insights`, e.g. `artifact: "build_mocks.noise_diagnostics"`. This creates a traceable chain from sub-analysis conclusion to downstream decision.

### Adding a Paper as Prior Insight

Found a paper through literature search? Three steps to wire it into the analysis:

1. **Cache the PDF** — `astra paper add <doi>` downloads it to the project's paper cache. Pass `--pdf PATH` if you already have a local copy, or `--version N` for a specific arXiv version.
2. **Add a `prior_insights:` entry** that cites the DOI (and optionally `version`) under `evidence:`. The `quote.exact` text must match the PDF verbatim; optional `prefix`/`suffix` (~20–100 chars on either side) disambiguate when the exact string occurs more than once.
3. **Verify** — `astra paper verify-quotes <doi>` for one paper, or `astra validate astra.yaml --verify-evidence` to check every quote in the spec. A wrong `exact` string fails validation.

`astra paper list` shows what's cached; `astra paper path <doi>` prints the PDF path so you can open it for review.

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

Sub-analyses can be **inline** (their content lives directly under the parent's `analyses:` map) or **external** (`path:` points to a directory with its own `astra.yaml`). `path:` is mutually exclusive with inline content -- a sub-analysis entry sets either `path:` or fields like `inputs`/`outputs`/`decisions`, not both. The parent below uses external sub-analyses:

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
    from: train_network.trained_model   # Re-export from sub-analysis (pure alias)
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
    from: ../build_mocks.mock_catalog    # Sibling output (escape upward, then descend)
outputs:
  - id: trained_model
    type: data
    inputs: [training_data]
    decisions: [cosmology_model, noise_model]
    recipe: { command: python src/train.py --train {inputs.training_data} --out {output}, resources: { gpus: 1, memory: "32GB" } }
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

**Path grammar.** `from:` paths use a uniform tree-path syntax: `../` escapes one scope upward (stack as needed), and `name.subname` descends into a named child scope. Multiple levels work in either direction. Per-slot direction:

| Slot | Legal forms | Meaning |
|---|---|---|
| `Input.from` | `../id`, `../../id`, `../scope.out_id` | parent/ancestor Input, or a sibling sub's Output (escape up, then descend) |
| `Decision.from` | `../id`, `../../id` | parent/ancestor Decision (downward-only flow; share via common ancestor) |
| `Output.from` | `child.out_id`, `child.sub.out_id` | own child sub's Output (re-export; descend through nested children) |

`from:` makes the node a pure pointer -- only `id` and `from` (plus `when` on Outputs) are allowed; everything else (`type`, `description`, `source`, `options`, `default`, `recipe`, …) is inherited from the source.

The **`universe:` field** in universe files selects which sub-analysis universe to load: `build_mocks: { universe: baseline }` loads `./analyses/build_mocks/universes/baseline.yaml`.

## Narrative

`narrative` is the analysis-level prose field on any Analysis (root or sub). It's structured as five Markdown sections: `summary`, `findings`, `methods`, `inputs`, `outputs`. The schema is closed (`additionalProperties: false`) -- no other keys are allowed.

**Recommendation:** fill `narrative` in *later*, once the structural pieces of the analysis (decisions, outputs, sub-analyses) have settled. Prose written too early goes stale fast and tends to describe what no longer exists. Per-element prose (what each Input, Output, Decision, Option, or Insight is and why) belongs on the elements themselves via `description`/`rationale`/`notes` -- those can be written from day one.

**Conditional coverage.** All five sections are schema-optional, but `astra validate` enforces:

| Section | Required when |
|---|---|
| `findings` | the analysis has entries under `findings:` |
| `methods` | the analysis has entries under `decisions:` or `analyses:` |
| `inputs` | the analysis has entries under `inputs:` |
| `outputs` | the analysis has entries under `outputs:` |
| `summary` | always optional |

Authors narrate what they declare; stub analyses with only a summary stay clean.

**Anchor references.** Inside any section, link to other elements with Markdown anchor links (`[text](#path.to.element)`) using the same tree-path grammar as `from:` -- `#decisions.scaling`, `#decisions.scaling.options.standard`, `#findings.best_model`, `#analyses.preprocessing` (whole sub-analysis), `#analyses.preprocessing.outputs.features` (element inside a sub-analysis), `#../decisions.method` to escape to a parent scope.

**Inline images.** Standard Markdown image syntax inside any section -- `![alt](path/to/img.png)` for repo-relative paths or `![alt](https://...)` for URLs. Renderers like lightcone-ui pick them up the same way they pick up text.

```yaml
narrative:
  summary: |
    A two-stage pipeline for Iris classification that demonstrates
    sub-analyses.
  methods: |
    The [feature_extraction sub-analysis](#analyses.feature_extraction)
    produces encoded features, which feed
    [classification](#analyses.classification). A
    [test_split](#decisions.test_split) decision controls the holdout.
  inputs: |
    [iris_data](#inputs.iris_data) is Fisher's 150-sample, 4-feature,
    3-class dataset.
  outputs: |
    The top level exposes [accuracy](#outputs.accuracy) and a
    [pipeline_summary](#outputs.pipeline_summary) report.
```

## CLI Reference (astra)

```bash
astra init [DIRECTORY]                          # Scaffold a new analysis
astra validate astra.yaml                       # Validate (run after every change)
astra validate astra.yaml --verify-evidence     # + verify insight quotes against PDFs
astra info [--decisions|--inputs|--outputs]     # Analysis summary / element details
astra universe generate -n NAME [-d "desc"]     # Generate universe from defaults
astra universe check universes/x.yaml           # Check universe constraints
astra viz [--fmt ascii|mermaid]                 # Visualize decision space
astra schema show analysis|universe|insights    # Show JSON schema
astra paper add DOI [--version N] [--pdf PATH]  # Cache a paper for evidence checks
astra paper list                                # List cached papers
astra paper show DOI                            # Show metadata for a cached paper
astra paper path DOI [--version N]              # Print the cached PDF's path
astra paper verify-quotes DOI                   # Batch-verify quotes; reads {"quotes":[...]} JSON from stdin
```

## Validation

Run `astra validate astra.yaml` after **every** spec change. Additional checks:
- Universe files: `astra universe check universes/<name>.yaml`
- Evidence quotes: `astra validate astra.yaml --verify-evidence`
