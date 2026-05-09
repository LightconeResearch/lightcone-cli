# REVIEW — interactive close-out

The reproduction has converged (verdict `pass` or user-accepted `partial`). Control returns to the user. REVIEW is the second always-interactive bookend (INTERVIEW being the first); it runs in the main loop session, not as a sub-agent, so it can use `AskUserQuestion` and invoke sibling skills that need user reach. Its job is to render the validation surfaces, walk the user through the accumulated open questions, land the resolutions, draft the final report, and finalize the constitution outcome — in one interactive arc.

The phase name **REVIEW** is freed by the old pre-implement REVIEW phase folding into ARCHITECT, SPECIFY, and IMPLEMENT as their rigor-dialed self-review passes. This close-out is what the previous shape called SUMMARIZE_RUN.

The constitution's per-phase mode is **always interactive** for this phase. It does not run as a sub-agent. There is no "silent close-out" path; the close-out is the human's review.

## Inputs

- `astra.yaml` — final spec (validates with `--verify-evidence` once LITERATURE has resolved every `prior_insights:` placeholder's `evidence:` selector)
- `comparison-report.yaml`, `comparison-report.md` — final verdict
- `targets/targets.md` — what was being matched against; reference figures / tables in `targets/`
- `results/<universe>/<output_id>/` — reproduced figures / tables / metrics
- `<paper-slug>/open-questions.md` — running report from sub-agent phases (paper-vs-code conflicts, ambiguities, anything sub-agents flagged for user resolution)
- `work/notes/architect/paper-index.md` and `work/notes/architect/code-index.md` — for context
- The constitution at the project root — its `outcome:` field needs the final write
- `<paper-slug>/CLAUDE.md` — paper identity, code location

## Outputs

- `.lightcone/comparison.html` — `/figure-comparison`'s portable side-by-side report (paper artifacts vs reproduced)
- (Optional) `.lightcone/check-sentence-by-sentence.md` — `/check-sentence-by-sentence`'s claim audit (file:line or NOT FOUND per sentence)
- `<paper-slug>/open-questions.md` — same file, but with `## Resolutions` section appended capturing what the user said for each entry
- Edits to `astra.yaml` / `implementation-notes.md` / `universes/baseline.yaml` if any open-question resolution warrants a spec change
- `REPRODUCTION-SUMMARY.md` — final report; concise (~1–2 pages)
- Constitution `outcome:` rewritten to its final form
- A commit closing out the reproduction

## Step 1: render the validation surfaces

### `/figure-comparison` (mandatory)

Invoke the `/figure-comparison` skill from this session. It builds a portable HTML side-by-side comparing paper artifacts (from `targets/`) to reproduced artifacts (from `results/<universe>/`). The skill uses `AskUserQuestion` for any inputs it can't infer from the workdir; that works because REVIEW is interactive — the prompts land in this session.

Output lands at `.lightcone/comparison.html`. Show the user the path and offer to open it (`open` on macOS, `xdg-open` on Linux, or just print the path so they click in their terminal).

**Do not spawn `/figure-comparison` under the `Task` tool.** It has `AskUserQuestion` in its `allowed-tools`; a Task-tool sub-agent has no user-reach, so the prompt fires into nothing.

### `/check-sentence-by-sentence` (opt-in)

Ask the user via `AskUserQuestion` whether they want the claim audit. It's optional because for many reproductions the figure-comparison already settles "did it match?"; the sentence-by-sentence audit earns its keep when the paper makes many specific quantitative claims and the user wants each one anchored to a code location.

If yes, invoke `/check-sentence-by-sentence`. Same discipline as `/figure-comparison` — it can prompt the user; do not spawn under `Task`.

Output lands at `.lightcone/check-sentence-by-sentence.md` (or wherever the skill writes it). Show the user the path.

## Step 2: walk `<paper-slug>/open-questions.md` with the user

Read `<paper-slug>/open-questions.md`. For each unresolved entry, surface it via `AskUserQuestion` with:

- **The question** (verbatim from the file)
- **Origin** — which phase / sub-agent flagged it
- **The default the loop applied** (if any — e.g. "code as canonical")
- **Three options**: ratify the default, override (user spells out their choice), or defer (leave as a known limitation in the final report)

Append a `## Resolutions` section to `<paper-slug>/open-questions.md` capturing what the user said for each entry. This makes the resolution durable — re-runs and future sessions see it.

If a resolution warrants a spec change (the user picks an override), edit `astra.yaml` / `implementation-notes.md` / `universes/baseline.yaml` accordingly and re-run `astra validate astra.yaml`. If the change would invalidate the comparison report (e.g. flips the canonical method for a primary output), surface that to the user — in most cases the reproduction is "done" and the override is a known limitation, but the user may choose to re-enter the loop.

## Step 3: write `REPRODUCTION-SUMMARY.md`

A single markdown file at the project root, ~1–2 pages. Sections:

1. **What was reproduced** — the paper, the scope, the targets.
2. **Verdict** — pass / partial. If partial, what failed and why we accepted it.
3. **Material decisions** — the paper-vs-code conflicts SPECIFY's code pass surfaced, what the user chose (interactively or by canonical-resolution default), and why.
4. **Outputs** — pointers to the figures / tables / metrics produced. One bullet per primary target with the path to the reproduced result and a one-line match note from the comparison report.
5. **What was learned** — anything the reproduction surfaced that wasn't visible from the paper alone (a parameter the code uses but the paper doesn't mention, a data cut stricter than stated, etc.). This is where the reproduction's value to the broader literature gets recorded.
6. **Resolved open questions** — pull from `<paper-slug>/open-questions.md`'s `## Resolutions` section. One bullet per question + its resolution.
7. **Re-running** — one paragraph: how to re-run from this workdir (`lc run --universe baseline`, the constitution path, the relevant `astra.yaml`).

Brief, not exhaustive. The depth lives in `astra.yaml` and the workdir's notes; the summary is the door into them.

## Step 4: finalize the constitution outcome

Rewrite the constitution's `outcome:` field to its final form. Now the user has walked the validation surfaces, ratified the open questions, and accepted (or explicitly partially-accepted) the reproduction. Write the outcome that teaches:

> Reproduced <paper> against the targets in `targets/targets.md` with verdict `pass` (attempt 4). All 7 primary targets match within stated tolerance; 2 of 5 secondary targets show <5% offset attributable to <reason>. Material conflicts surfaced and resolved: <list>. Open questions resolved: <count> (full chain in `open-questions.md`). Spec at `astra.yaml` (validates with `--verify-evidence`); side-by-side at `.lightcone/comparison.html`; full report at `REPRODUCTION-SUMMARY.md`.

The outcome should stand on its own — someone reading just `felt show <reproduction-fiber>` (or the kanban card) should learn the verdict, the material decisions that landed, and where the artifacts live. No "see the body for details."

## Step 5: commit

Stage `REPRODUCTION-SUMMARY.md`, `<paper-slug>/open-questions.md` (with resolutions), the constitution with the final outcome, the final `astra.yaml`, the comparison artifacts, and any housekeeping changes. Commit with a message that names the verdict and the close-out:

```
review: <paper-short-name> verdict <verdict>, summary at REPRODUCTION-SUMMARY.md
```

After the commit, optionally flip the constitution's status to `closed` (or whatever the per-paper conventions name) so future surveys recognize the reproduction is done.

## Survey signals (entry into REVIEW)

- `comparison-report.yaml` verdict is `pass` (or user has accepted `partial`) ⇒ ready to close out
- `.lightcone/comparison.html` exists ⇒ `/figure-comparison` rendered
- `<paper-slug>/open-questions.md` has a `## Resolutions` section covering every entry ⇒ open-questions walkthrough done
- `REPRODUCTION-SUMMARY.md` exists ⇒ final report written
- Constitution `outcome:` reflects the final state ⇒ REVIEW done; reproduction complete

## Notes

- **This phase runs interactively in the main loop session.** Do not spawn it under `Task`. The whole point of REVIEW (close-out) is that the user is reachable — every step uses `AskUserQuestion` (directly, or via the sibling skills it invokes).
- **`/figure-comparison` and `/check-sentence-by-sentence` use `AskUserQuestion`.** That's why REVIEW is the always-interactive close-out and they live here, not in the loop. Spawning either under `Task` from inside the loop fires prompts into nothing.
- **The user owns the verdict-acceptance decision.** REVIEW's purpose is to let the user see what the loop did and decide whether they accept it. The skill renders surfaces and asks; it does not unilaterally close.
- **Don't confuse with the rigor-dialed self-reviews.** ARCHITECT, SPECIFY, and IMPLEMENT each run their own internal fresh-context self-review passes during the loop. Those are unrelated to this close-out — same word, different jobs. The phase boundary makes them unambiguous: rigor-dial reviews live inside their host phase's reference; this one is the always-interactive close-out.
- **Open-question resolutions are durable.** Append to `<paper-slug>/open-questions.md`'s `## Resolutions` section so the next re-run / future session sees what was decided. Do not delete the original questions.
- **Keep the report short.** Long reports get skimmed; short reports get read. Two pages is generous.
- **Do not invent further work.** If the constitution's evidence checks all pass, the reproduction is done. The next session, the human, or a future revisit can decide whether the reproduction's place still serves them.
