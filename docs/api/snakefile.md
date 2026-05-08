# lightcone.engine.snakefile

Generate `.lightcone/Snakefile` and `.lightcone/snakefile-config.json`
from `astra.yaml`. Both are auto-generated on every `lc run` — never
edit by hand.

Source: `src/lightcone/engine/snakefile.py`.

## Public surface

```python
__all__ = ["generate", "discover_universes", "LIGHTCONE_DIR"]
```

## `generate(project_path, *, universes, runtime="none") → (Path, Path)`

Reads `astra.yaml`, resolves the analysis tree, and writes:

- `.lightcone/Snakefile` — the workflow.
- `.lightcone/snakefile-config.json` — per-`(rule_key, universe)` config.

Returns the two paths.

`runtime` is one of `docker | podman | podman-hpc | none` and is used to
wrap each recipe at generation time (see
[engine.container.wrap_recipe](container.md#wrap_recipe)). Resolution is
done once here, not per rule, so all rules use a consistent runtime.

## `discover_universes(project_path) → list[str]`

Sorted list of universe ids from `universes/*.yaml`, or `["default"]` if
the directory is empty / missing.

## Generated Snakefile shape

For each output with a `recipe:` block:

```python
rule <name>:
    input:
        <inp_id>="<output_dir>/...",      # only for sibling outputs
    output:
        data=directory("<output_dir>"),
        manifest="<output_dir>/.lightcone-manifest.json",
    params:
        cfg=lambda wc: CFG["<rule_key>"][wc.universe],
    run:
        shell('printf "▶ <rule_key> [%s]\\n" "{wildcards.universe}" >&2')
        shell(params.cfg["shell_command"])
        write_manifest(
            output_dir=Path(output.data),
            inputs={"<inp_id>": Path(input.<inp_id>), ...},
            cfg=params.cfg,
        )
        for _w in validate_output(Path(output.data), params.cfg.get("output_type"), params.cfg["output_id"]):
            print(f"\033[33m⚠\033[0m {_w}", file=sys.stderr)
```

## `cfg` content

Per-`(rule_key, universe)` entry written into
`snakefile-config.json`:

| Key | Source | Used by |
|-----|--------|---------|
| `output_id` | `tree_out.output_id` | `write_manifest` |
| `output_type` | `output_def["type"]` | `validate_output` |
| `universe_id` | universe name | `write_manifest` |
| `recipe` | `recipe.command` | `write_manifest` |
| `shell_command` | `wrap_recipe(recipe, image, runtime)` prefixed with `: lc_code_version=…;` | the rule body |
| `container_image` | raw `container:` spec from astra.yaml | `write_manifest` (for provenance) |
| `decisions` | merged universe decisions | `write_manifest`, `code_version` |
| `code_version` | `code_version(recipe, image_tag, decisions)` | drift detection via Snakemake `params` trigger |
| `git_sha`, `lc_version` | runtime metadata | `write_manifest` |
| `inputs` | resolved input paths (with `{universe}` substituted) | informational |

## Why we own the container wrap

Snakemake supports container directives (`container:` and
`--sdm apptainer`), but we deliberately don't use them. Two reasons,
both pragmatic:

- `--sdm apptainer` adds an extra container layer that defeats
  podman-hpc's migrate workflow.
- Default registry resolution on podman fails for
  `lc-<project>-<hash>` tags because they trip
  `unqualified-search-registries`. We pass `--pull=never` to skip the
  lookup; Snakemake's machinery doesn't make this easy to thread
  through.

## Naming details

- **`_rule_key(tree_out)`** — `output_id` for root outputs,
  `<analysis_id>.<output_id>` for sub-analysis outputs. This is the
  user-visible name and the cfg key.
- **`_rule_name(tree_out)`** — same as `_rule_key` but with `.` →
  `__` because Snakemake rule names must be Python identifiers.
- **`_output_dir_pattern(tree_out)`** — wildcard path. Root and inline
  sub-analyses: `results/{universe}/<output_id>`. Path-rooted
  sub-analyses: `<sub_path>/results/{universe}/<output_id>`.

## Tests

`tests/test_snakefile.py` covers rule generation across root + sub-analyses,
input wiring, container wrapping, `code_version` embedding, and (last
test) parses the generated Snakefile via `snakemake -n`.
