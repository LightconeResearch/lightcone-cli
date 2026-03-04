---
name: prism-feedback
description: >
  File a bug report from the current session. Use when something breaks:
  /prism-feedback <description of what went wrong>
allowed-tools: Bash(gh:*), Bash(python:*), Bash(uname:*), AskUserQuestion
argument-hint: "<what went wrong>"
---

# /prism-feedback

File a bug report against the right Lightcone repo based on the current session.

Be fast. The user is in the middle of work and wants to get back to it.

## Prerequisites

Run `gh auth status` silently. If it fails:

> GitHub CLI is not authenticated. Run `gh auth login`, then try again.

Stop.

---

## Step 1: Description

The user should have provided a description inline (e.g., `/prism-feedback pipeline dies on second output`). If they didn't, ask briefly:

**What went wrong?**

---

## Step 2: Draft and Confirm

Triage the repo from context:
- **ASTRA** — `astra` CLI, schema validation, YAML parsing, helpers
- **Prism** — `prism` CLI, Dagster execution, recipes, container builds, scaffolding, skills, telemetry hooks

Default to **Prism** if ambiguous.

Collect versions silently:

```bash
python3 -c "import astra; print(astra.__version__)" 2>/dev/null || echo "n/a"
python3 -c "import prism; print(prism.__version__)" 2>/dev/null || echo "n/a"
python3 --version 2>&1
uname -s -r
```

Show the user a single confirmation message with the target repo, title, and body. Use `AskUserQuestion` with options "File it" / "Let me edit". The issue body format:

```
## What happened

[1-3 sentences combining user description + session context]

## Error

[Trimmed error/traceback from session, if any]

## Reproduction

[Brief steps from session context]

## Environment

- ASTRA: [version]
- Prism: [version]
- Python: [version]
- OS: [os]
```

Omit sections that don't apply (e.g., no Error section if there was no traceback).

---

## Step 3: File

```bash
gh issue create --repo LightconeResearch/<REPO> --title "<TITLE>" --label "beta-feedback" --body "<BODY>"
```

If it fails due to the label not existing, retry without `--label`. Print the issue URL.

---

## Rules

- **Be fast** — minimize back-and-forth, one confirmation then file
- **Read-only** — never modify project files
- **Trim aggressively** — only the relevant portion of errors, not full files
- **No sensitive data** — strip absolute paths, credentials, tokens
- **Don't editorialize** — report what happened, don't speculate on fixes
