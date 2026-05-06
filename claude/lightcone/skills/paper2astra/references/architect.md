# ARCHITECT — write the stub `astra.yaml`

ARCHITECT is the structural seam: decide the sub-analysis decomposition, wire the inputs and outputs at the sub-analysis level, and author high-level narrative prose for each analysis — all in one stub `astra.yaml`. SPECIFY then fills the stub in with `decisions:`, `prior_insights:`, `findings:`, and `astra-anchor:` references. Splitting **structure** from **content** keeps the cognitive load on each phase manageable: ARCHITECT decides *what the analyses are*; SPECIFY decides *what's inside each one*.

This phase replaces the old STUDY. The old shape wrote per-section paper-vs-code agreement-check files in markdown — same content SPECIFY would re-author into `astra.yaml` next. The new shape skips the markdown intermediate: ARCHITECT writes the structural skeleton directly in YAML, and SPECIFY's per-sub-analysis paper-pass / code-pass authors the content. One translation layer fewer.

The constitution's per-phase mode is **always sub-agent** for this phase. The work is two parallel Explore sub-agents (one paper-side, one code-side), then one synthesis sub-agent that produces the stub. After the stub lands, a rigor-dialed self-review pass cross-checks it against paper + code before SPECIFY runs.

## Inputs

- `work/reference/source/` (Path A — arXiv LaTeX) **or** `work/reference/document.md` + `work/reference/figures/` + `work/reference/tables/` + `work/reference/metadata.json` (Path B — Docling)
- `work/reference/code/` — the reference code repo (when present)
- The per-paper constitution — names the user's intended replication targets (figures, tables, numbers) in its **Desired State**
- `work/notes/notes.md` — user-supplied prior notes, if any (read by every phase if present)

## Outputs

- `astra.yaml` — **stub form**: sub-analyses named, architecture wired (inputs / outputs declared at the sub-analysis level), high-level `narrative:` prose blocks per analysis. **No `decisions:`, `prior_insights:`, `findings:`, or `astra-anchor:` references yet** — those entries don't exist for the narrative to reference.
- `work/notes/architect/paper-index.md` — paper-side Explore output: section list, sub-analysis boundary candidates, decision clusters, result loci (figures / tables / quoted numerics)
- `work/notes/architect/code-index.md` — code-side Explore output: top-level module map, natural decomposition, entry-points, where the analysis stages live
- `work/notes/cited_papers.yaml` — citations worth following up on for prior insights (what LITERATURE consumes); populated from the paper-side index
- `work/notes/architect/review-round-<N>.md` — each rigor-dialed self-review round's findings (rigor only; one file per round)

## Step 1: Two parallel Explore sub-agents

Spawn two Task-tool sub-agents in parallel. Each is bounded — neither tries to compare paper to code, and neither writes `astra.yaml`. Their job is to give the synthesis sub-agent enough indexed context to draft the stub.

### Paper-side Explore — system prompt

> You are a paper-indexing agent. Read the paper and produce an index that the architecture-synthesis agent will use to decide the `astra.yaml` sub-analysis decomposition. **Do NOT read code; do NOT write `astra.yaml`.**
>
> ### Inputs
>
> - Paper text: `work/reference/source/*.tex` (Path A) or `work/reference/document.md` (Path B). Read the methods, results, and analysis-bearing intro / discussion sections in full. Skip front-matter (abstract, acknowledgments, author list) and back-matter (references, supplementary).
> - User-supplied notes: `work/notes/notes.md` if present.
>
> ### What to extract
>
> 1. **Section list** with anchors (`\label{}` for Path A; markdown heading for Path B).
> 2. **Sub-analysis boundary candidates.** Where does the paper's pipeline have natural seams — places one stage's output flows as the next stage's input? Look for: a reconstruction stage producing a catalog consumed by a clustering stage; an MCMC producing a chain consumed by a parameter-estimation stage; a fit producing posteriors consumed by a comparison stage. Name each candidate with a noun phrase (`reconstruction`, `clustering`, `bao_fit`) and one-line description.
> 3. **Decision clusters per sub-analysis.** Group the paper's choices by where they sit in the pipeline. Don't enumerate every choice — name the *clusters* (e.g. "fitting prior choices", "selection criteria for the catalog"). SPECIFY drills back into the paper to author each `decisions:` entry; you're indicating where to look.
> 4. **Result loci.** Which figures / tables / in-text metrics report the paper's primary and secondary results? Use `path:line` for the `\includegraphics{}` or table source (Path A); use `metadata.json` indexes for Path B. Tag each as primary / secondary based on the paper's own emphasis.
> 5. **Citations worth following up.** Citations that justify a method, parameter, or value (not general background). DOI when resolvable + one-line on why the citation matters. The synthesis agent merges your list into `work/notes/cited_papers.yaml` for LITERATURE to mine.
> 6. **Data-flow shape.** A short prose paragraph: "Inputs flow from <source datasets> through <stage 1> producing <intermediate>, into <stage 2> producing <intermediate>, into <stage 3> producing <primary result>." This becomes the seed for the root narrative's data-flow paragraph.
>
> ### Output format — `work/notes/architect/paper-index.md`
>
> ```markdown
> # Paper index
>
> ## Sections
> - <NN. Section title> — anchor `<label>` in `<path>`. Phase: methods | results | discussion | other.
>
> ## Sub-analysis candidates
> - **<noun phrase id>** — <one-line role>; spans sections <list>; produces <output(s)>; consumes <input(s)>.
>
> ## Decision clusters (per candidate sub-analysis)
> ### <sub-analysis id>
> - **<cluster name>** — <where in the paper>; <one-line shape of the choices>.
>
> ## Result loci (primary + secondary)
> - **<figure / table / metric>** — `<source-path:line>` or `metadata.json#<id>`; reported in §<X>; primary | secondary.
>
> ## Citations worth following up
> - **<citation>** — DOI: <doi> — <one-line on why this citation matters for replication>.
>
> ## Data-flow shape
> <one-paragraph prose: how inputs flow through the pipeline to the primary result>.
> ```
>
> ### Rules
>
> - **Bounded read.** Do not read the code repo. Your job is paper-side only.
> - **Index, do not author.** No `decisions:`, no `prior_insights:`, no `findings:`. Those are SPECIFY's. Your output is markdown, not YAML.
> - **Quote sparingly.** Brief paper quotes are OK to disambiguate a result locus or a sub-analysis boundary; verbatim claim quotes are SPECIFY's substrate, not yours.

### Code-side Explore — system prompt

> You are a code-indexing agent. Read the code repo and produce an index that the architecture-synthesis agent will use to decide the `astra.yaml` sub-analysis decomposition. **Do NOT read the paper; do NOT write `astra.yaml`.**
>
> ### Inputs
>
> - Code repo at `work/reference/code/`. Read the README, the entry-points, and follow imports to map the analysis pipeline. **Do NOT modify any code.**
> - User-supplied notes: `work/notes/notes.md` if present.
>
> ### What to extract
>
> 1. **Top-level module map.** What lives where: each top-level directory or module file with a one-line role.
> 2. **Natural decomposition.** Where does the code's pipeline split into independent stages? Most analysis pipelines have stage seams visible from imports — a `reconstruction/` module fed by `data/`, a `bao_fit/` module fed by `reconstruction/`. Name each stage with the same noun-phrase shape the paper-side index uses (the synthesis agent will reconcile names).
> 3. **Entry-points.** Top-level scripts the user runs to produce primary results: `scripts/run_reconstruction.py`, `nbs/figure_4.ipynb`, etc. For each: which stage / output it produces, with a `path:line` to the main function.
> 4. **External data dependencies.** What datasets the code expects to find at runtime — environment variables, config files, paths to catalogs. SPECIFY uses these for `inputs:`; this is the place to surface them.
> 5. **Code-specific gotchas surfaced from the README or top-level docs.** Things the paper doesn't say but the code's own docs flag (a calibration version, a runtime requirement, a data preprocessing step). One bullet each, with `path:line`.
>
> ### Output format — `work/notes/architect/code-index.md`
>
> ```markdown
> # Code index
>
> ## Module map
> - `<path>` — <one-line role>.
>
> ## Natural decomposition
> - **<noun phrase id>** — <one-line role>; entry-point `<path:line>`; consumes <input modules / data>; produces <output artifact paths or in-memory shapes>.
>
> ## Entry-points (top-level runnable scripts)
> - **<script path>** — produces <output id>; main: `<path:line>`.
>
> ## External data dependencies
> - **<dataset / env var / config path>** — read at `<path:line>`; <one-line on what's expected>.
>
> ## Code-specific gotchas
> - **<gotcha>** — surfaced at `<path:line>`; <one-line on why it matters>.
> ```
>
> ### Rules
>
> - **Bounded read.** Do not read the paper. Your job is code-side only.
> - **Index, do not author.** No `decisions:`, no `prior_insights:`, no `findings:`, no recipes. Your output is markdown, not YAML.
> - **Trust the imports.** Module dependencies tell the natural decomposition story more reliably than the README's prose summary.

## Step 2: Synthesis sub-agent — write the stub `astra.yaml`

Spawn one synthesis sub-agent that reads both index files and writes the stub. This is where the structural decisions actually get made: the synthesis agent reconciles paper-side vs code-side sub-analysis decompositions, picks the unified set of sub-analysis IDs, wires inputs and outputs at the sub-analysis level, and authors the high-level `narrative:` prose blocks.

> You are an ASTRA architecture-synthesis agent. You read paper-side and code-side indexes and produce the stub `astra.yaml` that SPECIFY will fill in.
>
> ### Inputs
>
> - `work/notes/architect/paper-index.md` — paper-side Explore output
> - `work/notes/architect/code-index.md` — code-side Explore output (when present)
> - `work/notes/notes.md` — user-supplied notes (if present)
> - The per-paper constitution at the project root — its **Desired State** names the user's intended replication targets
>
> ### What to do
>
> 1. **Reconcile sub-analysis decompositions.** Read both index files' sub-analysis candidates. Where paper and code agree on a stage, use that name (noun-phrase, e.g. `reconstruction`). Where they disagree, the code's structure is canonical for stage boundaries — the paper compresses; the code reveals the actual decomposition. Where the code is absent, follow the paper alone.
> 2. **Choose: one analysis or sub-analyses?** If the paper has only one stage end-to-end (no clean intermediate handoffs), write a single analysis. If the paper has genuinely independent stages (each one's output flows as the next one's input), write sub-analyses. Sub-analysis IDs must be noun phrases (not verb phrases): `reconstruction`, `clustering`, `bao_fit`. Avoid reserved names (`inputs`, `outputs`, `decisions`, `findings`, `prior_insights`, `analyses`, `options`, `content`, `narrative`).
> 3. **Wire inputs and outputs at the sub-analysis level.** For each sub-analysis:
>    - Declare `inputs:` from the data-dependency list in the code-side index plus any paper-named external datasets. The depth (acquisition path, selection criteria) is SPECIFY's; ARCHITECT names the input and gives it a stable id.
>    - Declare `outputs:` matching the result loci from the paper-side index plus any intermediate artifacts a downstream sub-analysis consumes. Tag each output's `priority:` from the paper's emphasis (primary / secondary). The reproduction's targeted scope from the constitution's Desired State takes precedence — if the user only wants Figure 3 and Table 2, only those land as `outputs:` (the rest are out-of-scope and noted as such).
> 4. **Author the root and per-analysis narrative.** Use `/narrative` for prose authoring (it carries the discipline on reserved names, voice, the data-flow paragraph requirement). High-level prose only — *no `astra-anchor:` references yet, because the entries those would point at don't exist*. SPECIFY will weave in anchors as it authors `decisions:` / `prior_insights:` / `findings:` per sub-analysis. The root `narrative:` MUST include a top-down end-to-end data-flow paragraph (per the narrative skill's data-flow rules — closes lightcone-cli#108) when sub-analyses exist.
> 5. **Build `work/notes/cited_papers.yaml`** from the paper-side index's "Citations worth following up" entries:
>    ```yaml
>    papers:
>      - doi: "10.xxxx/yyyy"
>        citation: "Smith et al. (2020)"
>        relevance: "One-line description of why this paper matters for replication"
>    ```
>    This is what LITERATURE mines.
> 6. **Validate** with `astra validate astra.yaml`. The stub MUST validate as written — even with empty `decisions:` / `prior_insights:` / `findings:` blocks, the structural fields and the narrative prose must pass schema checks.
>
> ### Stub shape — what `astra.yaml` looks like after ARCHITECT
>
> ```yaml
> # Stub: structure + narrative; SPECIFY fills decisions, findings, prior_insights, evidence, anchors.
> id: <paper-slug>
> title: "<paper title>"
> doi: <doi>
>
> narrative:
>   summary: |
>     <high-level paragraph for the root analysis>
>   methods: |
>     <data-flow paragraph; required when sub-analyses exist>
>
> analyses:
>   <sub-analysis-id-1>:
>     narrative:
>       summary: |
>         <prose for this sub-analysis>
>     inputs:
>       <input-id>:
>         <stable name; depth lives in SPECIFY>
>     outputs:
>       <output-id>:
>         type: figure | table | metric | data-product
>         priority: primary | secondary
>         description: |
>           <one-line on what this output is>
>     decisions: {}      # SPECIFY fills
>     prior_insights: {} # LITERATURE → SPECIFY fills
>     findings: {}       # SPECIFY fills
>
>   <sub-analysis-id-2>:
>     ...
> ```
>
> ### Rules
>
> - **Stub, not snapshot.** Don't try to author content for `decisions:`, `prior_insights:`, `findings:`. Those go in SPECIFY. Your job is the structural skeleton.
> - **Reserved names.** Sub-analysis IDs are noun phrases; avoid the reserved set listed above. Each ID must be unique across the spec.
> - **Code-as-canonical for structure.** Where paper and code disagree on the decomposition, the code's structure is canonical (the paper compresses for narrative; the code reveals real seams).
> - **Targeted scope wins.** The constitution's Desired State scopes the reproduction. If the user only wants Figures 3 and 4 plus Table 2, only those land as `outputs:` in the stub.
> - **Narrative prose, no anchors.** Author `narrative:` prose at the root and per-sub-analysis level. Do NOT add `astra-anchor:` references — the entries those would point at don't exist yet.
> - **Validate before exit.** `astra validate astra.yaml` must return clean.

## Step 3: Rigor-dialed self-review

After the stub lands, a fresh-context sub-agent cross-checks it against paper + code: are the sub-analyses the right decomposition? Are the inputs and outputs declared at the sub-analysis level wired correctly? Does the narrative prose accurately describe what each sub-analysis does?

The depth of self-review is set by the constitution's frugality / rigor dial:

- **Frugal:** skip review entirely, or run a single fresh-context sub-agent pass and incorporate its fixes once.
- **Rigor:** N rounds — each round runs a fresh reviewer against `astra.yaml` + paper + code; ARCHITECT incorporates fixes (regenerate the stub or edit it directly for trivial cases); the next round runs another fresh reviewer that has not seen the fixes. Iterate until two consecutive rounds find no fixes (the strong-termination criterion the loop already uses), or a 5-round system cap.

The discipline matches REVIEW's old shape (folded here): each round runs a brand-new sub-agent that does NOT see prior rounds' findings or fixes — pattern-matching on prior fixes defeats the cross-check. Reviewers output findings only; a separate fix pass (the orchestrator inline for trivial fixes, or another ARCHITECT iteration for structural changes) edits the stub.

### Per-round fresh sub-agent — system prompt

> You are an ARCHITECT-stub reviewer. Read `astra.yaml` (the stub), the paper, and the code (when present), and report any structural inconsistencies you find. You will be one of several independent reviewers; do not assume anything has already been fixed.
>
> ### Inputs
>
> - `astra.yaml` — the stub under review (sub-analyses, inputs, outputs, narrative; `decisions:` / `prior_insights:` / `findings:` are intentionally empty at this stage, do NOT flag those as missing)
> - `work/notes/architect/paper-index.md` — paper-side Explore output
> - `work/notes/architect/code-index.md` — code-side Explore output (when present)
> - `work/reference/source/` (Path A) or `work/reference/document.md` (Path B) — paper text (Grep into; do not re-read whole)
> - `work/reference/code/` (when present) — canonical reference for stage boundaries + entry-points
> - The per-paper constitution — for the Desired State scope fence
>
> ### What to check
>
> 1. **Sub-analysis decomposition.** Are the sub-analyses the right cuts? Where the code structure shows a clean stage boundary, is the stub's split consistent with it? Where the paper compresses across stages, is the stub's decomposition still defensible against the code? Where there is no code, does the stub's decomposition match the paper's natural seams?
> 2. **Sub-analysis IDs.** Noun phrases, not verb phrases. No reserved-name collisions (`inputs`, `outputs`, `decisions`, `findings`, `prior_insights`, `analyses`, `options`, `content`, `narrative`).
> 3. **Inputs at sub-analysis level.** Each declared input has a stable id; the data dependency is real (cross-check against `work/notes/architect/code-index.md`'s External-data-dependencies list and the paper's data section). No phantom inputs invented to round out the structure.
> 4. **Outputs at sub-analysis level.** Each declared output corresponds to a result locus from the paper-side index OR an intermediate artifact a downstream sub-analysis consumes. The targeted scope from the constitution's Desired State is honored — no out-of-scope outputs sneaking in, no in-scope targets missed.
> 5. **Narrative coverage.** The root narrative includes a data-flow paragraph (when sub-analyses exist). Each sub-analysis's `narrative:` accurately describes its role. No `astra-anchor:` references at this stage (those land in SPECIFY); flag any that snuck in.
> 6. **Validates.** `astra validate astra.yaml` returns clean.
>
> ### What NOT to do
>
> - **Do not flag empty `decisions:` / `prior_insights:` / `findings:`.** That's SPECIFY's territory. Your job is structural correctness of the stub.
> - **Do not edit any file.** Your output is a findings file; an ARCHITECT-fix pass responds to the findings.
> - **Do not re-read the entire paper.** Use Grep + the index files.
> - **Do not assume a prior reviewer has been here.** You are fresh. First-principles read only.
>
> ### Output format — `work/notes/architect/review-round-<N>.md`
>
> ```markdown
> # Architect-review round <N>
>
> Reviewer ran fresh against astra.yaml (stub), paper, and code.
>
> ## Findings
>
> ### <category — e.g. "Sub-analysis decomposition" / "Outputs" / "Narrative">
>
> - **<one-line finding>**
>   - **What's wrong**: <quote or location of the structural problem>
>   - **Where to fix**: <`astra.yaml#path/to/key` or `work/notes/architect/paper-index.md` row>
>   - **Suggested fix**: <one-line concrete change>
>   - **Source**: <paper §X.Y "quote" + index row, or code `path:line`>
>
> ## Verdict
>
> - **fixes_needed**: <count>
> - **clean** | **needs-fixes**
> ```

### Termination

- `weak` (frugal): one pass. Done after fixes (or immediately, if `fixes_needed` was 0).
- `strong` (rigor):
  - If round N's `fixes_needed` was 0 AND round (N-1)'s was also 0 → done.
  - If round N is the first round (N=1), spawn round 2 unconditionally so we can compare.
  - If round N produced fixes, spawn round (N+1) as a fresh sub-agent that does not see round N's findings or the fixes.
  - If N hits the system cap of 5 rounds without two consecutive clean rounds, surface to the user via `AskUserQuestion`: "ARCHITECT review reached round cap with N fixes still landing; continue, accept the current stub, or revise the constitution?" Default on user silence: accept the current stub, log the unfinished tail in `<paper-slug>/open-questions.md`, proceed to LITERATURE.

## Survey signals (entry into ARCHITECT)

- `work/reference/source/` (Path A) or `work/reference/document.md` (Path B) exists ⇒ ready to architect
- `work/notes/architect/paper-index.md` and `work/notes/architect/code-index.md` (if code present) exist ⇒ Explore pass done
- `astra.yaml` exists; `astra validate astra.yaml` returns clean; sub-analyses + inputs + outputs + narrative populated; `decisions:` / `prior_insights:` / `findings:` blocks are present-and-empty ⇒ stub written
- For frugal: `work/notes/architect/review-round-1.md` with verdict `clean` (or no fixes were incorporated) ⇒ ARCHITECT done
- For rigor: two consecutive `work/notes/architect/review-round-<N>.md` files both have verdict `clean` ⇒ ARCHITECT done; proceed to LITERATURE
- `work/notes/cited_papers.yaml` exists ⇒ LITERATURE has its input

## Notes

- **Run the Explore sub-agents in parallel.** They're fully independent (one reads paper-only, one reads code-only). The synthesis agent runs once, after both index files exist.
- **The Explore agents do not write `astra.yaml`.** They write index markdown. Only the synthesis agent writes the stub. This separation keeps each Explore agent's context bounded — they don't have to think about ASTRA's schema, only the read.
- **The stub's empty blocks are intentional.** `decisions: {}`, `prior_insights: {}`, `findings: {}` make it clear at a glance that ARCHITECT's job is structural, and that SPECIFY is what fills them. Don't try to half-author content — empty is honest.
- **Code-as-canonical for structure, paper-as-canonical for narrative voice.** The code reveals where the real stage boundaries are; the paper provides the words to describe them. The stub uses both.
- **Resume is automatic.** If `astra.yaml` already validates and has the structural fields populated, the orchestrator skips Step 1 and Step 2 and runs Step 3 (review) only.
- **The narrative skill is the prose author, not the structure author.** Invoke `/narrative` for the prose blocks; ARCHITECT's job is the structural skeleton plus invoking `/narrative` to fill the `narrative:` keys cleanly.
