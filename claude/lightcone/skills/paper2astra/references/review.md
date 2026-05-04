# REVIEW — pre-implementation sanity check

Verify that the ASTRA specification is complete, consistent, and ready for the IMPLEMENT phase. REVIEW edits the spec in place when fixes are obvious; it surfaces gaps to the user (or as Open Questions) when judgment is required.

The constitution's per-phase mode is **user choice** for this phase — defaults to sub-agent. REVIEW is mostly mechanical (cross-reference, validation), so sub-agent suits it; but a paper that hits the SPECIFY conflict-surfacing path heavily may want REVIEW interactive too.

## Inputs

- `astra.yaml` — the spec from SPECIFY
- `universes/baseline.yaml`
- `implementation-notes.md`
- `work/notes/methodology.md`
- `targets/targets.md`
- `work/reference/document.md` (Grep into; do not re-read whole)
- `work/notes/literature.yaml` (if present) — for evidence verification

## Outputs

- In-place edits to `astra.yaml`, `universes/baseline.yaml`, `implementation-notes.md` as needed
- No new files unless a missing data-acquisition path needs to be flagged with content

## Checks

1. **Target coverage.** Every replication target from `targets/targets.md` must appear as an output (or finding, or input/decision/universe default) in `astra.yaml`. Any missing target either gets added or earns an explicit out-of-scope reason in `targets.md`.

2. **Output definitions.** Each output has a clear `type` and sufficient description.

3. **Methodology detail.** Cross-check `work/notes/methodology.md` against the spec for gaps: missing hyperparameters, underspecified algorithms, vague data-processing steps. Re-read targeted sections of the paper to fill them in. Use Grep on `work/reference/document.md` rather than re-reading the whole thing.

4. **Decisions.** Decisions should cover what actually affects reproducibility. Remove cosmetic choices; add anything material that is missing. Ensure `universes/baseline.yaml` stays consistent.

5. **Data obtainability.** Every data source needs a concrete path (URL, package name, or generation code). Flag anything vague or "available upon request."

6. **Data acquisition.** Every input in `astra.yaml` must have a concrete acquisition path — a download URL, database query, API call, or package name. Verify that `methodology.md` documents how to obtain each dataset. Flag any dataset that is vague so IMPLEMENT knows what to handle.

7. **Implementation notes.** Check `implementation-notes.md` for completeness — does it flag the tricky parts? Add anything IMPLEMENT should know.

8. **Evidence verification.** If `work/notes/literature.yaml` exists, run:
   ```bash
   astra validate astra.yaml --verify-evidence
   ```
   This verifies that all prior-insight quotes match the source PDFs. Flag any misquotes or unsupported claims; these typically arise when a quote was paraphrased or when prefix/suffix carry editorial commentary instead of real surrounding text.

## Fixes

Edit files directly. After any change to `astra.yaml`, run:

```bash
astra validate astra.yaml
```

## CRITICAL: No synthetic data

Unless the paper itself uses synthetic / simulated data as input, the pipeline must use **real data only**. Check that:

- Every `astra.yaml` input has a real acquisition source (URL, query, etc.)
- `implementation-notes.md` does NOT suggest generating mock / synthetic data
- The methodology notes describe real data sources with concrete download paths

If any input lacks a concrete acquisition path, add one by searching the paper for URLs, DOIs, or archive references. If the data truly cannot be obtained programmatically, document this clearly in `implementation-notes.md` so IMPLEMENT writes a script that fails with a helpful message rather than silently substituting fake data.

## Rules

- Use Grep to search `work/reference/document.md` for specific claims to verify — do not read the entire markdown at once. Work primarily from notes and the spec.
- **Minimize churn** — don't restructure or rename unnecessarily.
- If everything looks good, say so briefly; don't invent problems.
- Do **NOT** add implementation recipes — that is IMPLEMENT's job.

## Survey signals (entry into REVIEW)

- `astra.yaml` exists and validates ⇒ ready to review
- `astra validate astra.yaml --verify-evidence` returns clean (when literature.yaml exists) ⇒ evidence side done
- All `targets/targets.md` entries map to spec homes (output / finding / input / decision / universe default) ⇒ coverage side done
- Both ⇒ REVIEW complete; proceed to IMPLEMENT

## Notes

- **REVIEW does not write code.** Its outputs are edits to the spec and additions to `implementation-notes.md`, not new scripts.
- **A clean REVIEW reduces IMPLEMENT thrash.** It is worth running even when the spec looks fine after SPECIFY — the cross-check catches "looks fine in isolation, breaks under full coverage" gaps.
