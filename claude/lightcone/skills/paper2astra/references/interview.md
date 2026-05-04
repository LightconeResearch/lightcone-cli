# Interview — drafting the per-paper reproduction constitution

The interview is the only phase paper2astra runs interactively. It happens once per project, up front, before any ralph loop is launched. Its job is to crystallize what the user actually wants — which paper, what scope, which seams want their attention, which they want delegated — and bake that into a constitution the ralph loop can drive.

Use the [`/constitution`](../../constitution/SKILL.md) skill to draft. The interview's job is to *gather* the inputs the constitution needs; the constitution skill carries the discipline of writing it.

---

## What the interview produces

A single markdown file at the project root — by convention `paper2astra-constitution.md` (or whatever name the user prefers). Its YAML frontmatter has `status: open`. Its body has the standard constitution sections: Desired State, Context, Skills, Evidence, Open Questions — populated for *this specific paper*.

After the interview, paper2astra hands this file to ralph:

```bash
../ralph-loops/scripts/ralph paper2astra-constitution.md
```

The constitution is the durable artifact; the interview's work product *is* the constitution. There is no separate "interview state" file.

---

## The four jobs

### 1. Identify the paper

Use `AskUserQuestion` if the user did not supply enough on `/paper2astra` invocation:

- **DOI or arXiv ID.** arXiv ID preferred when available — it unlocks the LaTeX-source acquisition path (see ACQUIRE).
- **Code repo URL** if the user knows it. (If not, ACQUIRE will search.)
- **User's prior familiarity.** Has the user reproduced this paper before? Read the paper recently? Worked with the original authors? This affects how much of the SUMMARIZE / EXTRACT_TARGETS work needs human ratification.
- **Notes file.** If the user has any prior notes (their own writeup, a sketch of which figures matter), capture the path; SUMMARIZE will read it.

### 2. Scope the reproduction

A paper has many figures, tables, and numbers. The user usually does not want all of them.

Ask:

- **Full reproduction or targeted?** Full = every primary result the paper reports. Targeted = "I only care about figures 3, 4, 7 and the headline number in Table 2." Targeted is cheaper and produces a tighter astra.yaml.
- **Specific decisions of interest.** A paper makes many choices. The user may care most about a few — e.g. "I want the BAO fit to use a different damping prior than the paper." These become first-class decisions in the spec, with the alternative preserved as a sibling option.
- **Sub-analysis structure.** Does the paper have genuinely independent stages (e.g. reconstruction → clustering → BAO fit)? If so, the spec wants sub-analyses; SPECIFY will mirror the structure. If the paper is monolithic, one analysis suffices.

These answers live in the constitution's **Desired State** section.

### 3. Choose interactive vs sub-agent per phase

Read the "Per-phase mode" table in `../SKILL.md`. The defaults are reasonable. Walk the user through it briefly:

- **Phases that are always interactive (defaults you should not flip):** SPECIFY, COMPARE. These are the ratification seams; the user has to be reachable.
- **Phases that are always sub-agent (defaults you should not flip):** SUMMARIZE, LITERATURE. These benefit from parallel fresh-context runs.
- **Phases the user chooses:** ACQUIRE, PARSE, EXTRACT_TARGETS, REVIEW, IMPLEMENT, RUN. These default to sub-agent (mostly mechanical) but may want user attention if the paper is unfamiliar or the user has strong opinions about implementation.

If the user has no opinion, take the defaults. The choice goes into the constitution's **Context** section as a per-phase mode table.

### 4. Draft the constitution

Invoke `/constitution`. Pass in:

- The paper identity (DOI, arXiv ID, code URL)
- The scope (full vs targeted, sub-analysis structure if known)
- The per-phase mode table
- Any prior context the user has shared

The constitution skill carries the discipline of section voice (pointers, not snapshots; constitution, not plan; constraints with reasons). The constitution it produces will look approximately like:

```markdown
---
status: open
---

# Reproduce <paper title> (<arXiv ID>)

## Desired State

A complete `astra.yaml` for <paper> at this workdir, with recipes that
produce reproduced versions of <list of targets>, validated by
`astra validate astra.yaml --verify-evidence`, with `comparison-report.yaml`
verdict `pass` against the targets in `targets/targets.md`.

Non-goals: <e.g., reproducing Figure 12's MCMC stack — out of scope
because compute too large for available targets>.

## Context

- Paper DOI: <doi>
- arXiv ID: <id>; LaTeX source acquisition path is the primary
- Code repo: <url> (or "to be searched in ACQUIRE")
- Workdir layout: standard Paper2ASTRA conventions —
  `work/reference/`, `work/notes/`, `targets/`, `astra.yaml`,
  `universes/`, `results/`
- Per-phase mode:
  | Phase | Mode |
  |---|---|
  | ACQUIRE | sub-agent |
  | PARSE | sub-agent |
  | SUMMARIZE | sub-agent |
  | EXTRACT_TARGETS | <per user> |
  | LITERATURE | sub-agent |
  | SPECIFY | interactive |
  | REVIEW | <per user> |
  | IMPLEMENT | <per user> |
  | RUN | <per user> |
  | COMPARE | interactive |
  | SUMMARIZE_RUN | sub-agent |

## Skills

- `/paper2astra` — this skill (the orchestrator)
- `/managing-bibliography` — ACQUIRE
- `/narrative` — SPECIFY
- `/check-sentence-by-sentence`, `/figure-comparison` — COMPARE

## Evidence

- `ls work/reference/document.md` — ACQUIRE + PARSE done
- `ls work/notes/methodology.md` — SUMMARIZE done
- `ls targets/targets.md` — EXTRACT_TARGETS done
- `ls astra.yaml && astra validate astra.yaml` — SPECIFY done and valid
- `astra validate astra.yaml --verify-evidence` — evidence quotes match source PDFs
- `ls comparison-report.yaml && yq '.verdict' comparison-report.yaml` — most-recent COMPARE verdict
- `git log --oneline` — chronological view of phase commits

The COMPARE → IMPLEMENT loop iterates until verdict is `pass` or
attempt budget (default 5) is exhausted.

## Open Questions

(empty — populated as the loop runs and surfaces material conflicts
the user must ratify)
```

Show the draft, take corrections, refine. When the user is happy:

- Save the constitution at the project root
- Tell the user how to launch the loop: `../ralph-loops/scripts/ralph paper2astra-constitution.md`
- Optionally launch it for them if they say yes

The interview ends here. Subsequent work happens inside ralph iterations.

---

## Discipline

- **The interview is short.** Do not turn it into a full paper-summarization session. The user does not need to teach you the paper — they need to tell you what they want reproduced. Three to five `AskUserQuestion` rounds, total. If the user is grinding through detail, gently steer back to scope.
- **The constitution is the work product.** Do not file separate "interview notes" or "scope document" files. Everything goes into the constitution.
- **The defaults are the path.** When the user says "I don't know, you choose," take the defaults from the per-phase mode table. The defaults reflect what the loops have learned about which seams matter.
- **One paper at a time.** A single constitution covers one paper. If the user wants two, run the interview twice — two constitutions, two ralph loops, two project workdirs.

---

## When the interview gets stuck

Most failure modes resolve into "the user has not yet decided what 'reproduce' means for them." If the conversation is circling, ask one of these directly:

- *"If we ran this and it produced figure 3 plus the headline number in Table 2, would you be done?"* — pins targeted vs full.
- *"Is there a specific decision in the paper you want to vary, or are we trying to match the paper exactly?"* — pins whether universes need to span alternatives.
- *"Do you want to look at every paper-vs-code conflict, or just the ones I think are material?"* — pins SPECIFY mode.

When all three answer cleanly, the constitution writes itself.
