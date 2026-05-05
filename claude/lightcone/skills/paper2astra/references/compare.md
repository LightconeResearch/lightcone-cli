# COMPARE — judge whether the reproduction matches

Compare reproduced results against the paper's replication targets. Produce a structured verdict the IMPLEMENT-retry loop consumes. COMPARE is the **second mandatory user-ratification seam** — the verdict (was it close enough?) is a judgment the user owns, not the agent.

The constitution's per-phase mode is **always interactive** for this phase. Pause for verdict ratification.

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
```

## Verdict rules

- **`pass`**: ALL high-priority targets match, no major issues with medium-priority.
- **`partial`**: some high-priority match, or all high-priority match but medium has issues.
- **`fail`**: most high-priority don't match, or fundamental methodological issue.

If verdict is not `pass`, **`fix_suggestions` MUST reference specific scripts and line numbers**. "The result is wrong" is not actionable; "scripts/bao_fit.py:42 uses `damping_prior=flat`, paper specifies Gaussian; change to gaussian per Howlett+2017 §4.2" is.

Also write `comparison-report.md` with a human-readable summary. For figure / table comparisons, describe what you see in both and explain your match judgment.

## Verdict ratification (the user seam)

After writing the report, surface the verdict to the user via `AskUserQuestion`:

- **If `pass`**: confirm with the user before exiting the COMPARE → IMPLEMENT loop. *"All high-priority targets match. Mark reproduction complete?"* The user accepts → SUMMARIZE_RUN runs; the user rejects → name what's still off and re-enter the loop.
- **If `partial`**: show the user the failing targets and the diagnosis. *"Partial match. <N> outputs failing: <list>. Continue retrying or accept partial?"* If the attempt budget (from the constitution) is reached, this surfacing is mandatory.
- **If `fail`**: same shape, but the loop's continuation should be questioned more sharply. A fundamental methodological issue may need a constitution amendment, not another implement retry.

The verdict is the agent's judgment; the **decision to keep iterating** is the user's. Default on user silence: continue the loop until the attempt budget is exhausted, then mandatory user surfacing.

## Survey signals (entry into COMPARE)

- All outputs in `lc status --universe baseline` are `ok` ⇒ ready to compare
- `comparison-report.yaml` exists with current `attempt` ⇒ COMPARE done for this attempt
- `comparison-report.yaml` verdict is `pass` ⇒ COMPARE → IMPLEMENT loop terminated; proceed to SUMMARIZE_RUN

## Notes

- **One COMPARE per IMPLEMENT.** Each IMPLEMENT retry produces a fresh COMPARE; the report's `attempt` field increments. Do not overwrite prior reports — keep them at `comparison-report-attempt-<N>.yaml` if useful, or commit each between iterations so git carries the history.
- **The verdict is the agent's; the keep-iterating decision is the user's.** Treat them as separate.
