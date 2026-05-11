# ACQUIRE — spawn paper-expert and code-expert

The orchestrator dispatches two named, persistent sub-agents in parallel: **paper-expert** (which runs `/paper-extraction` to stand up the paper's reading materials) and **code-expert** (which locates and clones the reference code repo, then runs `/lc-from-code` in scan-only mode against it). Their transcripts persist and become the experts ARCHITECT consults via `SendMessage` as it writes the `astra.yaml` stub.

## Where this runs

The orchestrator session, directly. There is no `acquire` sub-agent — ACQUIRE's work is two parallel spawns and a wait. The orchestrator captures both agent IDs on return; those IDs are how ARCHITECT reaches the experts.

## Inputs

- The paper's DOI or arXiv ID (from CLAUDE.md's Paper section)
- An optional code repo URL (from the interview, if the user knew it; recorded in CLAUDE.md)

## Outputs

Two persistent named sub-agents (paper-expert, code-expert), each reachable via `SendMessage` by ID. On disk:

- `work/reference/index.json` — paper-side structural index (figures, tables, outline, citations with resolved DOIs)
- `work/reference/astra.yaml` — paper-extraction's ASTRA-shape stub of the paper (id, name, narrative.summary, optionally findings)
- `work/reference/paper.pdf` and either `work/reference/paper.tex` + `source/` (Path A) or `work/reference/document.md` (Path B)
- `work/reference/figures/`, `work/reference/tables/`, `work/reference/bibliography-source.{bib,bbl}`
- `work/reference/code/` — cloned reference repo (absent if not found)
- `work/reference/code-status.yaml` — record of where the code came from
- `work/reference/code-index.md` — code-expert's scan output: script inventory, candidate decisions, dependencies, container hints

## Step 1 — Spawn paper-expert

```
Agent(
  name="paper-expert",
  prompt="/paper-extraction <doi-or-arxiv-id>",
  run_in_background=True,
)
```

paper-expert runs the full `/paper-extraction` workflow and stays alive after it finishes — its transcript holds the deep paper context that ARCHITECT and later phases consult. The skill is idempotent; re-invoking on a partially-populated `work/reference/` is safe.

Capture the returned agent ID.

## Step 2 — Spawn code-expert (in parallel)

code-expert is a single sub-agent that does *all* the code-side work for ACQUIRE: locate the repo URL, clone it, then run `/lc-from-code` in scan-only mode against the clone. The orchestrator spawns it with explicit instructions to stop at the scan — `/lc-from-code` normally continues into parameterization and execution; here we only want the inventory.

```
Agent(
  name="code-expert",
  prompt="""
    You are the code-expert for an lc-from-paper reproduction.

    Repo URL (from INTERVIEW): <url or 'unknown — find it'>
    Workdir: this directory.

    Your tasks for ACQUIRE:

    1. Locate the reference code repository.
       - If a URL was provided above, use it.
       - Otherwise, grep the paper materials in work/reference/ for repo URLs (abstract,
         intro, conclusion, footnotes, "Code Availability" / "Data Availability" sections).
         Path A: grep across work/reference/source/*.tex. Path B: grep work/reference/document.md.
         If still nothing, web-search: paper title + "github", Papers With Code, or the first
         author's GitHub profile. A few searches max — record failure and move on.

    2. Clone if found:
         git clone --depth 1 <url> work/reference/code

    3. Write work/reference/code-status.yaml:
         found: true        # or false
         url: "https://..."  # null if not found
         cloned: true       # false if found but clone failed
         notes: "..."

    4. If work/reference/code/ exists, run /lc-from-code in SCAN-ONLY mode against it:
       - Invoke /lc-from-code with the working directory at work/reference/code/.
       - Do ONLY Phase 1's scan (the Explore-subagent inventory pass).
       - Write the inventory to work/reference/code-index.md.
       - DO NOT touch astra.yaml at the project root.
       - DO NOT parameterize any code.
       - DO NOT run anything.
       - DO NOT modify the cloned repo.

    5. Stay alive after returning. ARCHITECT will SendMessage you with questions
       about the code as it writes the stub astra.yaml.

    Report back: paths produced, anything surprising, any structural caveats
    (no code found, broken clone, gnarly scan, etc.).
  """,
  run_in_background=True,
)
```

Capture the returned agent ID.

If paper-expert hasn't finished writing paper materials yet when code-expert needs to grep for a URL, code-expert can wait briefly or surface that it needs paper materials first. With a URL from INTERVIEW, code-expert is fully independent of paper-expert and runs truly in parallel.

## Step 3 — Hand off to ARCHITECT

When both sub-agents have returned, spawn the architect with both indices in its reading list and both expert agent IDs reachable. The architect's reference is [`architect.md`](architect.md); the spawn pattern lives there.

The handoff payload to architect's prompt:

```
- Paper-expert agent ID: <id>
- Code-expert agent ID:  <id>
- Read: work/reference/index.json, work/reference/astra.yaml, work/reference/code-index.md
- Ask the experts (via SendMessage by ID) anything that isn't in the indices.
```

## Survey signals (entry into ACQUIRE)

Run `ls work/reference/` first.

- `paper.pdf` + path indicator (`source/` for Path A, `document.md` for Path B) + `index.json` present → paper-expert's work is done (or paper-expert is still resumable; check whether the agent is still addressable, otherwise re-spawn against the existing materials — `/paper-extraction` is idempotent and will skip done work).
- `work/reference/code/` present, or `code-status.yaml` records `found: false`, **and** `code-index.md` is present → code-expert's work is done.
- When both indices are present and both expert agent IDs are recorded, ACQUIRE is complete; proceed to ARCHITECT.
- Otherwise, re-spawn whichever expert is missing. Both skills are survey-first and skip already-done work.

## Notes

- **paper-extraction is the substrate authority.** Don't re-fetch the LaTeX source, don't re-run Docling, don't re-parse the paper from inside ACQUIRE. If a substrate need surfaces — including mid-reproduction — fix it in `/paper-extraction`, not here. Bibliography resolution is paper-extraction's: cited-paper text and DOIs live inside `index.json#citations[key]`, not in a side file.
- **lc-from-code is the code-inventory authority** for the scan portion. ACQUIRE's code-expert prompt constrains it to scan-only; the parameterization and run portions of `/lc-from-code` are not invoked at this phase.
- **arXiv DOI form is `10.48550/arXiv.<id>`.** Useful when downstream tools want a DOI rather than an arXiv ID.
- **Equation numbers and section numbers must match the rendered paper.** When citing "eq. N" or "§N" downstream, find by content, not by a naïve count of TeX blocks or markdown headings. Path A: source preserves printed numbers in `\label{}`s. Path B: Docling preserves printed numbers.
- **This phase is acquisition + on-hand expertise, not understanding.** ACQUIRE doesn't write `astra.yaml` at the project root and doesn't compare paper to code. ARCHITECT does that work, with the experts on hand.
- **Code-as-canonical** is loaded by every subsequent sub-agent. The per-paper `CLAUDE.md` carries the rule; ACQUIRE just stands up the reference so the rule has something to point at.
- **The cloned code is read-only reference for the agents.** code-expert's scan reads it; ARCHITECT and later phases may have their experts re-read parts of it; nothing modifies `work/reference/code/`. (When the reproduction's implementation needs to happen later, that's an IMPLEMENT-phase decision, not an ACQUIRE one.)
- **Commit each artifact as it lands.** The orchestrator can commit paper materials when paper-expert returns, and the code clone + scan when code-expert returns — small, descriptive commits that make `git log` legible.
- **Surface anti-patterns the experts flag.** If code-expert reports the clone failed or the repo is clearly dead, or paper-expert reports the paper substrate is broken, surface to the user immediately rather than handing a half-acquired workdir to ARCHITECT.
