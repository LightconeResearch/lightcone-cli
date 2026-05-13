# Glossary

The terms you'll see all over the docs and the agent output, in plain
language.

## ASTRA

**A**gentic **S**chema for **T**ransparent **R**esearch **A**nalysis.
The schema lightcone-cli is built around. ASTRA's job is to capture an
analysis's inputs, outputs, and methodological decisions in a single
file (`astra.yaml`); lightcone-cli's job is to execute that spec
reproducibly. ASTRA ships separately as the `astra-tools` package and
the `astra` CLI handles the spec itself (validation, paper management,
evidence verification).

## astra.yaml

Your project's spec file. The single source of truth — every input,
output, recipe, and decision is declared here. Sub-analyses can be
nested via `analyses:` references.

## Recipe

A short shell or Python command that produces an output. Lives inside
an output's `recipe:` block in `astra.yaml`. Recipes can declare
which sibling outputs they depend on:

```yaml
outputs:
  - id: r2
    recipe:
      command: python scripts/fit.py --output {output[0]}
  - id: fit_plot
    recipe:
      command: python scripts/plot.py --r2_dir {input.r2} --output {output[0]}
      inputs: [r2]
```

## Decision

A methodological choice with multiple defensible options (e.g.
"standardize features?", "what outlier threshold?"). Decisions live
in the `decisions:` section of `astra.yaml` along with their `default`,
their `options`, and their `rationale`.

## Universe

One specific selection of decision values. Universes live as YAML
files in `universes/` (e.g. `universes/baseline.yaml`,
`universes/permissive.yaml`). Each universe materializes its results
to its own directory: `results/<universe>/<output_id>/`.

If your spec has no universes, `lc run` materializes against a
universe called `"default"` with all decisions at their declared
defaults.

## Sub-analysis

A nested ASTRA analysis with its own inputs, outputs, and decisions,
referenced from a parent's `analyses:` section. The full tree shares
one set of universes; sub-analyses can reference parent decisions
with `from:` references. Sub-analyses are useful when an analysis has
genuinely different stages (training vs. inference, fit vs. evaluate);
keep things in one analysis when they share the same product.

## Manifest

The per-output sidecar JSON file
(`<output_dir>/.lightcone-manifest.json`) that records what produced
the output and what's inside it. Fields include `code_version`,
`data_version`, `container_image`, `recipe`, `decisions`,
`input_versions`, `git_sha`, `host`, `lc_version`, and a few more.
Manifests are written atomically by `lc run` and read by `lc status`
and `lc verify`.

## code_version

A SHA-256 over `(recipe + container_image + decisions)`. The
fingerprint of "what does this rule do?" When it drifts, downstream
outputs go `stale` in `lc status`.

## data_version

A SHA-256 over the contents of an output directory (excluding the
manifest itself). The fingerprint of "what bytes were produced?"
`lc verify` recomputes this and compares to the recorded value to
catch tampering.

## input_versions

Inside a manifest, a dict mapping each declared input id to its
version: the upstream output's `data_version` when the input is
another materialized output, or an `mtime-size`/`sha256`
fingerprint when the input is an external file. This is the chain
`lc verify` walks back through.

## Container

A Docker / Podman / podman-hpc image used to execute a recipe in
isolation. Declared at the analysis level (`container: Containerfile`)
or per-recipe (`recipe: { container: python:3.12-slim }`). Recipe-level
overrides win.

## Containerfile

A Dockerfile by another name (the syntax is identical). lightcone-cli
calls them Containerfiles to make clear they work with podman as well
as docker.

## Image tag

The string the runtime uses to identify a built image. lightcone-cli
generates content-addressed tags for Containerfile builds:
`lc-<project>-<sha256[:12]>`. The hash covers the Containerfile and
your dependency files, so tags only change when the inputs to the
build change.

## Runtime

The OCI tool that actually executes containers: `docker`, `podman`,
or `podman-hpc`. Set in `~/.lightcone/config.yaml` under
`container.runtime`. `auto` picks the first usable; `none` opts out
(runs recipes directly on the host).

## Snakemake

The workflow engine `lc run` shells out to. You don't need to learn
Snakemake to use lightcone-cli — the Snakefile at `.lightcone/Snakefile`
is auto-generated from your `astra.yaml`. If you're curious, peek at
it; just don't edit it (your changes will get overwritten on the
next `lc run`).

## Dask

The distributed scheduler `lc run` dispatches jobs through. On a
laptop it's a `LocalCluster` sized to your machine; inside a SLURM
allocation it's an in-process scheduler with one `dask worker` per
node launched via `srun`.

## Skill

A Claude Code slash command bundled with the lightcone-cli plugin.
The `/lc-from-*` family is parallel by what you start from — a question
(`/lc-new`), code (`/lc-from-code`), or a paper
(`/lc-from-paper`). `/lc-feedback` files upstream issues from inside
the session. Each one is a structured prompt that drives the agent
through a specific phased workflow.

## Subagent

A Claude Code agent invoked by another agent via the `Task` tool. The
`lc-extractor` subagent reads PDFs and pulls verifiable quotes; it's
spawned by `/lc-new` during the literature deep-dive phase.
Subagents have isolated context, which is why `/lc-new` uses
one per paper — PDFs are big.

## Prior insight

A piece of evidence from the literature that informs a decision.
Lives in the `prior_insights:` section of `astra.yaml`. Each insight
has a `claim`, one or more `evidence` entries with verbatim quotes,
and a list of decision options it supports. Quotes are
machine-verified against the source PDF.

## Finding

A conclusion drawn *from* the analysis (as opposed to a prior
insight, which comes *into* the analysis). Findings live in the
`findings:` section, can cite specific outputs as evidence, and act
as the bridge between materialized results and the eventual paper.

## Status (`ok`, `stale`, `missing`, `alias`)

The four labels `lc status` produces:

- `ok` — manifest present, recomputed `code_version` matches.
- `stale` — manifest present but `code_version` drifted.
- `missing` — no manifest at the expected output directory.
- `alias` — output declared without a recipe; just a reference to
  another output.

## Failure kinds (`tampered_data`, `broken_chain`, `missing_manifest`)

The three labels `lc verify` produces when something's wrong:

- `tampered_data` — bytes on disk no longer match recorded
  `data_version`.
- `broken_chain` — recorded `input_versions` references an upstream
  whose `data_version` drifted.
- `missing_manifest` — output directory exists but the manifest is
  missing or unparseable.

## Ralph loop

A reusable autonomous iteration pattern for long-running agent work.
Each iteration surveys state, decides what to do next, writes or runs
code, commits, and exits. A bundled tmux runner spawns a fresh worker
per iteration with the *constitution* — a markdown file describing what
"done" looks like — as system prompt; the constitution stays editable
across iterations. Stop the loop by setting `status: closed` in the
constitution's frontmatter (the next iteration sees it and exits) or by
killing the tmux session.

## Permission tier

The set of tools and bash patterns Claude Code is allowed to use in
your project. Three tiers ship: `yolo` (everything), `recommended`
(default — full access minus dangerous patterns), `minimal` (read
only). Selected at `lc init` time and stored in
`.claude/settings.json`.
