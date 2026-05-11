# Crafting

How to help the user think through something that hasn't crystallized, and turn the result into structured commitments — fields on an `astra.yaml` if you're inside an analysis (decisions with excluded options, evidence pointers, scoped findings), or inline structure in the constitution itself.

Use it when the user is deciding something non-trivial, scoping a sub-analysis, drafting a living spec, or talking through an open question — any time careful interactive thinking is happening and the output can land in structured form.

The rhythm is two diamonds: first understand what the thing IS, then decide what to DO about it. Each diamond diverges to explore and converges to commit. The ontological question — *what IS this, really?* — is the convergence point of the first diamond, and it is the most practical question you can ask.

```
    ◇ Wonder              ◇ Design
   ╱  (diverge)          ╱  (diverge)
  ╱    surface          ╱    alternatives
 ╱     questions       ╱     trade-offs
●─────────────────────●─────────────────────●
 ╲                     ╲
  ╲    crystallize      ╲    commit
   ╲   the name          ╲   with reasons
    ◇  (converge)         ◇  (converge)
    Ontology              Delivery
```

Diamond 1 diverges into questions and converges on a name (*"this IS a decision about covariance estimation"*). Diamond 2 diverges into alternatives and converges on a commit (a default with `excluded_reason` for each rejection). The second diamond inherits the ontological commit from the first.

---

## The two diamonds

### Diamond 1: Wonder → Ontology

**Wonder (diverge).** What are we actually trying to figure out? Surface questions, assumptions, ambiguities. Do not propose answers yet. If the user is already pitching solutions, back them up to the question.

**Ontology (converge).** What IS this, really? Crystallize into a claim, decision, or question specific enough to act on. The convergence is complete when you can **name** the thing precisely — "this is a decision about covariance estimation" or "this is a question about whether leakage matters below ℓ=100." A good name is often the entire output of Diamond 1.

**Output of Diamond 1:** a stub with a real name and at least one structural placeholder — a decision label, an insight claim, or input/output IDs. Not a full block — just the hook that identifies what kind of thing this is.

### Diamond 2: Design → Delivery

**Design (diverge).** What are the real alternatives? For each, what would make it right or wrong? Trade-offs, excluded options, edge cases. This is where the Contrarian and Simplifier stances are most useful.

**Delivery (converge).** Commit to a default, write the `excluded_reason` for each rejected option, identify inputs and outputs, stage the evidence. The structure is now formalizable.

**Output of Diamond 2:** structured fields populated — `decisions` with options and default, `inputs`/`outputs` with IDs and types, `insights` with claim and evidence (in `astra.yaml` or in the constitution itself).

The two diamonds are sequential but the boundary is soft. If you find yourself naming alternatives before the thing is clear, back up to the ontology convergence point. If you converge too early on "this is a decision" when it is actually a question, the Design phase will feel forced — that is the cue to re-enter Wonder.

---

## Stances

Six lightweight lenses for when the conversation needs pressure. **Default is no stance** — straight conversation. Invoke a stance when pressure would help, announce it in one sentence, drop it when it has done its work. Do not stack or pipeline them.

### Socratic — *"What are you assuming?"*

Question-only. Never proposes answers. Surfaces the assumptions under the user's framing.

- What are you assuming is true that might not be?
- What would make option A right vs option B? What is the actual fork?
- If you had to write the `excluded_reason` for the option you are about to reject, what would it say?

**Use in Wonder and early Design.** When the user is about to commit to a path and you want the reasons made explicit.

### Ontologist — *"What IS this, really?"*

Pushes on definition before mechanism. Four questions:

1. **Essence** — what is the true nature, stripping away accidental properties?
2. **Root cause or symptom** — is this the fundamental issue or a surface effect?
3. **Prerequisites** — what must exist first for this even to make sense?
4. **Hidden assumptions** — what implicit beliefs is the framing resting on?

**Use at the Ontology convergence point.** When a word is doing heavy lifting and may mean different things in different sentences.

### Contrarian — *"What if the opposite were true?"*

Challenges premises, not details.

- What if the choice does not actually matter for your signal?
- What if the constraint you are designing around is not real?
- What if the simplest version is already good enough?

**Use in Design.** When the conversation is burning effort on a distinction that may not matter, or a third option (do nothing, use the default) is being ignored.

### Simplifier — *"Is this complexity earning its keep?"*

YAGNI, concrete first, data over code.

- What can we remove without losing the core value?
- What is the simplest version that would work?
- Can a data structure replace this logic?

**Use in Design and early Delivery.** When the design is drifting toward over-engineering or a feature list is growing without anchoring reasons.

### Researcher — *"What do we actually know?"*

Evidence before interpretation. Especially useful for scientific work where a claim needs to be defensible.

- What does the actual source say, not what we remember?
- What would count as evidence here? What would falsify the claim?
- What is the most specific claim we can make with the data in hand?

**Use in Delivery.** When an insight needs a defensible claim, or when the user is about to write an outcome that is stronger than the evidence supports.

### Architect — *"If we started over, would we build it this way?"*

Structural root cause. The question behind the question when friction keeps recurring.

- Is the same problem showing up in different forms?
- Which abstraction does not match reality?
- What assumption was wrong from the start?

**Use when a debate keeps returning.** The user is circling a decision they have already made three times and cannot stick to — the real question is probably structural, not tactical.

---

## The funnel

When the conversation is exploratory — no single topic, things are accumulating — keep a private running ledger of what is falling out, classified by destination:

| Item kind | What it looks like | Destination |
|-----------|--------------------|-------------|
| **Decision** | A choice between real alternatives | `decisions` block in `astra.yaml` / spec |
| **Finding** | A claim with at least the start of evidence | `findings` block in `astra.yaml` / spec |
| **Sub-analysis** | "Compute X from Y" with identifiable inputs/outputs | New `astra.yaml` sub-analysis |
| **Question** | An open thread worth tracking, not yet answered | "Open Questions" section of the constitution |
| **CLAUDE.md change** | A pattern or gotcha that belongs in project memory | Edit CLAUDE.md |

The ledger is your own working memory. **Do not surface it mid-conversation** unless the user asks or a flush cue fires.

**Flush cues:**

- User says "OK we should write this down" or similar
- Three or more items have accumulated and the topic is about to shift
- A natural pause after a decision or finding lands

On flush, present the ledger grouped by destination, then file with the user's assent. If the user declines an item, discard it without argument.

---

## Qualitative ambiguity self-check

Before committing to a path — filing a decision, launching an iteration loop, sealing an outcome — check three things qualitatively. **No scoring, no thresholds.** If any feels fuzzy, resolve it with AskUserQuestion.

1. **Goal.** Is what the user wants specific enough that two competent people would build the same thing from it? If not, what would pin it down?
2. **Constraints.** Are the limits named? What cannot change, what must be preserved, what would break everything? Missing constraints tend to show up as "oh wait, we also need…" after the commit.
3. **Success.** How will we know it is done or right? What is the evidence condition? Qualitative is fine ("a reviewer can follow the narrative cold"), but it has to be checkable.

When one is fuzzy, use AskUserQuestion with concrete options rather than open prose questions. Iterate until the answer is "yeah, that's it." **Stop when the fuzziness resolves, not when a score crosses a threshold.** Scores on qualitative priors add false precision; the honest signal is whether the user knows what they want.

This is a mirror, not a gate. If the user wants to file anyway with one dimension still fuzzy, file it — the fuzziness itself can live in an Open Questions section, and future iterations can refine it.

---

## Mapping outputs to structure

What comes out of the diamonds maps onto wherever you keep structured commitments:

| Diamond output | Destination |
|----------------|-------------|
| Wonder questions left open | "Open Questions" section in the constitution |
| Ontology convergence — "this IS a decision about X" | A `decisions.<key>.label` entry — in `astra.yaml` or in the constitution body |
| Design alternatives with trade-offs | `decisions.<key>.options`; rejected options get `excluded_reason` |
| Delivery — the commit | `decisions.<key>.default` |
| Finding at end of Delivery | `findings.<key>` with `claim` + `evidence` (or in `astra.yaml`) |
| Sub-analysis scope | New sub-analysis in `astra.yaml` |
| Process-level lesson that generalizes | Edit to project CLAUDE.md |

The same shapes apply directly inline in `astra.yaml` or the constitution itself; no separate substrate is required.

---

## Anti-patterns

- **Ambiguity gates.** Do not withhold help until the user clarifies N dimensions. The self-check is a mirror, not a door.
- **Numerical scoring.** Do not introduce 0–1 clarity scores with thresholds. The underlying signal is qualitative and the number adds false precision.
- **Stance pipelines.** Do not run Socratic → Ontologist → Contrarian in sequence. Pick one when it helps; drop it when it has.
- **Mandatory interview.** No prepared question list. Stances are responsive to the actual conversation.
- **Surfacing the ledger too early.** A single item is not a flush. Wait for accumulation or a pause.
- **Immutable outputs.** Nothing filed here is locked. Everything is editable; reversals are normal.
- **Nine-minds overload.** Six stances is already generous. Add more only when a specific gap shows up, never preemptively.
- **Interrogation without a ceiling.** Three questions is usually enough. If the user is getting irritated, stop asking and file what you have.
- **Converging before the name is clear.** If Diamond 2 feels forced, Diamond 1 has not finished. Back up.
