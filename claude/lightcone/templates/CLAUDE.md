# CLAUDE.md

ASTRA analysis project, orchestrated by lightcone-cli.

**Source of truth:**
- `astra.yaml` — the analysis specification
- `.claude/guides/astra-reference.md` — astra.yaml spec syntax
- `.claude/guides/lightcone-cli-reference.md` — `lc` CLI commands, workflow, status, failures

### Quick Start

```bash
lc status                 # what's done, stale, or missing
lc verify                 # check provenance integrity
```

### Keep astra.yaml and code in sync

`astra.yaml` and the code must never diverge. When you change one, update the other in the same edit and run `astra validate astra.yaml`. See `lightcone-cli-reference.md` → "Spec-Code Invariant" for the full rules.

---

## Project Notes

<!-- Add context that doesn't belong in astra.yaml: domain background, open questions, design decisions, blockers — anything you'd want on a cold resume. -->
