# Decision Guide

## Decision Prioritization

Evaluate every candidate decision before adding it to the spec.

**Flowchart:**
1. Does literature/domain knowledge clearly favor one option? --> Prefer that option as `default` and keep alternatives only if reviewer scrutiny is likely.
2. Are the options expected to give similar results? --> Include alternatives if they are still useful for robustness checks.
3. Neither clear? --> Include alternatives and record why uncertainty remains in `rationale`.

**Rationale guidance:** Document why options are included and what evidence/theory supports them, e.g.: `"Literature uses both 2.5 and 3 SD cutoffs with no consensus"`.

---

## Stability Test

A decision matters if changing it changes the conclusion. If all options give qualitatively similar results, the choice is cosmetic -- skip it.

---

## Not Decisions

Skip these: fixed requirements (constraints), implementation details (build phase), obvious best practices with no defensible alternative, purely cosmetic choices, **whether or not to include an output** (decisions control *how* something is computed, not *what* is produced -- outputs are fixed by the analysis structure).

---

## Scoping

A multiverse of trivial decisions is less informative than a focused one.

| Tier | Criterion |
|------|-----------|
| **1 -- Must vary** | Literature/domain knowledge suggests the choice matters. |
| **2 -- Should vary** | Impact uncertain but plausible. Include if feasible. |
| **3 -- Could vary** | Impact likely small. Defer unless core multiverse is manageable. |

---

## Constraint Patterns

Use constraints when decisions are not independent.

- **Incompatibility** (`incompatible_with` on option) -- two options cannot coexist in a universe.
- **Requirement** (`requires` on option) -- selecting one option forces another.
