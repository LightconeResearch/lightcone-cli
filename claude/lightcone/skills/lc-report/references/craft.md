# Report craft

Prose discipline for the report. The report does not duplicate the analysis — it cites into it. A reference is a citation; a sentence pointing at a decision is a small argument; the report is the layer where decisions, sub-analyses, findings, and outputs become a connected story.

## References carry the argument

- **Reference text is authored prose, not a raw id.** `{astra}`the post-reconstruction catalogs <reconstruction>`` reads as a sentence; `{astra}`reconstruction`` alone reads as a label. Use the `text <path>` form whenever the bare label wouldn't sit naturally in the sentence.
- **Inline references do the work of a citation.** Don't footnote or parenthesize what an inline reference can carry.
- **One reference per idea.** Three references stacked on a sentence means the sentence carries too much; split it or drop one.
- **Decision vs. selection.** `{astra}`decisions.algorithm`` names the *decision*; `{astra:value}`decisions.algorithm`` names the *option active under the universe*. Whenever the sentence depends on which option is active ("we reconstruct with **MultiGrid**"), use the value form so the prose updates with the universe.

## Make the data flow navigable

References are the trail a reader follows through the pipeline without leaving the prose.

- When Results discusses a sub-analysis's output, name its downstream consumers by reference: *"the reconstructed field feeds {astra}`the post-reconstruction BAO fit <bao_fit.outputs.xi_post>` and supports {astra}`the headline detection <findings.bao_detection>`."*
- The root page (`index.md`) is the end-to-end view: it traces raw inputs → sub-analyses → final outputs in a few sentences, with each stage referenced or embedded as a nav card. Details telescope into the sub-analysis pages.

## Length

1–3 paragraphs per section topic, at any level (root page, sub-analysis page). Length discipline is what keeps analyses modular, not a style preference: if the references don't fit in three paragraphs, the analysis is too big for one section — split it into a sub-analysis page. The report is a compressor; if it won't compress, split the thing being compressed.

## Sentence craft

- **Economy.** Every sentence introduces a new idea or sharpens one. Release real verbs: `conducted cross-correlation` → `cross-correlated`.
- **Specificity.** Names, references, and `{astra:value}` numbers over generic claims.
- **Arrive through content.** No "in this report we will describe…"; the content is the opening.

### Real subjects, real verbs

"We measure the BAO peak with the LRG sample" reads as agency. "The measurements of the BAO peak reveal a 7σ detection" reads as zombie-noun abstraction. The test: can you picture someone or something physically doing the verb? If not, rewrite.

Valid subjects:

- **We** — for decisions and actions ("we chose the Gaussian damping prior")
- **The thing itself** — for states and properties ("the covariance is dominated by shot noise")
- **Passive voice** — when the actor is obvious ("a redshift cut is applied")
- **Results / data as epistemic subjects** — for what the data shows ("the measurement shows a 7σ peak"; "Figure 2 reveals…")
- **Physics doing physics** — for physical processes ("lensing distorts shapes"; "higher-order effects produce B-modes")

Anthropomorphized abstractions fail the test: "the methodology validates," "this analysis demonstrates," "the catalogue evolution follows." Rewrite to a real subject doing a real verb.

## Anti-patterns

- **Wiki-style what-is framing.** "BAO is the baryon acoustic oscillation feature." A wiki summarizes; a report points into reasoning. Replace with the load-bearing statement and a reference: "we chose {astra}`the Gaussian damping prior <decisions.bao_damping_prior>` over flat because flat admitted spurious minima."
- **Decision-list paragraph.** "We made the following decisions: A, B, C." Reference each decision where it shapes the pipeline, not as recitation. Too many to weave coherently → the report wants a sub-analysis page.
- **Introduction as primer.** Teaching what the field is. Readers arrive with context; open with the question and the headline finding.
- **Describing what an embed already shows.** A paragraph enumerating a decision's options above `:::{astra} decisions.algorithm` `:::` duplicates the tabs the embed renders. The prose says why the decision matters; the embed says what it is.
- **Uncited declared elements.** Every declared finding, load-bearing decision, and promoted output belongs somewhere in the report. If an element genuinely isn't worth a mention, question whether it should be declared — surface that, don't pad.
