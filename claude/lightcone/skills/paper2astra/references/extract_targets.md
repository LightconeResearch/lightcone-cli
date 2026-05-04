# EXTRACT_TARGETS — pick the replication targets

Take the results inventory from SUMMARIZE and select the concrete figures, tables, and metrics the reproduction will iterate against. Build a self-contained `targets/` directory the COMPARE phase will measure against.

The constitution's per-phase mode is **user choice** for this phase — defaults to sub-agent. The selection of replication targets is sometimes obvious (paper has 3 primary figures) and sometimes wants user input (which sub-analyses are in scope).

## Inputs

- `work/notes/methodology.md` — has the results inventory split into primary / secondary
- `work/reference/metadata.json` — index of figures and tables with captions
- `work/reference/figures/`, `work/reference/tables/` — the actual extracted artifacts

## Outputs

- `targets/targets.md` — the target ledger
- `targets/<file>` — copies of selected reference files (figures, tables) so `targets/` is self-contained

## Step 1: Read the results inventory

Read `work/notes/methodology.md`. The results inventory section already separates primary from secondary results and notes which decisions feed into each. **Use this as your starting point** — do not re-analyze the paper from scratch.

## Step 2: Select replication targets

For each result in the inventory, find the corresponding figure, table, or in-text metric in `work/reference/`. Apply the constitution's scope:

- **Primary results should almost always be included.** The constitution's Desired State names them.
- **Secondary results** should be included only if they are useful checkpoints along the pipeline (i.e., if getting them right helps verify intermediate steps).
- **Targeted reproduction** (per the constitution): include only the targets in scope. Mark out-of-scope primary results in `targets.md` with a reason.

## Step 3: Populate `targets/`

The `targets/` directory is the self-contained reference set the COMPARE phase consumes.

1. **Copy relevant reference files** from `work/reference/figures/` and `work/reference/tables/` into `targets/`. Only copy the files corresponding to selected targets — not everything.

2. **Write `targets/targets.md`.** For each target, a brief entry:

   - What it is and where its reference file lives in `targets/`
   - Expected values / trends and how to judge if a reproduction matches
   - Which decisions from the decision map feed into this result
   - Whether reference code covers this computation (from `code-analysis.md` if present)
   - Priority: `primary` or `secondary`

   Keep entries brief — a few lines per target, not paragraphs.

## Rules

- All paths in `targets/targets.md` are relative to `targets/`.
- For figures: describe scientific content, not just "a plot" — name the panels, the axis ranges, the qualitative shape.
- For tables: note which specific values matter most.
- For metrics: quote the exact value from the paper text (with the section / equation / sentence reference).

## Survey signals (entry into EXTRACT_TARGETS)

- `work/notes/methodology.md` exists ⇒ ready to extract targets
- `targets/targets.md` exists and reference files have been copied ⇒ EXTRACT_TARGETS done

## Notes

- **Targets are coverage obligations, not the spec.** SPECIFY maps each target to its appropriate ASTRA home — outputs for artifacts, findings for claims, inputs / decisions / universe defaults for constants. EXTRACT_TARGETS' job is the ledger; SPECIFY's job is the structural placement.
- **Out-of-scope targets stay in `targets.md`** with an explicit reason, not silently dropped. The constitution's scope is the source of truth for what's in.
