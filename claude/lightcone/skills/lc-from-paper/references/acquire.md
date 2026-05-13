# ACQUIRE — stand up the on-disk substrate

The pre-loop substrate phase. Runs in the user's main session, right after INTERVIEW has committed `constitution.md` and `CLAUDE.md`. Two parallel sub-skill invocations produce the on-disk material every subsequent ralph iteration consults: `/paper-extraction` for the paper side, `/lc-from-code` in scan-only mode for the code side. Both write to `work/reference/`; both are survey-first and skip already-done work, so re-invoking on a partial state is safe.

There is no `acquire` sub-agent. ACQUIRE's work *is* the two sub-skill invocations. Once they return, commit the substrate and launch the ralph loop (per SKILL.md's *Launching the loop* section).

## Where this runs

User's main session, directly. Sub-skills are invoked as `/paper-extraction <id>` and `/lc-from-code` against the cloned reference repo.

## Inputs

- The paper's DOI or arXiv ID (from `constitution.md`'s Evidence section)
- An optional code repo URL (from the interview, if the user knew it; recorded in `constitution.md`'s Evidence section)

## Outputs

All on-disk; no persistent agents:

- `work/reference/paper.pdf`
- `work/reference/source/` (Path A — arxiv LaTeX) **or** `work/reference/document.md` (Path B — Docling fallback)
- `work/reference/index.json` — paper-side structural index (figures, tables, outline with line numbers, citations with resolved DOIs)
- `work/reference/astra.yaml` — paper-extraction's ASTRA-shape stub of the paper (id, name, narrative.summary, optionally findings)
- `work/reference/figures/`, `work/reference/tables/`, `work/reference/bibliography-source.{bib,bbl}`
- `work/reference/code/` — cloned reference repo (absent if not found)
- `work/reference/code-status.yaml` — record of where the code came from
- `work/reference/code-index.md` — script inventory, candidate decisions, dependencies, container hints

## Step 1 — Invoke `/paper-extraction`

```
/paper-extraction <doi-or-arxiv-id>
```

This runs the full paper-extraction workflow against the workdir. It writes everything under `work/reference/` listed above. The skill is idempotent; re-invoking on a partially-populated `work/reference/` is safe.

## Step 2 — Locate, clone, and scan the reference code (parallel with Step 1)

In a separate flow inside the same session:

1. **Locate the reference code repository.**
   - If a URL was provided at INTERVIEW (in `constitution.md`'s Evidence section), use it.
   - Otherwise, grep the paper materials in `work/reference/` for repo URLs (abstract, intro, conclusion, footnotes, "Code Availability" / "Data Availability" sections). Path A: grep across `work/reference/source/*.tex`. Path B: grep `work/reference/document.md`. If `/paper-extraction` hasn't finished yet when you need to grep, wait briefly or skip ahead and come back.
   - If still nothing, web-search: paper title + "github", Papers With Code, or the first author's GitHub profile. A few searches max — record failure and move on.

2. **Clone if found:**
   ```bash
   git clone --depth 1 <url> work/reference/code
   ```

3. **Write `work/reference/code-status.yaml`:**
   ```yaml
   found: true        # or false
   url: "https://..."  # null if not found
   cloned: true       # false if found but clone failed
   notes: "..."
   ```

4. **If `work/reference/code/` exists, run `/lc-from-code` in scan-only mode against it:**
   - Invoke `/lc-from-code` pointing at the cloned repo.
   - The scan-only branch of `/lc-from-code` does the inventory pass inline (no Explore sub-agent spawn); it writes to `work/reference/code-index.md`.
   - Do not touch `astra.yaml` at the project root, do not parameterize any code, do not run anything, do not modify the cloned repo.

`/lc-from-code`'s scan-only branch is the canonical code-inventory mechanism. Its prompt-context surface is what carries the "stop at scan" contract.

**A scan-only return is not an ACQUIRE stopping point.** ACQUIRE is incomplete until Step 3 below has either succeeded or hit a concrete launcher blocker. When `/lc-from-code` returns, do not summarize the scan as the final user-facing result. Continue immediately to Step 3: commit the substrate, launch the ralph loop, and tell the user the session name.

## Step 3 — Commit and launch the ralph loop

When both Step 1 and Step 2 have landed:

1. **Commit the substrate.** Stage `work/reference/` and commit — small, descriptive ("acquire: paper-extraction substrate"). For the code side: commit `code-status.yaml` + `code-index.md`. The `work/reference/code/` clone itself can be `.gitignore`d or committed depending on the project's preference; the inventory file (`code-index.md`) is what downstream iterations actually consult.

2. **Tell the user** the ralph loop is about to launch. Surface anything notable from Step 2 — if `code-status.yaml` records `found: false` or the cloned repo is gnarly, mention it now so the user can adjust scope before iterations start working against the substrate.

3. **Launch the loop** (per SKILL.md's *Launching the loop* section):
   ```bash
   .claude/skills/ralph/scripts/ralph constitution.md
   ```
   Tell the user the tmux session name and the attach command. Iterations start firing immediately.

## Survey signals (entry into ACQUIRE)

Run `ls work/reference/` first.

- `paper.pdf` + path indicator (`source/` for Path A, `document.md` for Path B) + `index.json` + paper-side `astra.yaml` present → `/paper-extraction` has done its work (or is mid-run; re-invoking is idempotent and will skip done work).
- `work/reference/code/` present, **or** `code-status.yaml` records `found: false`, **and** `code-index.md` is present → code-side work is done.
- When both sides are present and committed → ACQUIRE is complete; commit any unstaged changes and launch the loop.
- Otherwise, re-invoke whichever side is missing. Both skills are survey-first and skip already-done work.

## Notes

- **paper-extraction is the substrate authority.** Don't re-fetch the LaTeX source, don't re-run Docling, don't re-parse the paper from inside ACQUIRE. If a substrate need surfaces — including mid-reproduction, raised by an iteration — fix it in `/paper-extraction`, not here. Bibliography resolution is paper-extraction's: cited-paper text and DOIs live inside `index.json#citations[key]`, not in a side file.
- **lc-from-code is the code-inventory authority** for the scan portion. ACQUIRE's invocation constrains it to scan-only via the prompt; the parameterization and run portions of `/lc-from-code` are not invoked at this phase.
- **arXiv DOI form is `10.48550/arXiv.<id>`.** Useful when downstream tools want a DOI rather than an arXiv ID.
- **Equation numbers and section numbers must match the rendered paper.** When citing "eq. N" or "§N" downstream, find by content, not by a naïve count of TeX blocks or markdown headings. Path A: source preserves printed numbers in `\label{}`s. Path B: Docling preserves printed numbers.
- **This phase is acquisition, not understanding.** ACQUIRE doesn't write `astra.yaml` at the project root and doesn't compare paper to code. ARCHITECT does that, in the first ralph iteration after the loop launches.
- **Code-as-canonical** is loaded by every iteration via `CLAUDE.md`'s Rules. ACQUIRE just stands up the reference so the rule has something to point at.
- **The cloned code is read-only reference.** Iterations may re-read it; nothing modifies `work/reference/code/`. (When the reproduction's implementation needs to happen, that's an IMPLEMENT-phase decision, not an ACQUIRE one.)
- **Surface anti-patterns from the scan.** If `code-status.yaml` reports the clone failed or the repo is clearly dead, or if `/paper-extraction` reports the paper substrate is broken, surface to the user immediately rather than launching a loop against half-acquired substrate.
