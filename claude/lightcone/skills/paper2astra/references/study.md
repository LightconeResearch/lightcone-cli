# STUDY — section-parallel paper-vs-code agreement check

Read the parsed paper and the reference code together — by section, with sub-agents fanning out across the paper's structure — and produce a cross-referenced agreement check that the rest of the pipeline consumes. STUDY is paper2astra's load-bearing read phase: its value isn't "summarize the paper" but **measure the level of agreement between paper and code at the section level**.

This phase replaces the old SUMMARIZE. The old shape parallelized one sub-agent on the paper and another on the code; that loses the cross-reference, since "the whole paper" and "the whole code" are too much context for one agent to compare meaningfully. The new shape parallelizes by **paper section + matching code**, so each sub-agent carries enough context to surface disagreements at its own level.

The constitution's per-phase mode is **always sub-agent (parallel by paper-section)** for this phase. Spawn one Task-tool sub-agent per paper section. After they finish, spawn a single synthesis sub-agent.

## Inputs

- `work/reference/source/` (Path A — arXiv LaTeX) **or** `work/reference/document.md` + `work/reference/figures/` + `work/reference/tables/` + `work/reference/metadata.json` (Path B — Docling)
- `work/reference/code/` — the reference code repo (when present)
- `work/notes/notes.md` — user-supplied prior notes, if any (read by every phase if present)

## Outputs

- `work/notes/study/<NN>-<section-slug>.md` — one file per paper section, with the cross-referenced agreement check
- `work/notes/methodology.md` — consolidated decision map, results inventory, data sources (derived from the per-section files; what SPECIFY consumes)
- `work/notes/cited_papers.yaml` — citations worth following up on for prior insights (what LITERATURE consumes)

## Step 1: Identify paper sections and their matching code

Before fanning out, the orchestrating call (the loop iteration that enters STUDY, before spawning sub-agents) does a quick survey:

1. **List the paper's sections.** Path A: `grep -E '^\\section\{' work/reference/source/*.tex`. Path B: read the `##` headings in `work/reference/document.md`. Skip front-matter (abstract, acknowledgments, author list) and back-matter (references, supplementary). Keep methods, results, and any analysis-bearing intro/discussion.
2. **Locate matching code per section.** Two routes:
   - **Code's own structure**: most analysis pipelines mirror the paper's flow (a `reconstruction/` module → reconstruction section, a `bao_fit.py` → BAO-fit section). Walk the code repo's top-level layout and infer the mapping.
   - **Paper-side bibliography**: when the paper cites a specific module, function, or commit (e.g. "the fitting code at `https://github.com/.../bao_fit.py:42`"), record that.
3. **Build a section→code map** as a small YAML file at `work/notes/study/section-map.yaml`:
   ```yaml
   sections:
     - id: 01-data
       paper_section: "Data and Sample Selection"
       paper_anchor: "section:data"        # \label{} or markdown anchor
       code_paths:
         - work/reference/code/data/
         - work/reference/code/scripts/load_catalog.py
     - id: 02-methods
       paper_section: "BAO Fitting Methodology"
       paper_anchor: "section:methods"
       code_paths:
         - work/reference/code/bao_fit/
       notes: "Paper §3 cites the fitting code in footnote 7."
   ```

   When a section has no obviously matching code (e.g. "Discussion"), record `code_paths: []` and let the section sub-agent flag claims that imply implementation but have no code anchor — those are signal.

This step matters because it sets the unit of work. A bad map (one sub-agent gets all the code, another gets none) loses the parallelism's value.

## Step 2: Fan out — one sub-agent per section

Spawn one Task-tool sub-agent per entry in `section-map.yaml`. Each sub-agent gets:

- The paper-section reference (the `.tex` file path + `\section{}` anchor for Path A; the `document.md` file + heading anchor for Path B)
- The list of code paths from the section map
- The decision-map context structure (so claims and code locations align with what SPECIFY will need)

### Per-section sub-agent — system prompt

> You are a paper-vs-code agreement-check agent for one section of a research paper. Your job is to read the paper section *together with* its matching code and produce a cross-referenced agreement assessment.
>
> ### Inputs
>
> - Paper section: `<path-to-tex-or-md>` anchored at `<section-anchor>`. Read this section in full — it is bounded; do not stray into other sections.
> - Code paths: `<list of paths>`. Read each in full; for directories, read the entry-points and follow imports as needed. **Do NOT modify any code.**
>
> ### What to extract
>
> For each material claim or choice in the paper section, locate its implementation in the code (or note its absence) and record an agreement assessment.
>
> A "claim" is anything where a different choice would plausibly change a numerical result the paper reports — methods, parameters, data cuts, calibrations, statistical approaches, hyperparameters, software versions.
>
> A "code location" is a `file:line` reference (or `file:line-line` range) to where the code implements (or fails to implement) that claim.
>
> An "agreement level" is one of:
>
> - `matches`: paper says X, code does X. Cite the line(s); brief one-line note.
> - `minor-deviation`: paper and code differ in a way that does not change the numerical result (e.g. variable named differently, equivalent algorithm, refactored computation). Cite both, name the equivalence.
> - `material-disagreement`: paper and code differ in a way that plausibly changes a numerical result. Cite both verbatim. **Surface this prominently** — these are SPECIFY's seams.
> - `paper-only`: paper claims something the code does not implement. May indicate a methodological description not yet realized in the available code.
> - `code-only`: code does something the paper does not describe. Often a critical detail the paper compressed; flag it.
>
> ### Output format — `work/notes/study/<id>-<slug>.md`
>
> ```markdown
> # Study: <Section title>
>
> Paper anchor: `<section-anchor>` in `<paper-source-path>`.
> Code paths: <list>.
>
> ## Agreement table
>
> | Claim | Paper | Code | Agreement | Notes |
> |---|---|---|---|---|
> | <one-line claim> | §X.Y "<short quote>" | `path:line` | matches \| minor-deviation \| material-disagreement \| paper-only \| code-only | <one-line gloss> |
>
> ## Material disagreements
>
> For every `material-disagreement` row, expand here:
>
> ### <Claim>
>
> - **Paper says** (quote): "..." (page N, eq. M)
> - **Code does** (quote): `path:line-line`:
>   ```python
>   <code excerpt>
>   ```
> - **Why it matters** (one-line plausible-impact): <e.g. "changes the BAO peak amplitude by ~5%">
> - **Default per canonical-resolution rule**: <code | paper> — applied if SPECIFY runs sub-agent.
>
> ## Decisions surfaced
>
> Bullet list of choices in this section that should become first-class decisions in `astra.yaml`. Group by "what" + "why" + "alternatives" (mirroring the methodology.md decision-map shape).
>
> ## Cited papers worth following up
>
> List citations from this section that informed a decision (not general background). DOI when resolvable + one-line on why.
>
> ## Data sources (this section)
>
> Any external dataset, catalog, or archive this section's analysis consumes. For each: name + version, exact acquisition path (URL / query / package name), selection criteria.
>
> ## Open questions
>
> Anything ambiguous, missing, or contradictory that this section couldn't resolve from paper + code alone. Append to `<paper-slug>/open-questions.md` from outside the sub-agent (the orchestrator does this; sub-agents append silently to this section).
> ```
>
> ### Style
>
> Be concise but precise. Use bullets and tables. Quote the paper verbatim and cite `path:line` for the code. Do NOT pad with background.
>
> ### Rules
>
> - **Stay in your section.** Cross-references to other sections are notes, not extractions. If a section's claim depends on a definition from another section, note the dependency and continue.
> - **Quote, don't paraphrase**, when surfacing a paper-vs-code disagreement. SPECIFY needs the verbatim claim to author evidence-quote-backed findings.
> - **Code-as-canonical when both exist.** Where paper and code disagree, the code wins for numerics + method (the canonical-resolution rule). Record both, mark the agreement level as `material-disagreement`, surface the disagreement.
> - **Never block on `AskUserQuestion`.** You're a sub-agent; the user is not in this conversation. Append to the section's `## Open questions` block instead.

## Step 3: Synthesize — single sub-agent merges into methodology.md and cited_papers.yaml

Spawn one synthesis sub-agent that reads all `work/notes/study/<id>-<slug>.md` files and writes:

- `work/notes/methodology.md` — consolidated decision map (every "Decisions surfaced" entry merged across sections), results inventory (split into primary / secondary), data sources (every "Data sources" entry merged).
- `work/notes/cited_papers.yaml` — every "Cited papers worth following up" entry merged and de-duplicated.

### Synthesis sub-agent — system prompt

> You are a research-paper synthesis agent. Read every per-section file in `work/notes/study/` and merge them into a single `work/notes/methodology.md` and `work/notes/cited_papers.yaml`.
>
> ### Task
>
> 1. Read every `work/notes/study/<id>-<slug>.md` file (skip `section-map.yaml`).
> 2. Build `work/notes/methodology.md` with three sections:
>    - **Decision map**: every "Decisions surfaced" entry across all sections, grouped by pipeline stage. For each decision: what was chosen, why (cite the section + paper-citation), alternatives mentioned, and any *material-disagreement* with the code (cite the section's `Material disagreements` block).
>    - **Results inventory**: every primary and secondary result the paper reports, grouped primary/secondary, with which decisions feed into each.
>    - **Data sources**: every external dataset across sections, with name + version, acquisition path, selection criteria, format. **This section is critical** — IMPLEMENT will use it to write data download scripts. If acquisition is vague, flag it.
> 3. Build `work/notes/cited_papers.yaml` from the de-duplicated cited-papers entries:
>
>    ```yaml
>    papers:
>      - doi: "10.xxxx/yyyy"
>        citation: "Smith et al. (2020)"
>        relevance: "One-line description of why this paper matters for replication"
>    ```
>
> ### Style
>
> Cross-reference back to the per-section files (`see work/notes/study/03-bao-fit.md`) for the verbatim quotes and code locations. methodology.md is the consolidated view; the per-section files are the source of truth for evidence.
>
> ### Output skeleton — `work/notes/methodology.md`
>
> ```markdown
> # Methodology — consolidated study
>
> ## Decision map
>
> ### <Pipeline stage>
>
> - **<Decision name>**
>   - **What**: <chosen value/method>
>   - **Why**: <citation, e.g. "Smith+2020">. Section: `work/notes/study/<NN>-<slug>.md`
>   - **Alternatives**: <list>
>   - **Code agreement**: matches | minor-deviation | material-disagreement (see `work/notes/study/<NN>-<slug>.md#material-disagreements`)
>
> ## Results inventory
>
> ### Primary
> - <result> — feeds from <decisions>; expected: <values>; section: `work/notes/study/<NN>-<slug>.md`
>
> ### Secondary
> - <result> — feeds from <decisions>; expected: <values>; section: `work/notes/study/<NN>-<slug>.md`
>
> ## Data sources
>
> - **<Dataset name + version>**
>   - Obtain: <URL / query / package>
>   - Selection: <cuts>
>   - Format: <columns/fields>
>   - Used in: <list of sections>
> ```
>
> ### Rules
>
> - Preserve paper citations exactly as they appear in the source per-section files.
> - Do NOT introduce decisions that aren't in any per-section file's "Decisions surfaced" block.
> - When two sections name the same decision, merge — do not duplicate.

## Step 4: Append open questions to the running report

After the per-section sub-agents finish (and before the synthesis runs), the orchestrator scans each `work/notes/study/<id>-<slug>.md` for `## Open questions` entries and appends them to `<paper-slug>/open-questions.md` with the section as origin. The user resolves these in SUMMARIZE_RUN.

## Survey signals (entry into STUDY)

- `work/reference/source/` (Path A) or `work/reference/document.md` (Path B) exists ⇒ ready to study
- `work/notes/study/section-map.yaml` exists ⇒ section identification done
- Every section in `section-map.yaml` has a corresponding `work/notes/study/<id>-<slug>.md` ⇒ per-section pass done
- `work/notes/methodology.md` and `work/notes/cited_papers.yaml` exist ⇒ STUDY done; proceed to LITERATURE

## Notes

- **Run the section sub-agents in parallel.** They're fully independent (each reads its own paper section + code paths). The synthesis sub-agent runs once, after all per-section files exist.
- **The agreement check is the value.** A study that reads only the paper or only the code is a regression to the old SUMMARIZE — do not allow a section sub-agent to skip the code (or vice versa) unless that section genuinely has no matching code (and that absence itself is information, recorded as `paper-only` rows).
- **methodology.md is the door, not the source of truth.** SPECIFY drills back into the per-section files via the `see work/notes/study/...` pointers when authoring evidence-quote-backed findings. Do not bloat methodology.md with verbatim quotes; keep it as the consolidated view and let the per-section files carry the evidence.
- **Section granularity earns separate insights.** When a section's analysis builds on a method defined in another section, file the agreement check for the *defining* section there and note the dependency in the using section. Do not collapse all the borrowed pieces into the application section's row.
- **Resume is automatic.** If a per-section file already exists, the orchestrator skips its sub-agent. The synthesis re-runs whenever the set of per-section files changes.

## Output format — open question

The constitution flags whether `astra.yaml`'s `prior_insights` shape can absorb STUDY's per-section output directly. The current answer is **no**: `prior_insights` is for *cited* papers' findings supporting the *target* paper's decisions; STUDY's output is the *target paper's own claims* checked against *its own code*. The natural ASTRA homes for STUDY's output are downstream, in SPECIFY: paper-claim quotes become `findings` evidence in `astra.yaml`; code locations become decision-option metadata or implementation-notes. The per-section files stay as the source of truth; methodology.md is the consolidated derivation. Revisit if the spec gains a structure for "paper-vs-code agreement-check evidence" as a first-class entity.
