# /lc-feedback

File a GitHub issue against the right Lightcone repo from inside a
session, with version info and error context auto-collected. Be fast —
the user is in the middle of work.

Source: [`claude/lightcone/skills/lc-feedback/SKILL.md`](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/lc-feedback/SKILL.md).

Argument hint: `<what went wrong>`.

## Allowed tools

```
Bash(gh:*), Bash(python:*), Bash(uname:*), AskUserQuestion
```

Read-only on the project. Files only land on GitHub via `gh issue
create`.

## Workflow

1. **`gh auth status`** silently. If not authenticated, tell the user
   to run `gh auth login` and stop.
2. **Get the description** — typically inline (`/lc-feedback pipeline
   dies on second output`); if not, ask once.
3. **Triage the repo** from session context:
   - **ASTRA** — the `astra` CLI, schema validation, YAML parsing,
     helpers.
   - **lightcone-cli** — the `lc` CLI, recipes, container builds,
     scaffolding, skills, the engine layer.
   - Default to `lightcone-cli` if ambiguous.
4. **Collect environment** silently:
   ```bash
   python3 -c "import astra; print(astra.__version__)" 2>/dev/null || echo "n/a"
   python3 -c "import lightcone.cli; print(lightcone.cli.__version__)" 2>/dev/null || echo "n/a"
   python3 --version 2>&1
   uname -s -r
   ```
5. **Confirm** via `AskUserQuestion`: show target repo, title, body.
   Options: "File it" / "Let me edit".
6. **File**:
   ```bash
   gh issue create --repo LightconeResearch/<REPO> \
     --title "<TITLE>" --label "beta-feedback" --body "<BODY>"
   ```
   If the `beta-feedback` label doesn't exist, retry without `--label`.
   Print the issue URL.

## Issue body template

```
## What happened
[1–3 sentences combining user description + session context]

## Error
[Trimmed error/traceback from session, if any]

## Reproduction
[Brief steps from session context]

## Environment
- ASTRA: [version]
- lightcone-cli: [version]
- Python: [version]
- OS: [os]
```

Sections that don't apply are dropped.

## Hard rules

- Be fast — minimize back-and-forth, one confirmation then file.
- Read-only on the project.
- Trim aggressively — only the relevant portion of errors.
- No sensitive data — strip absolute paths, credentials, tokens.
- Don't editorialize — report what happened.

## Notes for the maintainer who's looking

The triage hint in the prompt currently says "lightcone-cli — `lc` CLI,
**Dagster execution**, recipes, container builds, scaffolding, skills."
That's stale — the Dagster mention should be replaced with
"Snakemake/Dask execution." See the `SKILL.md` source.
