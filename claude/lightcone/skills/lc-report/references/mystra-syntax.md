# MySTRA syntax reference

MySTRA is a MyST plugin (a single `.mjs` loaded by URL in `myst.yml` — nothing to install) that resolves `{astra}` references against `astra.yaml` at build time. Everything else is ordinary MyST — prose, math, citations, numbering, multi-page structure all come from the stock `myst` engine. Full upstream docs: <https://github.com/LightconeResearch/MySTRA>.

## Choosing a surface

| You want to… | Use |
|---|---|
| Mention an element in a sentence | `{astra}` role |
| Refer to a placed figure by number ("Figure 3") | `{astra:ref}` |
| Cite the literature behind an insight | `{astra:cite}` / `{astra:cite:t}` |
| Put a measured number in prose | `{astra:value}` |
| Embed a figure/decision/finding as a block | `{astra}` directive |
| Link to a placed block with custom text | `[text](#<kind>-<id>)` anchor |
| Split the report along sub-analyses | multi-page (dotted filenames) |

## Path grammar

Paths are dot-separated routes through the analysis tree, mirroring `astra.yaml` (the same spelling as `when: decision.option` and recipe `{inputs.id}` placeholders). Paths always resolve from the **root analysis**, on every page.

```
outputs.hubble_diagram                  an output (figure / table / metric / …)
decisions.algorithm                     a decision
decisions.algorithm.gp                  one option of a decision
findings.sig.fig1                       one evidence record of a finding
prior_insights.recon_sharpens_bao       a prior insight
inputs.raw_catalog                      an input
reconstruction.outputs.xi               an output in the `reconstruction` sub-analysis
reconstruction                          the sub-analysis itself
outputs                                 a whole collection (a registry)
```

- Collections are exactly the `astra.yaml` keys: `inputs`, `outputs`, `decisions`, `findings`, `prior_insights` (alias `prior-insights`), `analyses`, `universes`.
- Sub-analysis ids may be written directly — the `analyses.` prefix is implied — and nest to any depth: `clustering.correlation.outputs.xi`.
- Child long forms exist too: `decisions.<id>.options.<opt>`, `findings.<id>.evidence.<ev>`.
- A path stopping at a collection addresses the whole collection: `outputs`, `reconstruction.inputs`.
- Final segments are the `id`s from the spec; MySTRA renders the element's `label:` (or `name:` for analyses), falling back to the id with underscores → spaces.

## Inline mention — the `{astra}` role

```markdown
We adopt the {astra}`decisions.algorithm` and report {astra}`outputs.hubble_diagram`,
which confirms {astra}`findings.signal_detected`.
```

Custom display text uses MyST's `text <target>` convention:

```markdown
{astra}`our preferred method <decisions.algorithm>`
```

Any path works: decisions, options, outputs, findings, prior insights, inputs, sub-analyses, elements inside sub-analyses.

## Numbered reference — `{astra:ref}` (alias `{astra:numref}`)

A native numbered cross-reference ("Figure 3") to an output **placed as a block somewhere in the report** — sugar for `[Fig. %s](#output-<id>)`. If the target is never embedded, there is nothing to point at.

```markdown
{astra:ref}`outputs.hubble_diagram`                  # "Figure 3"
{astra:ref}`see Fig. %s <outputs.hubble_diagram>`    # custom text; %s is the number
```

## Citations — `{astra:cite}` and `{astra:cite:t}`

Turns DOI-backed evidence on findings/prior insights into real bibliographic citations through MyST's citation pipeline. Findings and prior-insight paths only.

```markdown
{astra:cite}`prior_insights.recon_sharpens_bao`     # "(Chen et al., 2024)" — parenthetical
{astra:cite:t}`prior_insights.recon_sharpens_bao`   # "Chen et al. (2024)"  — textual
```

Every distinct DOI on the element's evidence is cited (multiple DOIs render as a group). An element with no DOI evidence falls back to a plain inline reference.

## Live values — `{astra:value}`

Interpolates a real number from the resolved analysis at build time. The role body is the path; selection is expressed as inline options inside the braces:

```markdown
{astra:value col=DV_over_rd where="tracer=lrg3_elg1" pm=true}`outputs.bao_distance_table`   → 19.88 ± 0.17
{astra:value col=alpha1 where="tracer=elg1 recon=Pre" sig=3}`outputs.bao_alpha_values`      → 0.0696
{astra:value}`outputs.chi2_reduced`                                                          → 1.5
{astra:value}`decisions.algorithm`                                                           → MultiGrid
```

| Option | Meaning |
|---|---|
| `col=<column>` | Column to read (required for table outputs). |
| `where="k=v …"` | Row filters; space- or comma-separated `key=value` pairs, all must match (case-insensitive). Must select exactly one row. Alias: `filter=`. |
| `pm=true` | Also render uncertainty: `<col>_std` for tables, the metric's own uncertainty for metrics. |
| `err=<column>` | Explicit uncertainty column (instead of the `<col>_std` convention). |
| `sig=<N>` | Significant figures (default 4; uncertainties render with 2). |

- Quote values containing spaces (`where="tracer=lrg3 recon=Post"`); bare is fine otherwise (`col=alpha`).
- **Table outputs** read the materialized CSV/JSON. **Metric outputs** (a JSON scalar, `[value, uncertainty]`, or `{value, uncertainty, unit}`) interpolate directly, no options needed.
- **Decisions:** `{astra:value}`decisions.<id>`` renders the label of the **option selected under the active universe** — use it whenever prose depends on which option is active, so the sentence updates with the universe. (`{astra}`decisions.<id>`` names the decision itself.)
- Inline role options are recent mystmd; if `myst` reports an unknown role for `{astra:value col=…}`, update the CLI.

## Block embeds — the `{astra}` directive

```markdown
:::{astra} outputs.bao_fit_plot
:caption: The post-reconstruction fit; see {astra}`decisions.algorithm`.
:label: fig-bao
:::
```

What each path renders:

| Path | Renders |
|---|---|
| `outputs.<id>` | The real figure / table / metric, with provenance |
| `decisions.<id>` | Label, rationale, options as tabs, the universe's selection marked |
| `decisions.<id>.<option>` | One option: label, description, supporting insights |
| `findings.<id>` | Claim + notes + scope + evidence |
| `findings.<id>.<evidence>` | One evidence record |
| `prior_insights.<id>` | A "see also" admonition with its evidence |
| `inputs.<id>` | A one-row registry table |
| `<sub-analysis>` | A navigation card linking to the sub-analysis page |
| `universes.<id>` | Table of the universe's decision → selected-option pairs |
| a collection (`outputs`, `findings`, …) | The whole registry |

Directive options:

| Option | Meaning |
|---|---|
| `:label:` | Cross-reference label. **Replaces** the default `<kind>-<id>` anchor — manage the anchor yourself if you set it. |
| `:caption:` | Caption text (figure/table outputs). Markdown allowed. |
| `:compact:` | Findings: claim + notes + scope only (no evidence). |
| `:show:` / `:hide:` | Findings: include/exclude parts from `claim, notes, scope, evidence` (claim always kept). |
| `:class:` | Extra CSS class(es). |

## Anchors (plain MyST cross-references)

Every embedded element carries a stable anchor `<kind>-<id>`: `output-hubble_diagram`, `decision-algorithm`, `finding-signal_detected`, `prior_insight-<id>`, `input-<id>`, `option-<decision>-<option>`, `universe-<id>`, `analysis-<sub>`. They resolve project-wide, across pages:

```markdown
[](#output-hubble_diagram)              # auto-filled, numbered link text
[the diagram](#output-hubble_diagram)   # custom text
```

## Project files

```
my-analysis/
├── astra.yaml          # the analysis spec (MySTRA reads it from the working directory)
├── universes/          # first .yaml = the active universe
├── results/            # materialized artifacts, results/<universe>/<output>/
├── myst.yml            # registers the plugin; lists pages
└── index.md            # the report (+ optional sub-analysis pages)
```

Minimal `myst.yml` (what `lc init` scaffolds):

```yaml
version: 1
project:
  plugins:
    - https://github.com/LightconeResearch/MySTRA/releases/latest/download/mystra.mjs
  toc:
    - file: index.md
site:
  template: book-theme
```

`latest` tracks the newest MySTRA release; for a reproducible build pin a tag instead (e.g. `.../releases/download/v0.0.1/mystra.mjs`) — recommended for anything you intend to keep building. All other `myst.yml` settings (theme, numbering, bibliography, exports) are standard MyST.

**Figures resolve deterministically** — MySTRA never scans `results/`; it computes `<analysis path>/results/<universe>/<output-id>/<output-id>.<ext>` and hands images to MyST's asset pipeline (hashed and copied, so the built site is self-contained). A sub-analysis with `path:` in `astra.yaml` roots its own `results/` tree there automatically.

## Multi-page reports

Pages mirror the analysis tree; scope derives from the dotted filename:

| File | Scope |
|---|---|
| `index.md` | the root analysis |
| `reconstruction.md` | the `reconstruction` sub-analysis |
| `reconstruction.features.md` | `features` inside `reconstruction` |

List every page in `myst.yml`'s `toc:`. Override scope via frontmatter when the filename can't carry it:

```markdown
---
astra_scope: reconstruction.features
---
```

Paths still resolve from the root on every page; scope selects the page's resolved store (what a rich theme joins against). Cross-page links work through the `<kind>-<id>` anchors.

## Build, preview, diagnose

```bash
myst start          # live preview → http://localhost:3000
myst build --html   # static site in _build/html/ (self-contained)
```

Requires the MyST CLI (`npm install -g mystmd` or `uv tool install mystmd`; Node ≥ 18). Run from the project root — MySTRA reads `astra.yaml` from the working directory. `myst start` watches only `.md` files: after editing `astra.yaml` or a universe file, re-save any page (or restart) to re-render.

How breakage surfaces (the build never crashes on a bad reference — it renders the problem visibly):

| Problem | Rendering |
|---|---|
| Spec semantic issue (e.g. `when:` naming an unknown decision) | `[mystra]` build warning |
| Unresolvable inline role path | plain label derived from the path |
| `{astra:cite}` on a non-citable path | small inline error token |
| Unresolvable directive path | error admonition naming the path and reason |
| Broken `{astra:value}` (missing file, unknown column, no matching row) | inline code token, e.g. `⟨value: no column "alpha2" in "bao_table"⟩` |
| `{astra:ref}` to an output never placed as a block | dangling-reference build warning |
| Page with unknown `astra_scope` | build error |

MyST strict mode gates on these diagnostics. Treat any of them as a bug in the report: fix and rebuild until clean.
