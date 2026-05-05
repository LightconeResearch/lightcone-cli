# SUMMARIZE_RUN — final report, figure-comparison HTML, and constitution outcome

The reproduction has converged (verdict `pass` or user-accepted `partial`). Write the final summary, auto-render the figure-comparison HTML so the user sees side-by-sides on arrival, update the constitution's outcome, and prepare the workdir for handoff.

The constitution's per-phase mode is **always sub-agent**. There are no decisions left; this is reportage.

## Inputs

- `astra.yaml` — final spec
- `comparison-report.yaml`, `comparison-report.md` — final verdict
- `targets/targets.md` — what was being matched against
- `work/notes/methodology.md` — for context
- The constitution at the project root — its `outcome:` field needs rewriting

## Outputs

- `REPRODUCTION-SUMMARY.md` (or whatever name fits the project) — final report; concise.
- `figure-comparison.html` (or whatever name `/figure-comparison` produces) — auto-rendered side-by-side: original vs reproduced figures, tables, numerics. Spawned as a sub-agent so SUMMARIZE_RUN itself stays small.
- Updated `outcome:` on the constitution.
- A final commit on the reproduction branch with a clear message.

## Sub-agent invocations

This phase orchestrates two sub-agents — both auto-invoked via the `Task` tool, both fresh-context:

1. **`/figure-comparison`** — produces the HTML side-by-side. Always run; the user expects it on arrival. Read its SKILL.md (Nolan's skill) for what to pass it; at minimum, the path to the reproduction workdir so it can find originals (`work/reference/figures/`, `work/reference/tables/`) and reproduced outputs (`results/<universe>/`).

2. **`/check-sentence-by-sentence`** is **opt-in** — never auto-invoked here. After the report is written, the iteration's exit message surfaces it as a suggestion to the user: *"Want a paper-vs-code TeX audit? `/check-sentence-by-sentence` will fan out a sub-agent per claim and locate `file:line` or `NOT FOUND`. Token-expensive (~N sub-agents)."* The user decides whether to spend the budget.

## What the final report covers

A single markdown file at the project root, ~1–2 pages. Sections:

1. **What was reproduced** — the paper, the scope, the targets.
2. **Verdict** — pass / partial. If partial, what failed and why we accepted it.
3. **Material decisions** — the paper-vs-code conflicts the SPECIFY phase surfaced, what the user chose, and why.
4. **Outputs** — pointers to the figures / tables / metrics produced. One bullet per primary target, with the path to the reproduced result.
5. **What was learned** — anything the reproduction surfaced that wasn't visible from the paper alone (a parameter the code uses but the paper doesn't mention, a data cut that's stricter than stated, etc.). This is where the reproduction's value to the broader literature gets recorded.
6. **Re-running** — one paragraph: how to re-run from this workdir (`lc run --universe baseline`, the constitution path, the relevant `astra.yaml`).

Brief, not exhaustive. The depth lives in `astra.yaml` and the workdir's notes; the summary is the door into them.

## Constitution outcome

Rewrite the constitution's `outcome:` field to reflect the realized state. A good outcome teaches:

> Reproduced <paper> against the targets in `targets/targets.md` with verdict `pass` (attempt 4). All 7 primary targets match within stated tolerance; 2 of 5 secondary targets show <5% offset attributable to <reason>. Material conflicts surfaced and resolved: <list>. Spec at `astra.yaml` (validates with `--verify-evidence`); reproduction summary at `REPRODUCTION-SUMMARY.md`.

The constitution's `status:` flips to `closed` only when the user accepts. This sub-agent does not flip status — it prepares the outcome and surfaces to the user (via the iteration's exit message) that the constitution is ready for closure.

## Final commit

Stage the report, the updated constitution, the final `astra.yaml`, the comparison report, and any housekeeping changes. Commit with a message that names the verdict:

```
reproduction: <paper-short-name> verdict <verdict>, summary at REPRODUCTION-SUMMARY.md
```

## Survey signals (entry into SUMMARIZE_RUN)

- `comparison-report.yaml` verdict is `pass` (or user has accepted `partial`) ⇒ ready
- `REPRODUCTION-SUMMARY.md` exists; `figure-comparison.html` exists; constitution outcome is rewritten ⇒ done

## Notes

- **This phase does not flip the constitution's status to closed.** The user does that, after reviewing the summary. The phase's job is to produce the summary cleanly; the human keeps the close authority.
- **Keep the report short.** Long reports get skimmed; short reports get read. Two pages is generous.
