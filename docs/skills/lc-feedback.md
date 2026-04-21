# /lc-feedback

File a bug report on GitHub.

## Purpose

`/lc-feedback` is a fast, three-step workflow for filing issues on the ASTRA or lightcone-cli repositories from within a Claude Code session.

## Workflow

1. **Auth check** — Verifies `gh auth status`. If not authenticated, prompts `gh auth login`.
2. **Triage** — Determines whether the issue is about the ASTRA spec (`astra-tools`) or execution (`lightcone-cli`), and selects the correct repository.
3. **File** — Opens a pre-filled GitHub issue with title, description, reproduction steps, and relevant context (error messages, `lc status` output, etc.).

## Key rules

- **Speed over perfection** — file a minimal, accurate report rather than a comprehensive one.
- Trim sensitive data (credentials, personal paths) before including logs.
- Attach relevant context: error messages, `astra validate` output, the failing `lc run` command.

## Issue format

```markdown
## Problem
{one-line description}

## Reproduction
1. {step}
2. {step}

## Expected
{what should happen}

## Actual
{what happened}

## Context
- lightcone-cli version: {lc --version}
- Backend: {target backend}
- Error: {truncated error message}
```
