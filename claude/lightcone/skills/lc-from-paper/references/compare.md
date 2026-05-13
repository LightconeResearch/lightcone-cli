# COMPARE — judge the match, name the opportunities

Compare reproduced results against the paper's replication targets. COMPARE returns two things: a **verdict** (pass / partial / fail) and an **opportunity assessment** — where the gaps are, how much they likely matter, and how they sit relative to the user's fidelity intent in `constitution.md`'s Goal section. The verdict drives whether a subsequent iteration retries IMPLEMENT; the opportunity assessment tells the next iteration (and the user at REVIEW) which gaps fall below intent and would be high-leverage to close, even on `pass`. Together they replace the old yes/no framing.

COMPARE is what a ralph iteration does when the workdir signals "RUN done (`results/` materialized) + `comparison-report.yaml` absent or stale relative to latest RUN." The iteration writes the report; what happens next depends on the verdict and the iteration's read of the constitution's Fidelity intent. If verdict is `partial`/`fail` AND an opportunity is below intent AND attempt budget remains, the next iteration takes a retry attempt at IMPLEMENT against the failing outputs first. If verdict is `pass` AND no opportunities are below intent (or budget is exhausted), the iteration logs un-acted opportunities into CLAUDE.md's *Open opportunities*; a subsequent cold-survey iteration with no contributions closes the constitution and REVIEW runs in the user's main session.

## Inputs

- `targets/targets.md` — target ledger with priorities, expected values, comparison guidance
- `astra.yaml` — output definitions (each target maps to an output)
- `targets/` — reference figures / tables for comparison
- `results/<universe>/<output_id>/` — reproduced results
- `work/reference/source/` (Path A) or `work/reference/document.md` (Path B) — target paper text. Grep into for "what does the paper actually claim for this number" or "how does the paper describe what Figure 3 should show" when grading the comparison.
- `work/reference/code/` (when present) — read targeted modules pointed at by `code-index.md` for diagnosing divergence: "what does the reference code compute here that ours might miss".

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
    priority: primary|secondary
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

- **`pass`**: ALL primary targets match, no major issues with secondary targets.
- **`partial`**: some primary targets match, or all primary match but secondary has issues.
- **`fail`**: most primary targets don't match, or fundamental methodological issue.

If verdict is not `pass`, **`fix_suggestions` MUST reference specific scripts and line numbers**. "The result is wrong" is not actionable; "scripts/bao_fit.py:42 uses `damping_prior=flat`, paper specifies Gaussian; change to gaussian per Howlett+2017 §4.2" is.

## Opportunity assessment rules

The `opportunities:` block surfaces **gaps that didn't necessarily fail the verdict but would be high-leverage to close**. Examples worth flagging:

- A primary-target match was within tolerance but the underlying method is a sketch (e.g. simplified noise model that happens to land in the right range — tightening it would change the headline by O(10%)).
- A secondary target failed but is plausibly fixable from the same root cause as a primary that passed (one fix, two outputs).
- A decision SPECIFY recorded with code-as-canonical that has an unresolved disagreement still in `open-questions.md` and could move the result.
- A sub-analysis whose evidence quotes are paraphrased rather than verbatim (would fail `--verify-evidence` if pushed harder).

Each opportunity gets two grades: a **leverage** one-liner (impact if closed) and a **relative_to_intent** placement against the user's fidelity intent in `constitution.md`'s Goal section:

- `below` — the user's intent calls for tighter than this; closing the gap moves the reproduction toward what they actually want.
- `at` — closing the gap reaches the intent; further tightening would be gravy.
- `above` — already past the intent; log it but it doesn't pull on attention.

Read the Goal's fidelity intent prose to make the call. "Figure 3 must be right" + a rough figure 3 systematics = `below`. "Just checking the analysis is tractable" + a tight outputs block + a rough sub-analysis = `above` everywhere except the headline. When intent is silent on something, default to `at` for primary targets, `above` for secondaries.

Empty `opportunities:` is a strong signal — say "the reproduction reaches the fidelity intent across the targets" rather than padding.

Also write `comparison-report.md` with a human-readable summary. For figure / table comparisons, describe what you see in both and explain your match judgment. Include the opportunity assessment as its own section — group by `relative_to_intent` so the `below` items lead.

## Verdict + opportunity surfacing

After writing the report, the iteration acts against the fidelity intent (iterations run detached; the user isn't reachable interactively):

- If attempt < budget AND (verdict is `partial` / `fail` OR any opportunity is `below` intent), commit the report, exit. The next iteration surveys, sees the report's `below`-intent opportunities, and takes a retry attempt at IMPLEMENT targeting those gaps first.
- If verdict is `pass` AND no opportunities are `below` intent, OR attempt budget is exhausted, log un-acted opportunities into CLAUDE.md's *Open opportunities* list, commit. A subsequent cold-survey iteration (no contributions) closes the constitution by flipping `status:` to `closed`, and REVIEW close-out runs in the user's main session.

The verdict is the iteration's judgment from the data; the **decision to keep iterating or close** happens by iteration boundary — one iteration writes the report and the take, the next surveys and decides whether to retry or accept. The opportunity assessment — graded against the user's fidelity intent — is the bridge that turns a binary verdict into a picture the next iteration (and REVIEW) can navigate.

## Survey signals (entry into COMPARE)

- All outputs in `lc status --universe baseline` are `ok` ⇒ ready to compare
- `comparison-report.yaml` exists with current `attempt` ⇒ COMPARE done for this attempt
- `comparison-report.yaml` verdict is `pass` (or `partial` with un-acted opportunities logged into CLAUDE.md's Open opportunities) ⇒ COMPARE → IMPLEMENT loop terminated; the next cold-survey iteration closes the constitution and REVIEW runs in the user's main session

## Notes

- **One COMPARE per IMPLEMENT.** Each IMPLEMENT retry produces a fresh COMPARE; the report's `attempt` field increments. Do not overwrite prior reports — keep them at `comparison-report-attempt-<N>.yaml` if useful, or commit each between attempts so `git log` carries the history.
- **The verdict is the iteration's judgment from the data; the keep-iterating decision happens at iteration boundary.** One iteration writes the report and the take on what should happen next; the next iteration surveys, reads the take, and either retries or accepts. The user's voice enters at REVIEW close-out, not mid-loop.
- **The opportunity assessment stays accessible past close-out.** Un-acted-on opportunities sit in CLAUDE.md's *Open opportunities* list — durable, auto-loaded on any future Claude Code session in this workdir. Tightening any becomes a future IMPLEMENT pass against a clearer target.
