# ACQUIRE — stand up the code substrate

The post-INTERVIEW substrate phase. Runs in the user's main session, right after INTERVIEW has committed `constitution.md`, `CLAUDE.md`, and the paper substrate produced by `/paper-extraction`. ACQUIRE is now thin: it stands up the **code substrate** if there's a reference code repository, then commits. The paper substrate is INTERVIEW's deliverable, not ACQUIRE's — INTERVIEW reads the paper to ground its grounded-beat questions, so the substrate already exists on disk when ACQUIRE starts.

There is no `acquire` sub-agent. ACQUIRE's work is at most one skill invocation (`/lc-from-code` in scan-only mode) plus the surrounding clone, status file, and commit.

If the paper has no reference code repo (and the user didn't supply a private one in INTERVIEW), ACQUIRE is one step: write `code-status.yaml` with `found: false` and proceed to launch the loop.

## Where this runs

User's main session, directly. The one sub-skill invocation is `/lc-from-code` in scan-only mode against the cloned reference repo.

## Inputs

- **Code repo URL** (from `constitution.md`'s Evidence section, surfaced during INTERVIEW Beat 2). May be absent if the paper has no public code and the user didn't supply a private one.
- **Paper substrate** at `work/reference/{paper.pdf, source/ or document.md, index.json, astra.yaml, figures/, tables/}` — produced by `/paper-extraction` during INTERVIEW. Read-only from ACQUIRE's perspective; iterations consult it, ACQUIRE doesn't modify it.

## Outputs

All on-disk:

- `work/reference/code/` — cloned reference repo (absent if `code-status.yaml` records `found: false`)
- `work/reference/code-status.yaml` — record of where the code came from (or that it wasn't found)
- `work/reference/code-index.md` — script inventory, candidate decisions, dependencies, container hints (absent when no code substrate)

## Step 1 — Locate, clone, scan

1. **Locate the reference code repository.**
   - If a URL was supplied at INTERVIEW (recorded in `constitution.md`'s Evidence section), use it.
   - Otherwise, the paper has no public code repo and the user didn't supply a private one — go to Step 1.4 and record `found: false`.

2. **Clone if found:**
   ```bash
   git clone --depth 1 <url> work/reference/code
   ```

   For multi-project monorepos where the user pointed at specific subpaths (e.g. GitHub `tree/<branch>/<path>` URLs), clone the whole repo on the named branch — don't sparse-checkout — and capture the primary subpaths in `code-status.yaml` so `/lc-from-code` knows where to focus.

3. **If `work/reference/code/` exists, run `/lc-from-code` in scan-only mode against it:**
   - Invoke `/lc-from-code` pointing at the cloned repo, with an invocation prompt that names the primary subpaths from `code-status.yaml` (if any) and reminds it of the scan-only contract: write `work/reference/code-index.md` only; do not touch `astra.yaml` at the project root; do not parameterize any code; do not run anything; do not modify the cloned repo.
   - The scan-only branch of `/lc-from-code` does the inventory pass and writes to `work/reference/code-index.md`.

4. **Write `work/reference/code-status.yaml`:**
   ```yaml
   found: true        # or false
   url: "https://..."  # null if not found
   branch: "main"     # or whichever branch was cloned; null if not found
   cloned: true       # false if found but clone failed
   primary_subpaths:  # optional; for multi-project monorepos
     - "notebooks/..."
     - "..."
   notes: "..."
   ```

`/lc-from-code`'s scan-only branch is the canonical code-inventory mechanism. Its prompt-context surface is what carries the "stop at scan" contract.

**A scan-only return is not an ACQUIRE stopping point.** ACQUIRE is incomplete until Step 2 below has either succeeded or hit a concrete launcher blocker. When `/lc-from-code` returns, do not summarize the scan as the final user-facing result. Continue immediately to Step 2: commit the code substrate, launch the ralph loop, and tell the user the session name.

## Step 2 — Commit and launch the ralph loop

1. **Commit the code substrate.** Stage `code-status.yaml` + `code-index.md` and commit — small, descriptive ("acquire: code substrate (sp_validation @ develop)"). The `work/reference/code/` clone itself can be `.gitignore`d or committed depending on the project's preference; the inventory file (`code-index.md`) is what downstream iterations actually consult, and gitignoring keeps the workdir tracked-size small for a 50+ MB monorepo clone.

2. **Tell the user** the ralph loop is about to launch. Surface anything notable from Step 1 — if `code-status.yaml` records `found: false` or the cloned repo is gnarly (no `requirements.txt`, abandoned-looking, etc.), mention it now so the user can adjust scope before iterations start working against the substrate.

3. **Launch the loop** (per SKILL.md's *Launching the loop* section):
   ```bash
   .claude/skills/ralph/scripts/ralph constitution.md
   ```
   Tell the user the tmux session name and the attach command. Iterations start firing immediately.

## Survey signals (entry into ACQUIRE)

Run `ls work/reference/` first.

- `work/reference/code/` present, **or** `code-status.yaml` records `found: false`, **and** `code-index.md` is present → ACQUIRE is done. Commit any unstaged changes and launch the loop.
- Otherwise, run Step 1.

If the paper substrate (`paper.pdf`, `index.json`, etc.) is missing, INTERVIEW didn't complete cleanly — re-invoke `/paper-extraction` against the partial state (idempotent; skips done work) and confirm the constitution + CLAUDE.md are consistent with what's on disk, before continuing.

## Notes

- **lc-from-code is the code-inventory authority** for the scan portion. ACQUIRE's invocation constrains it to scan-only via the prompt; the parameterization and run portions of `/lc-from-code` are not invoked at this phase.
- **The cloned code is read-only reference.** Iterations may re-read it; nothing modifies `work/reference/code/`. (When the reproduction's implementation needs to happen, that's an IMPLEMENT-phase decision, not an ACQUIRE one.)
- **Code-as-canonical** is loaded by every iteration via `CLAUDE.md`'s Rules. ACQUIRE just stands up the reference so the rule has something to point at.
- **This phase is acquisition, not understanding.** ACQUIRE doesn't write `astra.yaml` at the project root and doesn't compare paper to code. ARCHITECT does that, in the first ralph iteration after the loop launches.
- **No reference code is still a valid ACQUIRE outcome.** When `code-status.yaml` records `found: false`, iterations operate in paper-only mode — methodology lives in the paper's prose; no code-as-canonical adjudication is needed. CLAUDE.md's code-as-canonical Rule self-disables in that case.
- **Surface anti-patterns from the scan.** If `code-status.yaml` reports the clone failed or the repo is clearly dead, surface to the user immediately rather than launching a loop against half-acquired substrate.

## Future substrate types

ACQUIRE's purpose is "stand up reference substrate that wasn't surfaced in INTERVIEW." Today, that's just the code. If a future paper requires substrate types that aren't paper-or-code (a specific dataset to fetch from an open archive, supplementary materials, calibration files), they fit naturally as Step 1.5 in ACQUIRE — produced before commit + launch, with a status file recording what was acquired. Don't accrete those into INTERVIEW (which is about conversation) or into the ralph loop (which is about iteration over committed substrate).
