# CLAUDE.md

ASTRA analysis project, orchestrated by lightcone-cli.

The single source of truth for this analysis is `astra.yaml`. Spec syntax and CLI workflow live in the `/astra` and `/lc-cli` reference skills (named in the session-start primer; invoke when you need depth).

### Quick Start

```bash
lc status                 # what's done, stale, or missing
lc verify                 # check provenance integrity
```

### Keep astra.yaml and code in sync

`astra.yaml` and the code must never diverge. When you change one, update the other in the same edit and run `astra validate astra.yaml`.

### Report

`index.md` + `myst.yml` are a template MyST report wired to the MySTRA
plugin. The report references analysis elements by path — inline mentions
with the `{astra}` role, block embeds with the `{astra}` directive, live
numbers with `{astra:value}` — so never hard-type a measured value in the
prose. The `/lc-report` skill drafts and extends it. Preview with
`myst start` (requires the MyST CLI, `npm i -g mystmd`).

---

## Project Notes

<!-- Add context that doesn't belong in astra.yaml: domain background, open questions, design decisions, blockers — anything you'd want on a cold resume. -->
