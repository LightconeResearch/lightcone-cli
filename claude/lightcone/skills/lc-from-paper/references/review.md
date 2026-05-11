# REVIEW — orchestrator-session close-out

The reproduction has converged (verdict `pass` or user-accepted `partial`). Control returns to the user. REVIEW is the second of two bookends that run in the orchestrator session itself, not as a named sub-agent (INTERVIEW being the first). It runs orchestrator-side because both `/figure-comparison` and `/check-sentence-by-sentence` use `AskUserQuestion`, which sub-agents don't have.

Its job is to render the validation surfaces, walk the user through the accumulated open questions, land the resolutions, draft the final report, and propagate any un-acted-on opportunities from the latest COMPARE into CLAUDE.md's **Rigor** section — in one interactive arc.

The phase name **REVIEW** is freed by the old pre-implement REVIEW phase folding into ARCHITECT, SPECIFY, LITERATURE, and IMPLEMENT as their per-spawn self-review passes. This close-out is what the previous shape called SUMMARIZE_RUN.

## Inputs

- `astra.yaml` — final spec (validates with `--verify-evidence` once LITERATURE has resolved every `prior_insights:` placeholder's `evidence:` selector)
- `comparison-report.yaml`, `comparison-report.md` — final verdict + opportunity assessment
- `targets/targets.md` — what was being matched against; reference figures / tables in `targets/`
- `results/<universe>/<output_id>/` — reproduced figures / tables / metrics
- `open-questions.md` at the workdir root — running report from sub-agent phases (paper-vs-code conflicts, ambiguities, anything sub-agents flagged for user resolution)
- `work/reference/index.json` and `work/reference/code-index.md` — for context
- **paper-expert** and **code-expert** — still reachable via `SendMessage` if the user asks a follow-up question during REVIEW that the report and CLAUDE.md don't answer. The experts persist for the lifetime of the reproduction; they're useful here for "remind me what the paper says about X" or "did the original code do Y" without leaving the orchestrator session.
- `CLAUDE.md` at the workdir root — paper identity, Goal, Rigor, Paper-vs-code disagreements (the at-a-glance summary that's accumulated across all sub-agent spawns)

## Outputs

- `.lightcone/comparison.html` — `/figure-comparison`'s portable side-by-side report (paper artifacts vs reproduced)
- (Optional) `.lightcone/check-sentence-by-sentence.md` — `/check-sentence-by-sentence`'s claim audit (file:line or NOT FOUND per sentence)
- `open-questions.md` — same file, but with `## Resolutions` section appended capturing what the user said for each entry
- Edits to `astra.yaml` / `implementation-notes.md` / `universes/baseline.yaml` if any open-question resolution warrants a spec change
- `REPRODUCTION-SUMMARY.md` — final report; concise (~1–2 pages); the canonical record of what the reproduction landed on
- CLAUDE.md updates — *Open opportunities* list under **Rigor** propagated from COMPARE's un-acted-on opportunities; **Paper-vs-code disagreements** entries reconciled with their resolutions
- A commit closing out the reproduction

## Step 1: render the validation surfaces

### `/figure-comparison` (mandatory)

Invoke the `/figure-comparison` skill from the orchestrator session. It builds a portable HTML side-by-side comparing paper artifacts (from `targets/`) to reproduced artifacts (from `results/<universe>/`). The skill uses `AskUserQuestion` for any inputs it can't infer from the workdir; that works because REVIEW runs orchestrator-side — the prompts land in this session, not in a sub-agent's chat.

Output lands at `.lightcone/comparison.html`. Show the user the path and offer to open it (`open` on macOS, `xdg-open` on Linux, or just print the path so they click in their terminal).

**Do not spawn `/figure-comparison` under the `Task` tool or as a named sub-agent.** It has `AskUserQuestion` in its `allowed-tools`; sub-agents have no user-reach, so the prompt fires into nothing.

### `/check-sentence-by-sentence` (opt-in)

Ask the user via `AskUserQuestion` whether they want the claim audit. It's optional because for many reproductions the figure-comparison already settles "did it match?"; the sentence-by-sentence audit earns its keep when the paper makes many specific quantitative claims and the user wants each one anchored to a code location.

If yes, invoke `/check-sentence-by-sentence`. Same discipline as `/figure-comparison` — it can prompt the user; do not spawn under `Task` or as a named sub-agent.

Output lands at `.lightcone/check-sentence-by-sentence.md` (or wherever the skill writes it). Show the user the path.

## Step 2: walk `open-questions.md` with the user

Read `open-questions.md` at the workdir root. For each unresolved entry, surface it via `AskUserQuestion` with:

- **The question** (verbatim from the file)
- **Origin** — which sub-agent flagged it
- **The default the sub-agent applied** (if any — e.g. "code as canonical")
- **Three options**: ratify the default, override (user spells out their choice), or defer (leave as a known limitation in the final report)

Append a `## Resolutions` section to `open-questions.md` capturing what the user said for each entry. This makes the resolution durable — re-runs and future sessions see it. Cross-reference with CLAUDE.md's **Paper-vs-code disagreements** section: every entry there should now have its resolution recorded, either inline (if the user picked the canonical default) or in `open-questions.md`.

If a resolution warrants a spec change (the user picks an override), edit `astra.yaml` / `implementation-notes.md` / `universes/baseline.yaml` accordingly and re-run `astra validate astra.yaml`. If the change would invalidate the comparison report (e.g. flips the canonical method for a primary output), surface that to the user — in most cases the reproduction is "done" and the override is a known limitation, but the user may choose to re-spawn IMPLEMENT.

## Step 3: write `REPRODUCTION-SUMMARY.md`

A single markdown file at the project root, ~1–2 pages. The canonical record of what this reproduction landed on. Sections:

1. **What was reproduced** — the paper, the scope, the targets.
2. **Verdict** — pass / partial. If partial, what failed and why we accepted it.
3. **Material decisions** — the paper-vs-code conflicts SPECIFY's code pass (and any IMPLEMENT pass) surfaced, what the user chose (in prose ratification or by canonical-resolution default), and why.
4. **Outputs** — pointers to the figures / tables / metrics produced. One bullet per primary target with the path to the reproduced result and a one-line match note from the comparison report.
5. **Open opportunities** — pull from `comparison-report.yaml`'s `opportunities:` block, plus anything in CLAUDE.md's **Rigor** section's *Open opportunities* list. One bullet each with the leverage assessment. This is what a future session (or a future-Cail revisiting) would tighten next.
6. **What was learned** — anything the reproduction surfaced that wasn't visible from the paper alone (a parameter the code uses but the paper doesn't mention, a data cut stricter than stated, etc.). The reproduction's value to the broader literature.
7. **Resolved open questions** — pull from `open-questions.md`'s `## Resolutions` section. One bullet per question + its resolution.
8. **Re-running** — one paragraph: how to re-run from this workdir (`lc run --universe baseline`, the relevant `astra.yaml`, where CLAUDE.md lives so future sub-agents auto-load it).

Brief, not exhaustive. The depth lives in `astra.yaml` and the workdir's notes; the summary is the door into them.

## Step 4: propagate opportunities into CLAUDE.md

For each opportunity in `comparison-report.yaml`'s `opportunities:` block that the user did NOT act on (i.e. they accepted the current verdict and chose to land here), append it to CLAUDE.md's **Rigor** section's *Open opportunities* list. Format: `<area> — <what could be tightened> — <leverage>`. This is what future sessions and future re-spawns walk up to; it's how the reproduction stays honest about what's at sketch / baseline / tightened / canonical rigor across its outputs.

If the user acted on an opportunity (e.g. authorized one more IMPLEMENT round to close a gap), it doesn't go in the open list — but its closure is worth noting in *Current state* (e.g. *Figure 3: tightened* if the systematics treatment got a heavier pass).

## Step 5: commit

Stage `REPRODUCTION-SUMMARY.md`, `open-questions.md` (with resolutions), the updated CLAUDE.md, the final `astra.yaml`, the comparison artifacts, and any housekeeping changes. Commit with a message that names the verdict and the close-out:

```
review: <paper-short-name> verdict <verdict>, summary at REPRODUCTION-SUMMARY.md
```

This commit is the durable mark that the reproduction has reached close-out. Future walk-ups read CLAUDE.md and `git log` to know where the reproduction stands; the close-out commit + REPRODUCTION-SUMMARY.md together stand in for the old constitution `outcome:` field.

## Survey signals (entry into REVIEW)

- `comparison-report.yaml` verdict is `pass` (or user has accepted `partial`) ⇒ ready to close out
- `.lightcone/comparison.html` exists ⇒ `/figure-comparison` rendered
- `open-questions.md` has a `## Resolutions` section covering every entry ⇒ open-questions walkthrough done
- `REPRODUCTION-SUMMARY.md` exists ⇒ final report written
- CLAUDE.md's **Rigor** section's *Open opportunities* list reflects the un-acted-on opportunities from the latest COMPARE ⇒ propagation done
- A `review:` commit lands ⇒ REVIEW done; reproduction complete

## Notes

- **This phase runs in the orchestrator session.** Do not spawn it as a named sub-agent. The whole point of REVIEW is that the user is reachable — every step uses `AskUserQuestion` (directly, or via the sibling skills it invokes).
- **`/figure-comparison` and `/check-sentence-by-sentence` use `AskUserQuestion`.** That's why REVIEW runs orchestrator-side and they live here, not in any sub-agent. Spawning either as a sub-agent fires prompts into nothing.
- **The user owns the verdict-acceptance decision.** REVIEW's purpose is to let the user see what the sub-agents did and decide whether they accept it. The skill renders surfaces and asks; it does not unilaterally close.
- **Don't confuse with the per-spawn self-reviews.** ARCHITECT, SPECIFY, LITERATURE, and IMPLEMENT each run their own internal fresh-context self-review passes during their work. Those are unrelated to this close-out — same word, different jobs. The phase boundary makes them unambiguous: per-spawn self-reviews live inside their host phase's reference; this one is the orchestrator-session close-out.
- **Open-question resolutions are durable.** Append to `open-questions.md`'s `## Resolutions` section so the next re-run / future session sees what was decided. Do not delete the original questions.
- **Keep the report short.** Long reports get skimmed; short reports get read. Two pages is generous.
- **Do not invent further work.** If the user has accepted the verdict and the opportunities are propagated, the reproduction is done. The next session, the user, or a future revisit can decide whether tightening any open opportunity still serves them.
