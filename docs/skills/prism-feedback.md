# /prism-feedback

File a bug report on GitHub.

## Purpose

`/prism-feedback` is a fast, three-step workflow for filing issues on the ASTRA or Prism repositories from within a Claude Code session.

## Workflow

1. **Auth check** — Verifies `gh auth status`. If not authenticated, prompts `gh auth login`.
2. **Triage** — Determines whether the issue is about the ASTRA spec (`astra-tools`) or execution (`Prism`), and selects the correct repository.
3. **File** — Opens a pre-filled GitHub issue with title, description, reproduction steps, and relevant context (error messages, `prism status` output, etc.).

## Key rules

- **Speed over perfection** — file a minimal, accurate report rather than a comprehensive one.
- Trim sensitive data (credentials, personal paths) before including logs.
- Attach relevant context: error messages, `astra validate` output, the failing `prism run` command.

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
- Prism version: {prism --version}
- Backend: {target backend}
- Error: {truncated error message}
```
