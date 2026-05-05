# FINAL_REVIEW — interactive post-loop ratification

The COMPARE → IMPLEMENT loop has terminated (verdict `pass` or attempt budget exhausted). SUMMARIZE_RUN has written `REPRODUCTION-SUMMARY.md`. Control now returns to the user; FINAL_REVIEW runs **inside the main loop session, not as a sub-agent**, so `AskUserQuestion` actually reaches a human. This is the post-loop ratification seam — validation surfaces (`/figure-comparison`, optional `/check-sentence-by-sentence`) plus the accumulated `open-questions.md` get walked through and resolved before the constitution is closed.

The constitution's per-phase mode is **always interactive** for this phase. The user must be reachable.

## Inputs

- `<paper-slug>/REPRODUCTION-SUMMARY.md` — final report from SUMMARIZE_RUN
- `comparison-report.{yaml,md}` — final verdict
- `<paper-slug>/open-questions.md` — accumulated questions from sub-agent / loop phases
- `<paper-slug>/<constitution>.md` — its `outcome:` field needs a final rewrite
- `astra.yaml` — may need targeted edits as questions resolve
- `implementation-notes.md` — may absorb resolutions that don't belong in `astra.yaml`

## Outputs

- `.lightcone/comparison.html` — self-contained side-by-side report (from `/figure-comparison`)
- `<paper-slug>/sentence-audit.md` (or wherever `/check-sentence-by-sentence` lands its report) — *optional*
- `<paper-slug>/open-questions.md` with every entry marked resolved (or explicitly deferred with a reason)
- `astra.yaml` and/or `implementation-notes.md` updated where resolutions changed a decision or added a gotcha
- Updated `outcome:` on the constitution
- A final commit naming the FINAL_REVIEW pass

## Task

1. **Open the report.** Read `REPRODUCTION-SUMMARY.md`. Skim `comparison-report.md`. The agent's job in this phase is to surface the right things to the user — not to re-derive what SUMMARIZE_RUN already concluded.

2. **Invoke `/figure-comparison`.** This is the rich validation surface — base64-embedded HTML side-by-sides for every paper artifact versus its reproduced counterpart. The skill prompts the user for any missing inputs (universe choice, paper-reference path) via its own `AskUserQuestion`. Land the HTML at `.lightcone/comparison.html` and surface the path to the user.

3. **Offer `/check-sentence-by-sentence`.** Ask the user via `AskUserQuestion`:

   > *"Run sentence-by-sentence audit of the paper against the code? (Slow but catches claims that drifted between paper and reproduction.)"*

   On yes: invoke `/check-sentence-by-sentence`. The skill prompts for paper-source path (arXiv TeX preferred, Docling markdown fallback). It produces a per-sentence `file:line` or `NOT FOUND` audit. Surface the audit path and any `NOT FOUND` clusters that suggest missing implementation.

4. **Walk `open-questions.md` with the user.** For every unresolved entry, surface via `AskUserQuestion`:

   > *"Open question: <question text>. The loop's best-judgment default was <default>. Accept, override, or defer?"*

   - **Accept**: mark resolved with the default; record the resolution in the entry.
   - **Override**: take the user's choice; update `astra.yaml` (decision options, baseline universe) or `implementation-notes.md` accordingly. Re-run `astra validate` if the spec changed.
   - **Defer**: leave the entry but mark it `deferred: <reason>` so it's clearly not forgotten.

   Some questions surface a real gap (a target wasn't reproduced, a method differs in a way that matters). When the gap is material, ask whether to re-enter the loop for another COMPARE → IMPLEMENT pass. The user owns that call.

5. **Rewrite the constitution `outcome:`.** The SUMMARIZE_RUN sub-agent prepared a draft outcome; refine it with what FINAL_REVIEW surfaced — accepted partials, deferred questions, the `/figure-comparison` HTML path, the audit path if run. The outcome should teach: someone reading it should understand what the reproduction landed and where the rough edges are without opening the body.

6. **Final commit.** Stage `.lightcone/comparison.html`, the audit (if run), the resolved `open-questions.md`, any `astra.yaml` / `implementation-notes.md` edits, and the constitution outcome:

   ```
   final_review: <paper-short-name> — N questions resolved, comparison.html rendered[, sentence audit completed]
   ```

7. **Surface closure to the user.** The constitution is now in shape for `status: closed`. Do not flip it from this phase — surface that it's ready, the user closes.

## Survey signals (entry into FINAL_REVIEW)

- `comparison-report.yaml` verdict is `pass` (or user-accepted `partial`) **and** `REPRODUCTION-SUMMARY.md` exists ⇒ ready to enter FINAL_REVIEW
- `.lightcone/comparison.html` exists, `open-questions.md` entries are all resolved or explicitly deferred, constitution `outcome:` reflects the post-review state ⇒ FINAL_REVIEW done

## Notes

- **This phase is not a sub-agent.** `/figure-comparison` and `/check-sentence-by-sentence` both have `AskUserQuestion` in their `allowed-tools`; spawning them under the `Task` tool would fire prompts into nothing. FINAL_REVIEW runs in the main loop session so the prompts land. The constitution lists FINAL_REVIEW as `interactive` for the same reason.
- **Don't relitigate SUMMARIZE_RUN.** The final report is the user's reading surface for "what landed." FINAL_REVIEW's job is the rich validation pass and the open-question ratification — not regenerating prose the sub-agent already produced cleanly.
- **`/figure-comparison` is mandatory; `/check-sentence-by-sentence` is opt-in.** The HTML side-by-side is cheap and high-signal; the sentence audit is slower and pays off most when the user has fidelity concerns. Default opt-in question: no.
- **The user holds closure.** This phase prepares the outcome and surfaces "ready"; flipping `status: closed` is the user's call after they're satisfied with what FINAL_REVIEW surfaced.
