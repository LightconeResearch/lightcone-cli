# Multiverse Analyses

The whole point of ASTRA is that the choices you make shouldn't be
silent. lightcone-cli makes it easy to sweep them: every methodological
choice is a **decision**, and every combination of decision values is
a **universe**. Each universe gets its own results tree, and the
provenance system keeps everything tagged so you can compare them.

## Decisions, options, defaults

A decision is one methodological choice. In `astra.yaml`:

```yaml
decisions:
  standardize:
    label: "Feature standardization"
    rationale: "Affects coefficient scale and downstream R²."
    default: standardized
    options:
      standardized: { label: "StandardScaler before fit" }
      raw: { label: "No preprocessing" }
  outlier_sigma:
    label: "Outlier removal threshold (σ)"
    default: "3.0"
    options:
      "3.0": { label: "3σ — conservative" }
      "5.0": { label: "5σ — permissive" }
      none:  { label: "No outlier removal" }
```

A few rules of thumb (the `/lc-new` skill enforces these):

- **One choice, one decision.** Don't bundle "preprocessing strategy"
  into one decision with five options that mix different axes.
- **Defaults must be defensible.** "It's what I used last time" is
  not a defense; "it's the most common choice in [paper X]" is.
- **Tooling choices that produce identical results are not
  decisions.** PyTorch vs TensorFlow. CSV vs Parquet. These don't
  change the science.

## Universes

A universe is one selection of values across all decisions. Universes
live as YAML files in `universes/`:

```yaml
# universes/baseline.yaml
id: baseline
decisions:
  standardize: standardized
  outlier_sigma: "3.0"
```

```yaml
# universes/permissive.yaml
id: permissive
decisions:
  standardize: standardized
  outlier_sigma: "5.0"
```

You don't usually write these by hand — generate them with `astra`:

```bash
astra universe generate -n permissive -d outlier_sigma=5.0
```

(Decisions you don't override fall back to their defaults from
`astra.yaml`.)

## Running across universes

```bash
lc run                          # every universe, every output
lc run --universe baseline      # one universe, every output
lc run accuracy                 # every universe, one output
lc run accuracy --universe baseline   # one of each
```

Each universe gets its own results directory:

```
results/
├── baseline/
│   ├── r2/...
│   └── fit_plot/...
├── permissive/
│   ├── r2/...
│   └── fit_plot/...
```

`lc status` shows a per-universe column, so you can see at a glance
which universes are caught up:

```
Universe baseline
  ✓ ok    r2
  ✓ ok    fit_plot
Universe permissive
  ✓ ok    r2
  ✸ stale fit_plot       # because the spec changed since this universe ran
```

## Comparing universes

The comparison itself is your code: read the per-universe
`r2.json` files, plot them, write up the result. ASTRA's job is to
make sure the comparison is *fair* — every universe used the same
spec, the same container image, the same recipe text. If anything
drifted, `lc verify` will flag it.

## Decision constraints

Sometimes choices interact:

```yaml
decisions:
  use_pca:
    default: "no"
    options:
      "yes": { label: "Yes" }
      "no": { label: "No" }
  n_components:
    default: "50"
    options:
      "50": { label: "50 components" }
      "100": { label: "100 components" }
    requires: { use_pca: "yes" }       # only meaningful when PCA is on
```

`requires:` means "this option is only valid when those conditions
hold." `incompatible_with:` is the dual. The full schema is in the
[ASTRA spec reference](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/guides/astra-reference.md).

## Sub-analyses

If your analysis has internal structure — say, a fitting stage and an
evaluation stage — you can split it into nested sub-analyses, each
with its own `astra.yaml` and own decisions. The full tree is
resolved automatically; sub-analyses can refer to each other's
outputs.

`/lc-new` will ask "should this be one analysis or several?" and
help you split. The default answer is one — split only when each part
genuinely has different inputs and outputs.

## What lightcone-cli does *not* do

- It doesn't auto-generate the cross-product of universes for you.
  You write the universe files (or have `astra universe generate`
  write them); each one is an explicit, defensible choice.
- It doesn't pick the "best" universe. That's your scientific call.

## Practical tips

- Start with one universe. Get the analysis materializing end-to-end
  before adding more.
- Add one decision at a time. The provenance system keeps everything
  tagged, so even a year later you'll know what `permissive` meant.
- When you change defaults in `astra.yaml`, downstream universes go
  `stale` automatically. `lc run` does the right thing.

For the hands-on side, the [tutorial](tutorial.md) builds and sweeps a
single decision end to end.
