# COMPARE — judge the match, name the opportunities

Compare reproduced results against the paper's replication targets. COMPARE returns two things: a **verdict** (pass / partial / fail) and an **opportunity assessment** — where the gaps are and how much they likely matter. The verdict drives whether the orchestrator re-spawns IMPLEMENT for another retry attempt; the opportunity assessment tells the orchestrator (and the user) which gaps would be high-leverage to close, even on `pass`. Together they replace the old yes/no framing.

This phase runs as the orchestrator-spawned `compare` sub-agent. The orchestrator and the user together decide what to do with COMPARE's output — spend another IMPLEMENT round now (close a high-leverage gap), accept the current verdict and proceed to REVIEW, or land at the current rigor level and log the gap as an open opportunity in CLAUDE.md's **Rigor** section. The user can drop into the compare sub-agent's chat for the verdict ratification conversation, or wait until REVIEW close-out.

## Inputs

- `targets/targets.md` — target ledger with priorities, expected values, comparison guidance
- `astra.yaml` — output definitions (each target maps to an output)
- `targets/` — reference figures / tables for comparison
- `results/<universe>/<output_id>/` — reproduced results

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

Each opportunity gets a leverage one-liner so the orchestrator and user can decide where to spend attention. Empty `opportunities:` is a strong signal — say "the reproduction is at canonical rigor across the targets" rather than padding.

Also write `comparison-report.md` with a human-readable summary. For figure / table comparisons, describe what you see in both and explain your match judgment. Include the opportunity assessment as its own section.

## Verdict + opportunity surfacing

After writing the report, the compare sub-agent reports back to the orchestrator with the verdict, the failing-output count (if any), and the headline opportunities. The orchestrator either:

- **Carries the report to the user** (if the user is reachable in the orchestrator session or the compare sub-agent's chat) for ratification: present verdict, the failing outputs (if `partial` / `fail`), and the top opportunities; ask whether to spend another IMPLEMENT round on a high-leverage gap, accept and proceed to REVIEW, or land at this rigor level and log the gaps as open opportunities in CLAUDE.md.
- **Acts on standing rigor settings** (if the user is unreachable): if attempt < budget AND verdict is `partial` / `fail`, re-spawn `implement` for a retry; if verdict is `pass` OR attempt >= budget, log opportunities in CLAUDE.md's **Rigor** section as open opportunities and proceed to REVIEW.

The verdict is the compare sub-agent's judgment; the **decision to keep iterating or move on** is the orchestrator's (in dialogue with the user). The opportunity assessment is the bridge — it turns a binary verdict into a graded picture the user can navigate.

## Survey signals (entry into COMPARE)

- All outputs in `lc status --universe baseline` are `ok` ⇒ ready to compare
- `comparison-report.yaml` exists with current `attempt` ⇒ COMPARE done for this attempt
- `comparison-report.yaml` verdict is `pass` (or `partial` accepted) ⇒ COMPARE → IMPLEMENT loop terminated; orchestrator proceeds to REVIEW close-out

## Notes

- **One COMPARE per IMPLEMENT.** Each IMPLEMENT retry produces a fresh COMPARE; the report's `attempt` field increments. Do not overwrite prior reports — keep them at `comparison-report-attempt-<N>.yaml` if useful, or commit each between attempts so `git log` carries the history.
- **The verdict is the compare sub-agent's; the keep-iterating decision is the orchestrator's** (in dialogue with the user, when reachable). Treat them as separate.
- **The opportunity assessment is part of the durable record.** When the user accepts the current verdict, propagate the un-acted-on opportunities into CLAUDE.md's **Rigor** section's *Open opportunities* list. Future sessions and future-Cail returning to this reproduction see them; tightening any becomes a re-spawn of IMPLEMENT against a clearer target.
