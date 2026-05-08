# lightcone.engine.tree

Walk the resolved analysis tree. Used by the Snakefile generator,
`status`, and `verify` to enumerate outputs, resolve `from:`
references, and merge universe decisions across nested sub-analyses.

Source: `src/lightcone/engine/tree.py`.

## `TreeOutput` (dataclass)

```python
@dataclass
class TreeOutput:
    output_id: str
    output_def: dict             # the raw dict from astra.yaml
    analysis_id: str | None      # None for root-level outputs
    analysis_path: str | None    # e.g. "./analyses/hod_fitting"
    analysis_spec: dict          # the sub-analysis spec dict (root spec for root outputs)
```

## Public functions

### `collect_tree_outputs(spec) → list[TreeOutput]`

Walk the resolved tree (root-level outputs first, then each
sub-analysis under `analyses:`) and return one `TreeOutput` per output
declaration.

### `collect_tree_inputs(spec) → dict[str, dict]`

Return `{qualified_id: input_def}` where `qualified_id` is `input_id`
for root inputs and `analysis_id.input_id` for sub-analysis inputs.

### `resolve_universe_decisions(project_path, spec, universe_id) → dict`

Load and merge universe decisions from root and sub-analyses. Returns
a flat dict using qualified keys for sub-analysis decisions
(`analysis_id.decision_id`) to avoid collisions.

Behavior:

- Loads root universe from `universes/<universe_id>.yaml`.
- For each sub-analysis with `path:`, looks for the corresponding
  universe file at `<sub_path>/universes/<sub_universe_id>.yaml`. The
  sub-universe id comes from `root_universe.analyses.<analysis_id>.universe`
  if present, otherwise the root universe id.
- `from:` references on sub-analysis decisions (`from: ../parent_decision`)
  are resolved to the corresponding root decision value.
- Local sub-decisions not referenced via `from:` are still added.

### `get_decisions_for_analysis(merged_decisions, analysis_id) → dict`

Extract the decisions relevant to a specific analysis. Root analysis
(`analysis_id=None`): returns all unqualified keys. Sub-analysis:
returns decisions with the matching `analysis_id.` prefix, stripped to
local names.

### `resolve_output_path(project_path, tree_output, universe_id) → Path`

Returns the *parent* directory of the output dir (i.e. the
`results/<universe>/` directory). The actual output dir is this path
joined with `tree_output.output_id`.

- Root + inline sub-analyses: `<project>/results/<universe>/`
- Path-rooted sub-analyses: `<project>/<sub_path>/results/<universe>/`

### `resolve_container_spec(tree_output, root_spec) → str | None`

Pick the container declaration in priority order:

```
recipe-level  >  sub-analysis-level  >  root-level
```

Returns the raw spec string (Containerfile path or registry image), or
`None` when no container is declared anywhere.

### `find_upstream_output(consumer, inp_id, all_outputs) → TreeOutput | None`

Resolve a recipe input id to the producing `TreeOutput`. Mirrors the
lookup the Snakefile generator does for `rule.input`. Handles:

- Dotted `<analysis_id>.<output_id>` → match by qualified key.
- Inside a sub-analysis, bare `inp_id` → first try
  `<consumer.analysis_id>.<inp_id>`, then bare.
- `inp_id` referencing an analysis-level input with `from:` pointing
  at a sibling output → resolved through that.

Returns `None` for inputs that refer to external files (no producer
rule).

### `resolve_input_path(project_path, spec, from_ref, universe_id) → str | None`

Resolve a `from:` reference on an input to a concrete filesystem path.
Handles:

- `../parent_input` → root input's `source` (only if absolute).
- `../sibling.output_id` → sibling sub-analysis's results path.
- `sibling.output_id` (no `../`) → same as above (root convenience).

## Tests

`tests/test_tree.py` covers all four resolvers, including the `from:`
edge cases for sub-analysis decisions and inputs.
