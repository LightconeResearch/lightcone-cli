# SUMMARIZE_RUN — final report and outcome draft

The reproduction has converged (verdict `pass` or user-accepted `partial`). Write the final summary to disk, draft the constitution's outcome, and prepare the workdir for the post-loop interactive return. SUMMARIZE_RUN runs as a silent sub-agent — it produces the report cleanly and exits; the next phase, FINAL_REVIEW, picks up interactively to drive `/figure-comparison`, optionally `/check-sentence-by-sentence`, walk the user through `open-questions.md`, and finalize the outcome before closure.

The constitution's per-phase mode is **always sub-agent**. There are no decisions left for this phase; this is reportage that hands off to FINAL_REVIEW.

## Inputs

- `astra.yaml` — final spec
- `comparison-report.yaml`, `comparison-report.md` — final verdict
- `targets/targets.md` — what was being matched against
- `work/notes/methodology.md` — for context
- The constitution at the project root — its `outcome:` field needs rewriting

## Outputs

- `REPRODUCTION-SUMMARY.md` (or whatever name fits the project) — final report; concise.
- Draft `outcome:` on the constitution. FINAL_REVIEW refines it after the user has walked the validation surfaces.
- A commit on the reproduction branch with a clear message.

## What the final report covers

A single markdown file at the project root, ~1–2 pages. Sections:

1. **What was reproduced** — the paper, the scope, the targets.
2. **Verdict** — pass / partial. If partial, what failed and why we accepted it.
3. **Material decisions** — the paper-vs-code conflicts the SPECIFY phase surfaced, what the user chose, and why.
4. **Outputs** — pointers to the figures / tables / metrics produced. One bullet per primary target, with the path to the reproduced result.
5. **What was learned** — anything the reproduction surfaced that wasn't visible from the paper alone (a parameter the code uses but the paper doesn't mention, a data cut that's stricter than stated, etc.). This is where the reproduction's value to the broader literature gets recorded.
6. **Re-running** — one paragraph: how to re-run from this workdir (`lc run --universe baseline`, the constitution path, the relevant `astra.yaml`).
7. **Open questions for FINAL_REVIEW** — short pointer to `<paper-slug>/open-questions.md`, with a count of unresolved entries. FINAL_REVIEW will walk these with the user; this section just flags that they're waiting.

Brief, not exhaustive. The depth lives in `astra.yaml` and the workdir's notes; the summary is the door into them.

## Constitution outcome (draft)

Draft the constitution's `outcome:` field to reflect the realized state. A good outcome teaches:

> Reproduced <paper> against the targets in `targets/targets.md` with verdict `pass` (attempt 4). All 7 primary targets match within stated tolerance; 2 of 5 secondary targets show <5% offset attributable to <reason>. Material conflicts surfaced and resolved: <list>. Spec at `astra.yaml` (validates with `--verify-evidence`); reproduction summary at `REPRODUCTION-SUMMARY.md`. **FINAL_REVIEW pending: <N> open questions, `/figure-comparison` not yet rendered.**

This is a draft. **FINAL_REVIEW refines it** after the user has walked the validation surfaces and ratified the open questions. The constitution's `status:` flips to `closed` only when the user accepts FINAL_REVIEW's surfacing. This sub-agent does not flip status, and does not finalize the outcome — it prepares the report and the outcome draft, then exits so FINAL_REVIEW can take over interactively.

## Commit

Stage the report, the constitution outcome draft, the final `astra.yaml`, the comparison report, and any housekeeping changes. Commit with a message that names the verdict and signals the handoff:

```
summarize_run: <paper-short-name> verdict <verdict>, summary at REPRODUCTION-SUMMARY.md, final_review pending
```

## Survey signals (entry into SUMMARIZE_RUN)

- `comparison-report.yaml` verdict is `pass` (or user has accepted `partial`) ⇒ ready
- `REPRODUCTION-SUMMARY.md` exists, constitution outcome draft is in place ⇒ SUMMARIZE_RUN done; FINAL_REVIEW takes over interactively

## Notes

- **This phase does not flip the constitution's status to closed.** The user does that, after FINAL_REVIEW. SUMMARIZE_RUN's job is to produce the summary cleanly and hand off.
- **Do not invoke `/figure-comparison` or `/check-sentence-by-sentence` from here.** Both have `AskUserQuestion` in their `allowed-tools`; spawning them under the `Task` tool fires prompts into nothing. They run in FINAL_REVIEW, where the user is reachable.
- **Keep the report short.** Long reports get skimmed; short reports get read. Two pages is generous.
