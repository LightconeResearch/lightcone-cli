# COMPARE — judge the match, name the opportunities

Compare reproduced results against the paper's replication targets. COMPARE returns two things: a **verdict** (pass / partial / fail) and an **opportunity assessment** — where the gaps are, how much they likely matter, and how they sit relative to the user's fidelity intent in CLAUDE.md's Goal section. The verdict drives whether the orchestrator re-spawns IMPLEMENT for another retry; the opportunity assessment tells the orchestrator (and the user) which gaps fall below intent and would be high-leverage to close, even on `pass`. Together they replace the old yes/no framing.

This phase runs as the orchestrator-spawned `compare` sub-agent. The orchestrator and the user together decide what to do with COMPARE's output — spend another IMPLEMENT round now (close a below-intent gap), accept the current verdict and proceed to REVIEW, or land at the current trajectory and log the gap as an open opportunity in CLAUDE.md's **Rigor** section. The user can drop into the compare sub-agent's chat for the verdict ratification conversation, or wait until REVIEW close-out.

## Inputs

- `targets/targets.md` — target ledger with priorities, expected values, comparison guidance
- `astra.yaml` — output definitions (each target maps to an output)
- `targets/` — reference figures / tables for comparison
- `results/<universe>/<output_id>/` — reproduced results
- **paper-expert** (agent ID passed in by the orchestrator) — reachable via `SendMessage`. Useful for "what does the paper actually claim for this number" or "how does the paper describe what Figure 3 should show" when grading the comparison.
- **code-expert** (agent ID passed in by the orchestrator) — reachable via `SendMessage`. Useful for diagnosing divergence: "what does the reference code compute here that ours might miss".

## Outputs

- `comparison-report.yaml` — structured verdict
- `comparison-report.md` — human-readable summary

## Result path convention

For an output with `id: X`, the reproduced result lives at `results/<universe_id>/X.<ext>`:

- metrics: `.json` containing `{"value": ...}`
- figures: `.png`
- tables: `.csv`

## Task

1. **Read `targets/targets.md`.** Every replication target with its priority, expected values, comparison guidance, and the path to its reference file in `targets/`.
2. **Read `astra.yaml`.** Outputs correspond to targets. Match each target to its output.
3. **For every target**, find its reproduced result in `results/<universe_id>/` and compare against the reference file in `targets/`. Missing results are `match: false`.
4. **Write `comparison-report.yaml` and `comparison-report.md`.**

## Comparison guidance

**Metrics.** Judge whether the reproduced value is scientifically equivalent to the expected value from `targets/targets.md`. Numerical tolerance comes from the target's stated precision; bare match is not the bar.

**Figures.** Read the reference figure from `targets/` and compare to the reproduced image. Focus on shape / trend, axis ranges, key features (peaks, inflections, curve ordering), and magnitudes. **Do NOT require pixel-perfect matches** — stochastic methods produce variation. Judge whether the same scientific conclusion follows from both figures.

**Tables.** Compare key values noted in `targets/targets.md` first, then remaining values. Reference tables are in `targets/`.

## Output: `comparison-report.yaml`

```yaml
verdict: pass|partial|fail
attempt: <attempt_number>
outputs:
  <output_id>:
    type: metric|figure|table
    priority: high|medium|low
    paper_value: "<from targets/targets.md>"
    reproduced_value: "<from results>"
    reference_file: "<path in targets/>"
    reproduced_file: "<results/...>"
    match: true|false
    notes: "<what matches, what differs>"
failure_diagnosis: null|"<root cause>"
fix_suggestions:
  - "<specific actionable suggestion with script and line number>"
opportunities:
  - area: "<which output / sub-analysis / decision>"
    gap: "<what could be tightened — even if the target matched>"
    leverage: "<rough sense of impact: 'changes headline number by ~10%' / 'cosmetic only' / 'unknown'>"
    fix_pointer: "<where the fix would land — script:line, decision id, or implementation-notes section>"
    relative_to_intent: above|at|below
```

## Verdict rules

- **`pass`**: ALL high-priority targets match, no major issues with medium-priority.
- **`partial`**: some high-priority match, or all high-priority match but medium has issues.
- **`fail`**: most high-priority don't match, or fundamental methodological issue.

If verdict is not `pass`, **`fix_suggestions` MUST reference specific scripts and line numbers**. "The result is wrong" is not actionable; "scripts/bao_fit.py:42 uses `damping_prior=flat`, paper specifies Gaussian; change to gaussian per Howlett+2017 §4.2" is.

## Opportunity assessment rules

The `opportunities:` block surfaces **gaps that didn't necessarily fail the verdict but would be high-leverage to close**. Examples worth flagging:

- A primary-target match was within tolerance but the underlying method is a sketch (e.g. simplified noise model that happens to land in the right range — tightening it would change the headline by O(10%)).
- A secondary target failed but is plausibly fixable from the same root cause as a primary that passed (one fix, two outputs).
- A decision SPECIFY recorded with code-as-canonical that has an unresolved disagreement still in `open-questions.md` and could move the result.
- A sub-analysis whose evidence quotes are paraphrased rather than verbatim (would fail `--verify-evidence` if pushed harder).

Each opportunity gets two grades: a **leverage** one-liner (impact if closed) and a **relative_to_intent** placement against the user's fidelity intent in CLAUDE.md's Goal section:

- `below` — the user's intent calls for tighter than this; closing the gap moves the reproduction toward what they actually want.
- `at` — closing the gap reaches the intent; further tightening would be gravy.
- `above` — already past the intent; log it but it doesn't pull on attention.

Read the Goal's fidelity intent prose to make the call. "Figure 3 must be right" + a sketch-level figure 3 systematics = `below`. "Just checking the analysis is tractable" + a canonical-grade outputs block + a sketchy sub-analysis = `above` everywhere except the headline. When intent is silent on something, default to `at` for primary targets, `above` for secondaries.

Empty `opportunities:` is a strong signal — say "the reproduction is at canonical rigor across the targets" rather than padding.

Also write `comparison-report.md` with a human-readable summary. For figure / table comparisons, describe what you see in both and explain your match judgment. Include the opportunity assessment as its own section — group by `relative_to_intent` so the `below` items lead.

## Verdict + opportunity surfacing

After writing the report, the compare sub-agent reports back to the orchestrator with the verdict, the failing-output count (if any), and the headline opportunities — `below`-intent items first. The orchestrator either:

- **Carries the report to the user** (if the user is reachable in the orchestrator session or the compare sub-agent's chat) for ratification: present verdict, the failing outputs (if `partial` / `fail`), and the top `below`-intent opportunities; ask whether to spend another IMPLEMENT round on those gaps, accept and proceed to REVIEW, or land at the current trajectory and log the gaps as open opportunities in CLAUDE.md.
- **Acts against intent** (if the user is unreachable): if attempt < budget AND (verdict is `partial` / `fail` OR any opportunity is `below` intent), re-spawn `implement` targeting the `below` gaps first; if verdict is `pass` AND no opportunities are `below`, OR attempt >= budget, log remaining opportunities in CLAUDE.md's **Rigor** section and proceed to REVIEW.

The verdict is the compare sub-agent's judgment; the **decision to keep iterating or move on** is the orchestrator's (in dialogue with the user). The opportunity assessment — graded against the user's fidelity intent — is the bridge that turns a binary verdict into a picture both parties can navigate.

## Survey signals (entry into COMPARE)

- All outputs in `lc status --universe baseline` are `ok` ⇒ ready to compare
- `comparison-report.yaml` exists with current `attempt` ⇒ COMPARE done for this attempt
- `comparison-report.yaml` verdict is `pass` (or `partial` accepted) ⇒ COMPARE → IMPLEMENT loop terminated; orchestrator proceeds to REVIEW close-out

## Notes

- **One COMPARE per IMPLEMENT.** Each IMPLEMENT retry produces a fresh COMPARE; the report's `attempt` field increments. Do not overwrite prior reports — keep them at `comparison-report-attempt-<N>.yaml` if useful, or commit each between attempts so `git log` carries the history.
- **The verdict is the compare sub-agent's; the keep-iterating decision is the orchestrator's** (in dialogue with the user, when reachable). Treat them as separate.
- **The opportunity assessment is part of the durable record.** When the user accepts the current verdict, propagate the un-acted-on opportunities into CLAUDE.md's **Rigor** section's *Open opportunities* list. Future sessions and future-Cail returning to this reproduction see them; tightening any becomes a re-spawn of IMPLEMENT against a clearer target.
